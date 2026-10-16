from functools import wraps
from datetime import datetime, timedelta
from decimal import Decimal
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .finance import (
    batch_financials,
    sync_all_feed_expenses,
    sync_batch_chick_expense,
    sync_feed_inventory_expense,
)
from .forms import (
    BatchForm,
    DailyEventForm,
    DailyMedicationForm,
    ExpenseForm,
    FeedFormulaForm,
    FeedInventoryForm,
    FeedPreparationForm,
    ProphylaxisProgramForm,
    SaleForm,
    TreatmentLogForm,
)
from .models import (
    Batch,
    Expense,
    ExpenseCategory,
    FeedConsumption,
    FeedFormula,
    FeedFormulaItem,
    FeedInventory,
    FeedPreparation,
    FeedPreparationItem,
    FeedType,
    Mortality,
    ProphylaxisProgram,
    TreatmentLog,
    Sale,
)
from .signals import sync_batch_treatments


def admin_required(view_func):
    """Restreint l'exécution de la vue aux administrateurs (is_staff ou is_superuser)."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            messages.warning(
                request,
                "Action non autorisée : votre compte est en mode consultation seule. Seul l'administrateur peut effectuer des opérations d'écriture."
            )
            referer = request.META.get("HTTP_REFERER")
            if referer:
                return redirect(referer)
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


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
    sync_all_feed_expenses()

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
        if request.user.is_staff or request.user.is_superuser:
            messages.warning(request, "Créez d'abord un lot actif.")
            return redirect("batch-create")
        messages.warning(request, "Aucun lot actif pour le moment.")
        return redirect("dashboard")

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
    existing_feeds = list(FeedConsumption.objects.filter(batch=batch, date=target_date).order_by("feed_type"))
    administered_treatments = TreatmentLog.objects.filter(batch=batch, date_administered=target_date)

    is_editing = bool(existing_mortality or existing_feeds or administered_treatments.exists())

    if request.method == "POST":
        if not (request.user.is_staff or request.user.is_superuser):
            messages.warning(
                request,
                "Action non autorisée : votre compte est en mode consultation seule. Seul l'administrateur peut enregistrer des données au journal."
            )
            return redirect("daily-entry")

        form = DailyEventForm(request.POST, batch=batch, target_date=target_date)
        if form.is_valid():
            data = form.cleaned_data
            entry_date = data["date"]

            stock_summary = FeedInventory.stock_summary()
            stock_warnings = []

            prior_consumptions_map = {
                fc.feed_type: fc for fc in FeedConsumption.objects.filter(batch=batch, date=entry_date)
            }

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

                # 2. Multi-Feed Consumption
                for ft, label in FeedType.choices:
                    kg_val = data.get(f"{ft}_kg")
                    bags_val = data.get(f"{ft}_bags")

                    prior_fc = prior_consumptions_map.get(ft)
                    prior_kg = Decimal("0.00")
                    if prior_fc:
                        if prior_fc.kg_consumed:
                            prior_kg += prior_fc.kg_consumed
                        elif prior_fc.bags_consumed:
                            prior_kg += prior_fc.bags_consumed * Decimal("50.00")

                    distributed_kg = Decimal("0.00")
                    if kg_val and kg_val > 0:
                        distributed_kg += Decimal(str(kg_val))
                    if bags_val and bags_val > 0:
                        distributed_kg += Decimal(str(bags_val)) * Decimal("50.00")

                    remaining_in_stock = stock_summary.get(ft, {}).get("remaining_kg", Decimal("0.00"))
                    available_for_entry = remaining_in_stock + prior_kg

                    if distributed_kg > 0:
                        # Stock shortage notification check
                        if distributed_kg > available_for_entry:
                            deficit = distributed_kg - available_for_entry
                            stock_warnings.append(
                                f"Alerte stock ({label}) : {distributed_kg:.1f} kg distribués alors que le stock disponible n'était que de {max(available_for_entry, Decimal('0.00')):.1f} kg (déficit de {deficit:.1f} kg). Pensez à enregistrer un réapprovisionnement."
                            )

                        if kg_val and kg_val > 0 and bags_val and bags_val > 0:
                            FeedConsumption.objects.update_or_create(
                                batch=batch,
                                date=entry_date,
                                feed_type=ft,
                                defaults={
                                    "bags_consumed": None,
                                    "kg_consumed": distributed_kg.quantize(Decimal("0.01")),
                                }
                            )
                        elif kg_val and kg_val > 0:
                            FeedConsumption.objects.update_or_create(
                                batch=batch,
                                date=entry_date,
                                feed_type=ft,
                                defaults={
                                    "bags_consumed": None,
                                    "kg_consumed": Decimal(str(kg_val)).quantize(Decimal("0.01")),
                                }
                            )
                        elif bags_val and bags_val > 0:
                            FeedConsumption.objects.update_or_create(
                                batch=batch,
                                date=entry_date,
                                feed_type=ft,
                                defaults={
                                    "bags_consumed": Decimal(str(bags_val)).quantize(Decimal("0.001")),
                                    "kg_consumed": None,
                                }
                            )
                    else:
                        if prior_fc:
                            prior_fc.delete()

                # 3. Treatments
                selected_treatments = data["treatments"]
                TreatmentLog.objects.filter(batch=batch, date_administered=entry_date).exclude(
                    pk__in=[t.pk for t in selected_treatments]
                ).update(is_completed=False, date_administered=None)

                for treatment in selected_treatments:
                    treatment.is_completed = True
                    treatment.date_administered = entry_date
                    treatment.save(update_fields=["is_completed", "date_administered"])

            for warn_msg in stock_warnings:
                messages.warning(request, f"⚠️ {warn_msg}")

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

        for ef in existing_feeds:
            if ef.feed_type == FeedType.STARTER:
                if ef.kg_consumed:
                    initial["starter_kg"] = ef.kg_consumed
                if ef.bags_consumed:
                    initial["starter_bags"] = ef.bags_consumed
            elif ef.feed_type == FeedType.GROWER:
                if ef.kg_consumed:
                    initial["grower_kg"] = ef.kg_consumed
                if ef.bags_consumed:
                    initial["grower_bags"] = ef.bags_consumed
            elif ef.feed_type == FeedType.FINISHER:
                if ef.kg_consumed:
                    initial["finisher_kg"] = ef.kg_consumed
                if ef.bags_consumed:
                    initial["finisher_bags"] = ef.bags_consumed

        if existing_feeds:
            f0 = existing_feeds[0]
            initial["feed_type"] = f0.feed_type
            initial["bags_consumed"] = f0.bags_consumed
            initial["kg_consumed"] = f0.kg_consumed

        if administered_treatments.exists():
            initial["treatments"] = list(administered_treatments.values_list("pk", flat=True))

        form = DailyEventForm(initial=initial, batch=batch, target_date=target_date)

    # Build stock summary per feed type for UI
    stock_summary = FeedInventory.stock_summary()
    feed_cards_data = []
    for ft, label in FeedType.choices:
        stock_item = stock_summary.get(ft, {"label": label, "remaining_kg": Decimal("0.00"), "remaining_bags": Decimal("0.00")})
        existing_for_ft = next((ef for ef in existing_feeds if ef.feed_type == ft), None)
        already_used = Decimal("0.00")
        if existing_for_ft:
            if existing_for_ft.kg_consumed:
                already_used += existing_for_ft.kg_consumed
            elif existing_for_ft.bags_consumed:
                already_used += existing_for_ft.bags_consumed * Decimal("50.00")

        effective_avail_kg = stock_item["remaining_kg"] + already_used
        feed_cards_data.append({
            "type": ft,
            "label": label,
            "remaining_kg": effective_avail_kg.quantize(Decimal("0.01")),
            "remaining_bags": stock_item["remaining_bags"],
            "field_kg": form[f"{ft}_kg"],
            "field_bags": form[f"{ft}_bags"],
        })

    # Build history entries for active batch
    all_dates = set(Mortality.objects.filter(batch=batch).values_list("date", flat=True))
    all_dates.update(FeedConsumption.objects.filter(batch=batch).values_list("date", flat=True))
    all_dates.update(TreatmentLog.objects.filter(batch=batch, is_completed=True).values_list("date_administered", flat=True))

    history_entries = []
    for d in sorted([d for d in all_dates if d is not None], reverse=True):
        m = Mortality.objects.filter(batch=batch, date=d).first()
        day_feeds = list(FeedConsumption.objects.filter(batch=batch, date=d).order_by("feed_type"))
        t_list = list(TreatmentLog.objects.filter(batch=batch, date_administered=d))
        age_d = max((d - batch.start_date).days + 1, 0)
        history_entries.append({
            "date": d,
            "age_days": age_d,
            "mortality": m,
            "feeds": day_feeds,
            "feed": day_feeds[0] if day_feeds else None,
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
        "feed_cards_data": feed_cards_data,
    })


@login_required
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
    sync_all_feed_expenses()

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
@admin_required
def expense_create(request):
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save()
        if hasattr(expense, "feed_inventory") and expense.feed_inventory:
            messages.success(request, f"Dépense enregistrée et stock d'aliment mis à jour ({expense.feed_inventory.get_feed_type_display()}).")
        else:
            messages.success(request, "Dépense enregistrée.")
        return redirect("expense-list")
    return render(request, "poulailler_core/expense_form.html", {
        "form": form,
        "title": "Nouvelle dépense",
        "submit_label": "Enregistrer la dépense",
    })


@login_required
@admin_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    form = ExpenseForm(request.POST or None, request.FILES or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        expense = form.save()
        if hasattr(expense, "feed_inventory") and expense.feed_inventory:
            messages.success(request, f"Dépense modifiée et stock d'aliment synchronisé ({expense.feed_inventory.get_feed_type_display()}).")
        else:
            messages.success(request, "Dépense modifiée.")
        return redirect("expense-list")
    return render(request, "poulailler_core/expense_form.html", {
        "form": form,
        "expense": expense,
        "title": "Modifier la dépense",
        "submit_label": "Mettre à jour",
    })


@login_required
@admin_required
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if request.method == "POST":
        if hasattr(expense, "feed_inventory") and expense.feed_inventory:
            expense.feed_inventory.delete()
        expense.delete()
        messages.success(request, "Dépense supprimée et stock associé ajusté.")
        return redirect("expense-list")
    return render(request, "poulailler_core/confirm_delete.html", {
        "title": "Supprimer la dépense",
        "object_name": f"{expense.designation} ({expense.amount:,.0f} FCFA du {expense.date})",
        "cancel_url": "/depenses/",
    })


@login_required
def feed_stock_view(request):
    sync_all_feed_expenses()
    summary = FeedInventory.stock_summary()
    purchases = FeedInventory.objects.select_related("batch", "expense").order_by("-purchase_date", "-pk")[:25]
    consumptions = FeedConsumption.objects.select_related("batch").order_by("-date", "-pk")[:25]
    return render(request, "poulailler_core/stocks.html", {
        "feed_stock": summary,
        "purchases": purchases,
        "consumptions": consumptions,
    })


@login_required
@admin_required
def feed_purchase(request):
    form = FeedInventoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        inventory = form.save()
        sync_feed_inventory_expense(inventory)
        total_fcfa = inventory.total_cost
        messages.success(request, f"Réception de stock enregistrée et dépense de {total_fcfa:,.0f} FCFA comptabilisée avec succès.")
        return redirect("feed-stock")
    return render(request, "poulailler_core/feed_purchase_form.html", {
        "form": form,
        "title": "Réception d'aliment (Stock)",
        "submit_label": "Ajouter au stock et comptabiliser",
    })


@login_required
@admin_required
def feed_purchase_edit(request, pk):
    inventory = get_object_or_404(FeedInventory, pk=pk)
    if hasattr(inventory, "feed_preparation") and inventory.feed_preparation:
        return redirect("feed-preparation-edit", pk=inventory.feed_preparation.pk)
    form = FeedInventoryForm(request.POST or None, instance=inventory)
    if request.method == "POST" and form.is_valid():
        inventory = form.save()
        sync_feed_inventory_expense(inventory)
        messages.success(request, f"Réception d'aliment et dépense associées mises à jour ({inventory.total_cost:,.0f} FCFA).")
        return redirect("feed-stock")
    return render(request, "poulailler_core/feed_purchase_form.html", {
        "form": form,
        "title": "Modifier la réception d'aliment",
        "submit_label": "Mettre à jour le stock",
    })


@login_required
@admin_required
def feed_purchase_delete(request, pk):
    inventory = get_object_or_404(FeedInventory, pk=pk)
    if request.method == "POST":
        expense = inventory.expense
        inventory.delete()
        if expense:
            expense.delete()
        messages.success(request, "Réception de stock et dépense associée supprimées.")
        return redirect("feed-stock")
    if inventory.purchase_mode == inventory.PurchaseMode.RETAIL:
        desc = f"{inventory.quantity_kg:.1f} kg au détail"
    else:
        desc = f"{inventory.bags_purchased} sac(s)"
    return render(request, "poulailler_core/confirm_delete.html", {
        "title": "Supprimer la réception d'aliment",
        "object_name": f"{inventory.get_feed_type_display()} du {inventory.purchase_date} ({desc} - {inventory.total_cost:,.0f} FCFA)",
        "cancel_url": "/stocks/",
    })


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
@admin_required
def sale_create(request):
    form = SaleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Vente enregistrée.")
        return redirect("sale-list")
    return render(request, "poulailler_core/form_page.html", {"form": form, "title": "Nouvelle vente", "submit_label": "Enregistrer la vente"})


@login_required
@admin_required
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


# ==============================================================================
# PROVENDE & FORMULATIONS ALIMENTAIRES (FABRICATION MAISON)
# ==============================================================================

@login_required
def feed_formula_list(request):
    """
    Tableau de bord de la provenderie :
    - Liste des formules actives (Croissance, Finition...) avec leurs ingrédients et coût théorique
    - Historique des sessions de préparation d'aliments
    - KPI globaux d'économies réalisées vs sacs industriels
    """
    formulas = FeedFormula.objects.prefetch_related("items").all()
    preparations = FeedPreparation.objects.select_related("batch", "formula", "expense", "feed_inventory").order_by("-date", "-pk")

    total_produced_kg = sum((p.total_produced_kg for p in preparations), Decimal("0.00"))
    total_cost_spent = sum((p.total_cost for p in preparations), Decimal("0.00"))
    total_savings = sum((p.savings_amount for p in preparations), Decimal("0.00"))
    avg_cost_per_kg = (total_cost_spent / total_produced_kg).quantize(Decimal("0.01")) if total_produced_kg > 0 else Decimal("0.00")
    avg_cost_bag = (avg_cost_per_kg * Decimal("50.00")).quantize(Decimal("0.01"))

    return render(request, "poulailler_core/feed_formulas.html", {
        "formulas": formulas,
        "preparations": preparations,
        "total_produced_kg": total_produced_kg,
        "total_cost_spent": total_cost_spent,
        "total_savings": total_savings,
        "avg_cost_per_kg": avg_cost_per_kg,
        "avg_cost_bag": avg_cost_bag,
        "preparations_count": preparations.count(),
    })


@login_required
@admin_required
def feed_formula_create(request):
    """Créer une nouvelle formule de provende avec ses ingrédients."""
    form = FeedFormulaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            formula = form.save()
            names = request.POST.getlist("ingredient_name[]")
            qtys = request.POST.getlist("quantity[]")
            units = request.POST.getlist("unit[]")
            costs = request.POST.getlist("default_unit_cost[]")

            for order, (name, qty_str, unit, cost_str) in enumerate(zip(names, qtys, units, costs), start=1):
                clean_name = (name or "").strip()
                if not clean_name:
                    continue
                try:
                    qty = Decimal(qty_str.replace(",", ".")) if qty_str else Decimal("0.00")
                    cost = Decimal(cost_str.replace(",", ".")) if cost_str else Decimal("0.00")
                except Exception:
                    qty = Decimal("0.00")
                    cost = Decimal("0.00")

                if qty > 0:
                    FeedFormulaItem.objects.create(
                        formula=formula,
                        ingredient_name=clean_name,
                        quantity=qty,
                        unit=unit.strip() or "kg",
                        default_unit_cost=cost,
                        order=order,
                    )

            messages.success(request, f"La formule « {formula.name} » ({formula.target_total_kg:,.0f} kg) a été enregistrée.")
            return redirect("feed-formula-list")

    return render(request, "poulailler_core/feed_formula_form.html", {
        "form": form,
        "title": "Nouvelle formule d'aliment",
        "submit_label": "Enregistrer la formule",
        "is_edit": False,
        "formula": None,
    })


@login_required
@admin_required
def feed_formula_edit(request, pk):
    """Modifier une formule existante et ses ingrédients."""
    formula = get_object_or_404(FeedFormula.objects.prefetch_related("items"), pk=pk)
    form = FeedFormulaForm(request.POST or None, instance=formula)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            formula = form.save()
            formula.items.all().delete()

            names = request.POST.getlist("ingredient_name[]")
            qtys = request.POST.getlist("quantity[]")
            units = request.POST.getlist("unit[]")
            costs = request.POST.getlist("default_unit_cost[]")

            for order, (name, qty_str, unit, cost_str) in enumerate(zip(names, qtys, units, costs), start=1):
                clean_name = (name or "").strip()
                if not clean_name:
                    continue
                try:
                    qty = Decimal(qty_str.replace(",", ".")) if qty_str else Decimal("0.00")
                    cost = Decimal(cost_str.replace(",", ".")) if cost_str else Decimal("0.00")
                except Exception:
                    qty = Decimal("0.00")
                    cost = Decimal("0.00")

                if qty > 0:
                    FeedFormulaItem.objects.create(
                        formula=formula,
                        ingredient_name=clean_name,
                        quantity=qty,
                        unit=unit.strip() or "kg",
                        default_unit_cost=cost,
                        order=order,
                    )

            messages.success(request, f"La formule « {formula.name} » a été mise à jour.")
            return redirect("feed-formula-list")

    return render(request, "poulailler_core/feed_formula_form.html", {
        "form": form,
        "title": f"Modifier la formule : {formula.name}",
        "submit_label": "Mettre à jour la formule",
        "is_edit": True,
        "formula": formula,
    })


@login_required
@admin_required
def feed_formula_delete(request, pk):
    """Supprimer une formule."""
    formula = get_object_or_404(FeedFormula, pk=pk)
    if request.method == "POST":
        name = formula.name
        formula.delete()
        messages.success(request, f"La formule « {name} » a été supprimée.")
        return redirect("feed-formula-list")

    return render(request, "poulailler_core/confirm_delete.html", {
        "title": f"Supprimer la formule « {formula.name} »",
        "object_name": f"Formule {formula.name} ({formula.get_feed_type_display()} · {formula.target_total_kg:,.0f} kg)",
        "cancel_url": "/provende/",
    })


def _get_formulas_dict():
    formulas_qs = FeedFormula.objects.filter(is_active=True).prefetch_related("items")
    formulas_dict = {}
    for f in formulas_qs:
        formulas_dict[str(f.pk)] = {
            "id": f.pk,
            "name": f.name,
            "feed_type": f.feed_type,
            "target_total_kg": float(f.target_total_kg),
            "commercial_bag_reference_price": float(f.commercial_bag_reference_price),
            "items": [
                {
                    "name": item.ingredient_name,
                    "quantity": float(item.quantity),
                    "unit": item.unit,
                    "default_unit_cost": float(item.default_unit_cost),
                }
                for item in f.items.all()
            ]
        }
    return formulas_dict


def _extract_and_save_preparation_items(prep, request):
    milling_cost = prep.milling_cost or Decimal("0.00")

    names = request.POST.getlist("item_name[]")
    recipe_qtys = request.POST.getlist("item_recipe_qty[]")
    recipe_units = request.POST.getlist("item_recipe_unit[]")
    purchased_flags = request.POST.getlist("item_purchased[]")
    descriptions = request.POST.getlist("item_desc[]")
    costs = request.POST.getlist("item_cost[]")

    items_to_create = []
    total_items_cost = Decimal("0.00")

    for idx, name in enumerate(names):
        clean_name = (name or "").strip()
        if not clean_name:
            continue

        qty_str = recipe_qtys[idx] if idx < len(recipe_qtys) else "0"
        unit = recipe_units[idx] if idx < len(recipe_units) else "kg"
        desc = descriptions[idx] if idx < len(descriptions) else ""
        cost_str = costs[idx] if idx < len(costs) else "0"

        is_purchased = True
        if idx < len(purchased_flags):
            is_purchased = purchased_flags[idx] in ("1", "true", "True", "on")

        try:
            recipe_qty = Decimal(qty_str.replace(",", ".")) if qty_str else Decimal("0.00")
        except Exception:
            recipe_qty = Decimal("0.00")

        try:
            purchase_cost = Decimal(cost_str.replace(",", ".")) if (is_purchased and cost_str) else Decimal("0.00")
        except Exception:
            purchase_cost = Decimal("0.00")

        if not is_purchased:
            purchase_cost = Decimal("0.00")

        total_items_cost += purchase_cost

        items_to_create.append({
            "ingredient_name": clean_name,
            "recipe_quantity": recipe_qty,
            "recipe_unit": unit.strip() or "kg",
            "purchased_today": is_purchased,
            "purchased_description": desc.strip(),
            "purchase_cost": purchase_cost,
            "order": idx + 1,
        })

    total_spent = total_items_cost + milling_cost
    prep.total_cost = total_spent

    with transaction.atomic():
        prep.save()

        prep.items.all().delete()
        for item_data in items_to_create:
            FeedPreparationItem.objects.create(
                preparation=prep,
                **item_data
            )

        cat_feed, _ = ExpenseCategory.objects.get_or_create(
            name="Aliment",
            defaults={"is_investment": False, "useful_life_months": 1}
        )
        formula_label = prep.formula.name if prep.formula else "Provende maison"
        expense_designation = f"Fabrication provende {prep.get_feed_type_display()} · {prep.total_produced_kg:,.0f} kg ({formula_label})"

        expense = prep.expense
        if expense:
            expense.batch = prep.batch
            expense.category = cat_feed
            expense.date = prep.date
            expense.designation = expense_designation
            expense.amount = prep.total_cost if prep.total_cost > 0 else Decimal("0.01")
            expense.save()
        else:
            expense = Expense.objects.create(
                batch=prep.batch,
                category=cat_feed,
                date=prep.date,
                designation=expense_designation,
                amount=prep.total_cost if prep.total_cost > 0 else Decimal("0.01"),
            )
            prep.expense = expense

        unit_price_calc = (prep.total_cost / prep.total_produced_kg).quantize(Decimal("0.01")) if prep.total_produced_kg > 0 else Decimal("0.01")
        feed_inv = prep.feed_inventory
        if feed_inv:
            feed_inv.batch = prep.batch
            feed_inv.expense = expense
            feed_inv.feed_type = prep.feed_type
            feed_inv.purchase_mode = FeedInventory.PurchaseMode.RETAIL
            feed_inv.purchase_date = prep.date
            feed_inv.quantity_kg = prep.total_produced_kg
            feed_inv.unit_price = unit_price_calc if unit_price_calc > 0 else Decimal("0.01")
            feed_inv.supplier = "Provenderie ferme (Fabrication maison)"
            feed_inv.notes = f"Session du {prep.date} · Formule : {formula_label}"
            feed_inv._skip_sync_signal = True
            feed_inv.save()
        else:
            feed_inv = FeedInventory(
                batch=prep.batch,
                expense=expense,
                feed_type=prep.feed_type,
                purchase_mode=FeedInventory.PurchaseMode.RETAIL,
                purchase_date=prep.date,
                quantity_kg=prep.total_produced_kg,
                unit_price=unit_price_calc if unit_price_calc > 0 else Decimal("0.01"),
                supplier="Provenderie ferme (Fabrication maison)",
                notes=f"Session du {prep.date} · Formule : {formula_label}",
            )
            feed_inv._skip_sync_signal = True
            feed_inv.save()
            prep.feed_inventory = feed_inv

        prep.save(update_fields=["expense", "feed_inventory"])


@login_required
@admin_required
def feed_preparation_create(request, formula_id=None):
    """
    Session de préparation d'aliments :
    - Saisie des ingrédients incorporés
    - Déclaration de ce qui a réellement été acheté aujourd'hui vs ce qui est pris sur réserve existante (0 FCFA)
    - Synchronisation automatique en 1 clic :
        * Crée la Dépense globale (Catégorie Aliment)
        * Alimente le Stock d'aliment (+X kg)
        * Calcule le coût de revient / kg et l'économie réalisée vs sac industriel
    """
    selected_formula_id = formula_id or request.GET.get("formula")
    initial_formula = None
    if selected_formula_id:
        initial_formula = FeedFormula.objects.filter(pk=selected_formula_id, is_active=True).first()

    initial_data = {}
    if initial_formula:
        initial_data["formula"] = initial_formula
        initial_data["feed_type"] = initial_formula.feed_type
        initial_data["total_produced_kg"] = initial_formula.target_total_kg
        initial_data["commercial_bag_reference_price"] = initial_formula.commercial_bag_reference_price

    form = FeedPreparationForm(request.POST or None, initial=initial_data)

    if request.method == "POST" and form.is_valid():
        prep = form.save(commit=False)
        _extract_and_save_preparation_items(prep, request)

        messages.success(
            request,
            f"Aliment fabriqué avec succès ! +{prep.total_produced_kg:,.0f} kg de {prep.get_feed_type_display()} versés au stock. "
            f"Dépense réelle de {prep.total_cost:,.0f} FCFA comptabilisée (Coût : {prep.cost_per_kg:,.0f} F/kg · "
            f"Économie estimée : {prep.savings_amount:,.0f} FCFA)."
        )
        return redirect("feed-preparation-detail", pk=prep.pk)

    return render(request, "poulailler_core/feed_preparation_form.html", {
        "form": form,
        "initial_formula": initial_formula,
        "formulas_json": json.dumps(_get_formulas_dict()),
        "existing_items_json": "null",
        "title": "Nouvelle fabrication d'aliment · Provende maison",
    })


@login_required
@admin_required
def feed_preparation_edit(request, pk):
    """Modifier une session de fabrication d'aliments et synchroniser dépense et stock."""
    prep = get_object_or_404(
        FeedPreparation.objects.select_related("batch", "formula", "expense", "feed_inventory").prefetch_related("items"),
        pk=pk
    )
    form = FeedPreparationForm(request.POST or None, instance=prep)

    if request.method == "POST" and form.is_valid():
        prep = form.save(commit=False)
        _extract_and_save_preparation_items(prep, request)

        messages.success(
            request,
            f"Fabrication mise à jour avec succès ! Stock ({prep.total_produced_kg:,.0f} kg de {prep.get_feed_type_display()}) "
            f"et dépense ({prep.total_cost:,.0f} FCFA) synchronisés."
        )
        return redirect("feed-preparation-detail", pk=prep.pk)

    existing_items_data = [
        {
            "name": item.ingredient_name,
            "qty": float(item.recipe_quantity) if item.recipe_quantity is not None else "",
            "unit": item.recipe_unit,
            "cost": float(item.purchase_cost),
            "is_purchased": item.purchased_today,
            "desc": item.purchased_description,
        }
        for item in prep.items.all().order_by("order", "pk")
    ]

    return render(request, "poulailler_core/feed_preparation_form.html", {
        "form": form,
        "prep": prep,
        "initial_formula": prep.formula,
        "formulas_json": json.dumps(_get_formulas_dict()),
        "existing_items_json": json.dumps(existing_items_data),
        "title": f"Modifier la fabrication du {prep.date.strftime('%d/%m/%Y')} · {prep.get_feed_type_display()}",
    })


