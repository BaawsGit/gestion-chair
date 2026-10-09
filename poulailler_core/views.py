from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .finance import batch_financials, sync_batch_chick_expense
from .forms import (
    BatchForm,
    DailyEventForm,
    DailyMedicationForm,
    ExpenseForm,
    FeedInventoryForm,
    ProphylaxisProgramForm,
    SaleForm,
    TreatmentLogForm,
)
from .models import (
    Batch,
    Expense,
    ExpenseCategory,
    FeedConsumption,
    FeedInventory,
    Mortality,
    ProphylaxisProgram,
    TreatmentLog,
    Sale,
)
from .signals import sync_batch_treatments


@login_required
def dashboard(request):
    today = timezone.localdate()
    all_batches = Batch.objects.order_by("-start_date", "-pk")
    batch_id = request.GET.get("batch")

    if batch_id:
        batch = all_batches.filter(pk=batch_id).first()
    else:
        batch = all_batches.filter(status=Batch.Status.ACTIVE).first() or all_batches.first()

    if batch is None:
        return render(request, "poulailler_core/dashboard.html", {
            "batch": None,
            "today": today,
            "all_batches": all_batches,
        })

    if batch.chick_cost > 0 and not Expense.objects.filter(batch=batch, category__name__iexact="Poussins").exists():
        sync_batch_chick_expense(batch)

    financials = batch_financials(batch)
    due_treatments = batch.treatments.filter(
        is_completed=False, date_planned__lte=today + timedelta(days=3)
    ).order_by("date_planned", "target_day")[:12]

    mortality_rows = list(
        batch.mortalities.values("date").annotate(total=Sum("count")).order_by("date")
    )
    expense_rows = list(
        Expense.objects.filter(batch=batch).values("category__name").annotate(total=Sum("amount")).order_by("category__name")
    )

    context = {
        "batch": batch,
        "all_batches": all_batches,
        "today": today,
        "financials": financials,
        "due_treatments": due_treatments,
        "recent_mortality": batch.mortalities.select_related("batch")[:6],
        "mortality_labels": [row["date"].strftime("%d/%m") for row in mortality_rows],
        "mortality_values": [row["total"] for row in mortality_rows],
        "expense_labels": [row["category__name"] for row in expense_rows],
        "expense_values": [float(row["total"]) for row in expense_rows],
        "expenses_total": sum((row["total"] for row in expense_rows), Decimal("0.00")),
        "feed_stock": FeedInventory.stock_summary(),
    }
    return render(request, "poulailler_core/dashboard.html", context)