@login_required
def feed_preparation_detail(request, pk):
    """Fiche détaillée d'une session de fabrication d'aliments."""
    prep = get_object_or_404(
        FeedPreparation.objects.select_related("batch", "formula", "expense", "feed_inventory").prefetch_related("items"),
        pk=pk
    )
    return render(request, "poulailler_core/feed_preparation_detail.html", {
        "prep": prep,
        "items": prep.items.all(),
    })


@login_required
@admin_required
def feed_preparation_delete(request, pk):
    """Supprimer une préparation et nettoyer la dépense et le stock créés."""
    prep = get_object_or_404(FeedPreparation, pk=pk)
    if request.method == "POST":
        date_str = prep.date
        produced_kg = prep.total_produced_kg
        feed_type_label = prep.get_feed_type_display()

        with transaction.atomic():
            inv_id = prep.feed_inventory_id
            exp_id = prep.expense_id
            prep.delete()
            if inv_id:
                FeedInventory.objects.filter(pk=inv_id).delete()
            if exp_id:
                Expense.objects.filter(pk=exp_id).delete()

        messages.success(request, f"La fabrication du {date_str} ({produced_kg:,.0f} kg de {feed_type_label}) a été supprimée, avec annulation du stock et de la dépense associée.")
        return redirect("feed-formula-list")

    return render(request, "poulailler_core/confirm_delete.html", {
        "title": f"Supprimer la fabrication du {prep.date}",
        "object_name": f"Fabrication provende {prep.get_feed_type_display()} · {prep.total_produced_kg:,.0f} kg ({prep.total_cost:,.0f} FCFA)",
        "cancel_url": f"/provende/preparations/{prep.pk}/",
    })