@login_required
def daily_entry(request):
    batch = Batch.objects.filter(status=Batch.Status.ACTIVE).order_by("-start_date").first()
    if batch is None:
        messages.warning(request, "Créez d'abord un lot actif.")
        return redirect("batch-create")

    # Determine target date for edition or new entry
    target_date_str = request.POST.get("date") if request.method == "POST" else request.GET.get("date")
    if target_date_str:
        try:
            target_date = datetime.strptime(str(target_date_str).strip(), "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    existing_mortality = Mortality.objects.filter(batch=batch, date=target_date).first()
    existing_feed = FeedConsumption.objects.filter(batch=batch, date=target_date).first()
    administered_treatments = TreatmentLog.objects.filter(batch=batch, date_administered=target_date)

    is_editing = bool(existing_mortality or existing_feed or administered_treatments.exists())

    if request.method == "POST":
        form = DailyEventForm(request.POST, batch=batch, target_date=target_date)
        if form.is_valid():
            data = form.cleaned_data
            entry_date = data["date"]
            with transaction.atomic():
                # 1. Mortality (Update or Create or Delete if 0)
                count = data["mortality_count"] or 0
                if count > 0:
                    Mortality.objects.update_or_create(
                        batch=batch,
                        date=entry_date,
                        defaults={
                            "count": count,
                            "cause_suspected": data["cause_suspected"] or Mortality.Cause.UNKNOWN,
                            "notes": data["mortality_notes"],
                        },
                    )
                else:
                    Mortality.objects.filter(batch=batch, date=entry_date).delete()

                # 2. Feed Consumption (Update or Create or Delete)
                if data["feed_type"]:
                    FeedConsumption.objects.update_or_create(
                        batch=batch,
                        date=entry_date,
                        defaults={
                            "feed_type": data["feed_type"],
                            "bags_consumed": data["bags_consumed"],
                            "kg_consumed": data["kg_consumed"],
                        },
                    )
                else:
                    FeedConsumption.objects.filter(batch=batch, date=entry_date).delete()

                # 3. Treatments
                selected_treatments = data["treatments"]
                # Unmark previous ones on this date not selected
                TreatmentLog.objects.filter(batch=batch, date_administered=entry_date).exclude(
                    pk__in=[t.pk for t in selected_treatments]
                ).update(is_completed=False, date_administered=None)

                # Mark selected
                for treatment in selected_treatments:
                    treatment.is_completed = True
                    treatment.date_administered = entry_date
                    treatment.save(update_fields=["is_completed", "date_administered"])

            if is_editing:
                messages.success(request, f"La saisie du {entry_date.strftime('%d/%m/%Y')} a été modifiée avec succès.")
            else:
                messages.success(request, f"La saisie du {entry_date.strftime('%d/%m/%Y')} a été enregistrée avec succès.")
            return redirect(f"/journal/?date={entry_date.isoformat()}")
    else:
        initial = {"date": target_date}
        if existing_mortality:
            initial["mortality_count"] = existing_mortality.count
            initial["cause_suspected"] = existing_mortality.cause_suspected
            initial["mortality_notes"] = existing_mortality.notes
        if existing_feed:
            initial["feed_type"] = existing_feed.feed_type
            initial["bags_consumed"] = existing_feed.bags_consumed
            initial["kg_consumed"] = existing_feed.kg_consumed
        if administered_treatments.exists():
            initial["treatments"] = list(administered_treatments.values_list("pk", flat=True))

        form = DailyEventForm(initial=initial, batch=batch, target_date=target_date)

    # Build history entries for active batch
    all_dates = set(Mortality.objects.filter(batch=batch).values_list("date", flat=True))
    all_dates.update(FeedConsumption.objects.filter(batch=batch).values_list("date", flat=True))
    all_dates.update(TreatmentLog.objects.filter(batch=batch, is_completed=True).values_list("date_administered", flat=True))

    history_entries = []
    for d in sorted([d for d in all_dates if d is not None], reverse=True):
        m = Mortality.objects.filter(batch=batch, date=d).first()
        f = FeedConsumption.objects.filter(batch=batch, date=d).first()
        t_list = list(TreatmentLog.objects.filter(batch=batch, date_administered=d))
        age_d = max((d - batch.start_date).days + 1, 0)
        history_entries.append({
            "date": d,
            "age_days": age_d,
            "mortality": m,
            "feed": f,
            "treatments": t_list,
            "is_selected": (d == target_date),
        })

    day_age = max((target_date - batch.start_date).days + 1, 0)

    return render(request, "poulailler_core/daily_entry.html", {
        "form": form,
        "batch": batch,
        "target_date": target_date,
        "day_age": day_age,
        "is_editing": is_editing,
        "history_entries": history_entries,
    })


@login_required
def daily_entry_delete(request, date_str):
    batch = Batch.objects.filter(status=Batch.Status.ACTIVE).order_by("-start_date").first()
    if not batch:
        return redirect("dashboard")
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return redirect("daily-entry")

    if request.method == "POST":
        with transaction.atomic():
            Mortality.objects.filter(batch=batch, date=target_date).delete()
            FeedConsumption.objects.filter(batch=batch, date=target_date).delete()
            TreatmentLog.objects.filter(batch=batch, date_administered=target_date).update(
                is_completed=False, date_administered=None
            )
        messages.success(request, f"La saisie du {target_date.strftime('%d/%m/%Y')} a été supprimée.")
        return redirect("daily-entry")

    return render(request, "poulailler_core/confirm_delete.html", {
        "title": f"Supprimer la saisie du {target_date.strftime('%d/%m/%Y')}",
        "object_name": f"Données de mortalité, alimentation et soins du {target_date.strftime('%d/%m/%Y')} pour le lot {batch.name}",
        "cancel_url": f"/journal/?date={date_str}",
    })


@login_required
def complete_treatment(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("Action POST requise.")
    treatment = get_object_or_404(TreatmentLog, pk=pk)
    treatment.is_completed = True
    treatment.date_administered = timezone.localdate()
    treatment.save(update_fields=["is_completed", "date_administered"])
    messages.success(request, f"Soin validé : {treatment.medicine_name} (J{treatment.target_day}).")
    redirect_target = request.POST.get("next") or request.META.get("HTTP_REFERER") or "dashboard"
    return redirect(redirect_target)


@login_required
def treatment_list(request):
    today = timezone.localdate()
    all_batches = Batch.objects.order_by("-start_date", "-pk")
    batch_id = request.GET.get("batch")

    if batch_id:
        batch = all_batches.filter(pk=batch_id).first()
    else:
        batch = all_batches.filter(status=Batch.Status.ACTIVE).first() or all_batches.first()

    programs = ProphylaxisProgram.objects.order_by("target_day", "name")

    if not batch:
        return render(request, "poulailler_core/treatments.html", {
            "batch": None,
            "all_batches": all_batches,
            "programs": programs,
            "program_count": programs.count(),
        })

    status_filter = request.GET.get("status", "all")
    treatments = batch.treatments.order_by("target_day", "date_planned", "pk")
    if status_filter == "pending":
        treatments = treatments.filter(is_completed=False)
    elif status_filter == "completed":
        treatments = treatments.filter(is_completed=True)

    return render(request, "poulailler_core/treatments.html", {
        "batch": batch,
        "all_batches": all_batches,
        "treatments": treatments,
        "status_filter": status_filter,
        "today": today,
        "programs": programs,
        "program_count": programs.count(),
    })


@login_required
def treatment_create(request):
    batch_id = request.GET.get("batch") or request.POST.get("batch")
    initial = {}
    if batch_id:
        batch = get_object_or_404(Batch, pk=batch_id)
        initial["batch"] = batch
        initial["target_day"] = batch.current_age_days
        initial["date_planned"] = timezone.localdate()
    else:
        active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
        if active_batch:
            initial["batch"] = active_batch
            initial["target_day"] = active_batch.current_age_days
            initial["date_planned"] = timezone.localdate()

    form = TreatmentLogForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        treatment = form.save()
        messages.success(request, f"Soin J{treatment.target_day} ({treatment.medicine_name}) ajouté avec succès au lot {treatment.batch.name}.")
        return redirect(f"/soins/?batch={treatment.batch.pk}")

    return render(request, "poulailler_core/form_page.html", {
        "form": form,
        "title": "Ajouter un soin / médicament au lot",
        "submit_label": "Enregistrer le soin",
    })


@login_required
def treatment_edit(request, pk):
    treatment = get_object_or_404(TreatmentLog, pk=pk)
    form = TreatmentLogForm(request.POST or None, instance=treatment)
    if request.method == "POST" and form.is_valid():
        treatment = form.save()
        messages.success(request, f"Soin J{treatment.target_day} ({treatment.medicine_name}) mis à jour.")
        return redirect(f"/soins/?batch={treatment.batch.pk}")

    return render(request, "poulailler_core/form_page.html", {
        "form": form,
        "title": f"Modifier le soin · J{treatment.target_day} {treatment.medicine_name}",
        "submit_label": "Mettre à jour le soin",
    })


@login_required
def treatment_delete(request, pk):
    treatment = get_object_or_404(TreatmentLog, pk=pk)
    batch_pk = treatment.batch.pk
    if request.method == "POST":
        medicine = treatment.medicine_name
        day = treatment.target_day
        treatment.delete()
        messages.success(request, f"Soin J{day} ({medicine}) supprimé du lot.")
        return redirect(f"/soins/?batch={batch_pk}")

    return render(request, "poulailler_core/confirm_delete.html", {
        "title": f"Supprimer le soin J{treatment.target_day} ({treatment.medicine_name})",
        "object_name": f"Soin J{treatment.target_day} - {treatment.medicine_name} du lot {treatment.batch.name}",
        "cancel_url": f"/soins/?batch={batch_pk}",
    })


@login_required
def treatment_sync_batch(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if request.method == "POST":
        sync_batch_treatments(batch)
        messages.success(request, f"Le calendrier du lot {batch.name} a été synchronisé avec le programme de prophylaxie.")
    return redirect(f"/soins/?batch={batch.pk}")


@login_required
def prophylaxis_program_list(request):
    """
    Day-by-Day 45-day medication calendar.
    Organizes all 45 days into a clean avicultural timeline.
    """
    programs_by_day = {}
    for p in ProphylaxisProgram.objects.filter(is_active=True).order_by("target_day", "pk"):
        programs_by_day.setdefault(p.target_day, []).append(p)

    calendar_days = []
    for day in range(1, 46):
        day_progs = programs_by_day.get(day, [])
        calendar_days.append({
            "day": day,
            "programs": day_progs,
            "has_med": len(day_progs) > 0,
            "phase": 1 if day <= 14 else (2 if day <= 28 else 3),
        })

    treated_days_count = sum(1 for d in calendar_days if d["has_med"])
    active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()

    return render(request, "poulailler_core/prophylaxis_programs.html", {
        "calendar_days": calendar_days,
        "treated_days_count": treated_days_count,
        "active_batch": active_batch,
    })


@login_required
def prophylaxis_program_create(request):
    """
    Set medication for a single day or range of days (e.g. J1 to J3 = Tetracolivit).
    """
    day_param = request.GET.get("day")
    initial = {}
    if day_param:
        try:
            d_val = int(day_param)
            initial["start_day"] = d_val
            initial["end_day"] = d_val
            existing = ProphylaxisProgram.objects.filter(target_day=d_val, is_active=True).first()
            if existing:
                initial["medicine_name"] = existing.medicine_name
                initial["dosage"] = existing.dosage
                initial["administration_route"] = existing.administration_route
                initial["notes"] = existing.notes
        except (ValueError, TypeError):
            pass

    form = DailyMedicationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        start = data["start_day"]
        end = data["end_day"] or start
        med = data["medicine_name"]
        route = data["administration_route"]
        dosage = data["dosage"]
        notes = data["notes"]

        with transaction.atomic():
            for d in range(start, end + 1):
                ProphylaxisProgram.objects.update_or_create(
                    target_day=d,
                    defaults={
                        "name": med,
                        "medicine_name": med,
                        "dosage": dosage,
                        "administration_route": route,
                        "duration_days": 1,
                        "frequency_days": 1,
                        "notes": notes,
                        "is_active": True,
                    },
                )
            active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
            if active_batch:
                sync_batch_treatments(active_batch)

        if start == end:
            messages.success(request, f"Jour J{start} configuré avec succès : {med}.")
        else:
            messages.success(request, f"Jours J{start} à J{end} configurés avec succès : {med}.")
        return redirect("prophylaxis-program-list")

    day_label = f"Jour J{initial.get('start_day')}" if initial.get("start_day") else "du 1er au 45ème jour"
    return render(request, "poulailler_core/form_page.html", {
        "form": form,
        "title": f"Planifier un médicament · {day_label}",
        "submit_label": "Enregistrer dans le programme (J1 à J45)",
    })


@login_required
def prophylaxis_program_edit(request, pk):
    program = get_object_or_404(ProphylaxisProgram, pk=pk)
    initial = {
        "start_day": program.target_day,
        "end_day": program.target_day,
        "medicine_name": program.medicine_name,
        "dosage": program.dosage,
        "administration_route": program.administration_route,
        "notes": program.notes,
    }
    form = DailyMedicationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        program.target_day = data["start_day"]
        program.name = data["medicine_name"]
        program.medicine_name = data["medicine_name"]
        program.dosage = data["dosage"]
        program.administration_route = data["administration_route"]
        program.notes = data["notes"]
        program.duration_days = 1
        program.frequency_days = 1
        program.save()

        active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
        if active_batch:
            sync_batch_treatments(active_batch)

        messages.success(request, f"Jour J{program.target_day} ({program.medicine_name}) mis à jour.")
        return redirect("prophylaxis-program-list")

    return render(request, "poulailler_core/form_page.html", {
        "form": form,
        "title": f"Modifier le médicament · Jour J{program.target_day}",
        "submit_label": "Mettre à jour ce jour",
    })


@login_required
def prophylaxis_program_duplicate(request, day):
    if day >= 45:
        messages.warning(request, "Impossible de dupliquer au-delà de J45.")
        return redirect("prophylaxis-program-list")

    source_progs = ProphylaxisProgram.objects.filter(target_day=day, is_active=True)
    if not source_progs.exists():
        messages.warning(request, f"Aucun médicament configuré au jour J{day} à dupliquer.")
        return redirect("prophylaxis-program-list")

    with transaction.atomic():
        ProphylaxisProgram.objects.filter(target_day=day + 1).delete()
        for p in source_progs:
            ProphylaxisProgram.objects.create(
                target_day=day + 1,
                name=p.name,
                medicine_name=p.medicine_name,
                dosage=p.dosage,
                administration_route=p.administration_route,
                duration_days=1,
                frequency_days=1,
                notes=p.notes,
                is_active=True,
            )
        active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
        if active_batch:
            sync_batch_treatments(active_batch)

    messages.success(request, f"Médicament du jour J{day} dupliqué à l'identique sur le jour J{day + 1}.")
    return redirect("prophylaxis-program-list")


@login_required
def prophylaxis_program_clear_day(request, day):
    if request.method == "POST":
        with transaction.atomic():
            ProphylaxisProgram.objects.filter(target_day=day).delete()
            active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
            if active_batch:
                TreatmentLog.objects.filter(batch=active_batch, target_day=day, is_completed=False).delete()
        messages.success(request, f"Le jour J{day} a été réinitialisé (aucun médicament / eau claire).")
        return redirect("prophylaxis-program-list")

    return render(request, "poulailler_core/confirm_delete.html", {
        "title": f"Effacer le médicament du jour J{day}",
        "object_name": f"Traitement prévu pour le jour J{day}",
        "cancel_url": "/prophylaxie/programme/",
    })


@login_required
def prophylaxis_program_delete(request, pk):
    program = get_object_or_404(ProphylaxisProgram, pk=pk)
    day = program.target_day
    name = program.name
    if request.method == "POST":
        with transaction.atomic():
            program.delete()
            active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
            if active_batch:
                TreatmentLog.objects.filter(batch=active_batch, target_day=day, is_completed=False).delete()
        messages.success(request, f"Protocole J{day} ({name}) supprimé du programme.")
        return redirect("prophylaxis-program-list")

    return render(request, "poulailler_core/confirm_delete.html", {
        "title": f"Supprimer le protocole J{program.target_day} ({program.name})",
        "object_name": f"Protocole J{program.target_day} - {program.name} ({program.medicine_name})",
        "cancel_url": "/prophylaxie/programme/",
    })


@login_required
def batch_create(request):
    form = BatchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        batch = form.save()
        sync_batch_chick_expense(batch)
        messages.success(request, f"Le lot {batch.name} a été créé ({batch.initial_quantity} poussins démarrés : {batch.ordered_quantity} commandés + {batch.bonus_quantity} bonus).")
        return redirect("dashboard")
    return render(request, "poulailler_core/batch_form.html", {
        "form": form,
        "title": "Nouveau lot de poussins",
        "submit_label": "Créer le lot",
        "is_edit": False,
    })


@login_required
def batch_edit(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    form = BatchForm(request.POST or None, instance=batch)
    if request.method == "POST" and form.is_valid():
        batch = form.save()
        sync_batch_chick_expense(batch)
        messages.success(request, f"Les paramètres du lot {batch.name} ont été mis à jour ({batch.initial_quantity} poussins démarrés : {batch.ordered_quantity} commandés + {batch.bonus_quantity} bonus).")
        return redirect("dashboard")
    return render(request, "poulailler_core/batch_form.html", {
        "form": form,
        "batch": batch,
        "title": f"Modifier le lot : {batch.name}",
        "submit_label": "Enregistrer les modifications",
        "is_edit": True,
    })


@login_required
def batch_history(request):
    batches = Batch.objects.order_by("-start_date", "-pk")
    return render(request, "poulailler_core/batches.html", {"batches": batches})


@login_required
def expense_list(request):
    batches = Batch.objects.order_by("-start_date", "-pk")
    for b in batches:
        if b.chick_cost > 0 and not Expense.objects.filter(batch=b, category__name__iexact="Poussins").exists():
            sync_batch_chick_expense(b)

    categories = ExpenseCategory.objects.all()
    batch_id = request.GET.get("batch")
    category_id = request.GET.get("category")

    expenses = Expense.objects.select_related("batch", "category").order_by("-date", "-pk")
    if batch_id:
        if batch_id == "general":
            expenses = expenses.filter(batch__isnull=True)
        else:
            expenses = expenses.filter(batch_id=batch_id)
    if category_id:
        expenses = expenses.filter(category_id=category_id)

    total_amount = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return render(request, "poulailler_core/expenses.html", {
        "expenses": expenses,
        "total_amount": total_amount,
        "batches": batches,
        "categories": categories,
        "selected_batch": batch_id,
        "selected_category": category_id,
    })


@login_required
def expense_create(request):
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Dépense enregistrée.")
        return redirect("expense-list")
    return render(request, "poulailler_core/form_page.html", {"form": form, "title": "Nouvelle dépense", "submit_label": "Enregistrer"})


@login_required
def feed_stock_view(request):
    summary = FeedInventory.stock_summary()
    purchases = FeedInventory.objects.order_by("-purchase_date", "-pk")[:25]
    consumptions = FeedConsumption.objects.select_related("batch").order_by("-date", "-pk")[:25]
    return render(request, "poulailler_core/stocks.html", {
        "feed_stock": summary,
        "purchases": purchases,
        "consumptions": consumptions,
    })


@login_required
def feed_purchase(request):
    form = FeedInventoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Entrée de stock enregistrée. N'oubliez pas d'enregistrer la dépense correspondante si besoin.")
        return redirect("feed-stock")
    return render(request, "poulailler_core/form_page.html", {"form": form, "title": "Réception d'aliment", "submit_label": "Ajouter au stock"})


@login_required
def sale_list(request):
    batches = Batch.objects.order_by("-start_date", "-pk")
    batch_id = request.GET.get("batch")
    sales = Sale.objects.select_related("batch").order_by("-date", "-pk")
    if batch_id:
        sales = sales.filter(batch_id=batch_id)

    aggregates = sales.aggregate(
        total_revenue=Sum("total_amount"),
        total_sold=Sum("quantity_sold"),
        total_weight=Sum("total_weight_kg"),
    )
    return render(request, "poulailler_core/sales.html", {
        "sales": sales,
        "batches": batches,
        "selected_batch": batch_id,
        "total_revenue": aggregates["total_revenue"] or Decimal("0.00"),
        "total_sold": aggregates["total_sold"] or 0,
        "total_weight": aggregates["total_weight"] or Decimal("0.00"),
    })


@login_required
def sale_create(request):
    form = SaleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Vente enregistrée.")
        return redirect("sale-list")
    return render(request, "poulailler_core/form_page.html", {"form": form, "title": "Nouvelle vente", "submit_label": "Enregistrer la vente"})


@login_required
def close_batch(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if request.method == "POST":
        if batch.status != Batch.Status.ACTIVE:
            messages.error(request, "Ce lot n'est plus actif.")
            return redirect("dashboard")
        batch.status = Batch.Status.CLOSED
        if not batch.end_date:
            batch.end_date = timezone.localdate()
        batch.save(update_fields=["status", "end_date"])
        messages.success(request, f"Le lot {batch.name} est clôturé. Son historique et bilan sont conservés.")
        return redirect("batch-report", pk=batch.pk)

    financials = batch_financials(batch)
    produced = max(batch.initial_quantity - batch.deaths_count, 1)
    unit_cost = (financials["total_costs"] / Decimal(produced)).quantize(Decimal("0.01"))
    margin_per_bird = (financials["net"] / Decimal(batch.sold_count)).quantize(Decimal("0.01")) if batch.sold_count > 0 else Decimal("0.00")

    return render(request, "poulailler_core/batch_report.html", {
        "batch": batch,
        "financials": financials,
        "unit_cost": unit_cost,
        "margin_per_bird": margin_per_bird,
        "close_confirmation": True,
    })


@login_required
def batch_report(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    financials = batch_financials(batch)
    produced = max(batch.initial_quantity - batch.deaths_count, 1)
    unit_cost = (financials["total_costs"] / Decimal(produced)).quantize(Decimal("0.01"))
    margin_per_bird = (financials["net"] / Decimal(batch.sold_count)).quantize(Decimal("0.01")) if batch.sold_count > 0 else Decimal("0.00")

    return render(request, "poulailler_core/batch_report.html", {
        "batch": batch,
        "financials": financials,
        "unit_cost": unit_cost,
        "margin_per_bird": margin_per_bird,
        "close_confirmation": False,
    })
