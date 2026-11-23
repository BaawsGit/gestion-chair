from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Batch,
    Expense,
    FeedConsumption,
    FeedFormula,
    FeedFormulaItem,
    FeedInventory,
    FeedPreparation,
    FeedPreparationItem,
    FeedType,
    Mortality,
    ProphylaxisProgram,
    Sale,
    TreatmentLog,
)


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"
            if isinstance(field.widget, forms.NumberInput):
                if str(field.widget.attrs.get("min", "")) == "0.01" and field.widget.attrs.get("step") not in (None, "0.01", "any"):
                    field.widget.attrs["min"] = "0"


class BatchForm(StyledModelForm):
    class Meta:
        model = Batch
        fields = [
            "name",
            "start_date",
            "target_duration_days",
            "ordered_quantity",
            "unit_cost_chick",
            "bonus_quantity",
            "notes",
        ]
        widgets = {
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "ordered_quantity": forms.NumberInput(attrs={"min": "1", "step": "1", "id": "id_ordered_quantity"}),
            "bonus_quantity": forms.NumberInput(attrs={"min": "0", "step": "1", "id": "id_bonus_quantity"}),
            "unit_cost_chick": forms.NumberInput(attrs={"min": "0", "step": "0.01", "id": "id_unit_cost_chick"}),
        }
        labels = {
            "name": "Nom du lot",
            "start_date": "Date d'arrivée des poussins",
            "target_duration_days": "Durée prévue du cycle (jours)",
            "ordered_quantity": "Quantité commandée / facturée (sujets)",
            "unit_cost_chick": "Prix unitaire du poussin (FCFA)",
            "bonus_quantity": "Ajout / Poussins bonus offerts (gratuit - 0 FCFA)",
            "notes": "Remarques & observations",
        }
        help_texts = {
            "ordered_quantity": "Quantité achetée et facturée (multipliée par le prix unitaire).",
            "unit_cost_chick": "Prix unitaire d'un poussin (ex: 750 FCFA).",
            "bonus_quantity": "Unités gratuites ajoutées par le couvoir (ex: 1 pour 50 commandés). Ce champ ne coûte rien.",
            "target_duration_days": "Cycle habituel de ~45 jours pour du poulet de chair.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and "start_date" in self.fields and not self.initial.get("start_date"):
            self.fields["start_date"].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        ordered = cleaned_data.get("ordered_quantity") or 0
        bonus = cleaned_data.get("bonus_quantity") or 0
        if ordered + bonus < 1:
            raise ValidationError("L'effectif total démarré (commandés + bonus) doit être au minimum de 1 poussin.")
        return cleaned_data


class ExpenseForm(StyledModelForm):
    feed_type = forms.ChoiceField(
        label="Type d'aliment",
        choices=[("", "Sélectionnez le type d'aliment..."), *FeedType.choices],
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_feed_type"}),
        help_text="Spécifiez le type d'aliment pour impacter automatiquement le stock."
    )
    feed_purchase_mode = forms.ChoiceField(
        label="Format d'achat de l'aliment",
        choices=[("bags", "En sac(s)"), ("retail", "Au détail (en kg)")],
        initial="bags",
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_feed_purchase_mode"}),
    )
    feed_quantity_kg = forms.DecimalField(
        label="Quantité en kg (au détail)",
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "ex. 10", "id": "id_feed_quantity_kg"}),
        help_text="Nombre de kilogrammes achetés (ex: 10 kg)."
    )
    feed_bags_purchased = forms.IntegerField(
        label="Nombre de sacs",
        min_value=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1", "placeholder": "ex. 2", "id": "id_feed_bags_purchased"}),
        help_text="Nombre de sacs achetés."
    )
    feed_bag_weight_kg = forms.DecimalField(
        label="Poids par sac (kg)",
        initial=Decimal("50.00"),
        min_value=Decimal("0.01"),
        max_digits=7,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.5", "placeholder": "ex. 50 ou 25", "id": "id_feed_bag_weight_kg"}),
        help_text="Poids standard du sac en kg."
    )
    feed_supplier = forms.CharField(
        label="Fournisseur d'aliment",
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Nom du fournisseur ou magasin", "id": "id_feed_supplier"})
    )

    class Meta:
        model = Expense
        fields = ["batch", "category", "date", "designation", "amount", "invoice_file"]
        widgets = {
            "date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control", "id": "id_date"}),
            "category": forms.Select(attrs={"id": "id_category", "class": "form-select"}),
            "designation": forms.TextInput(attrs={"id": "id_designation", "class": "form-control"}),
            "amount": forms.NumberInput(attrs={"id": "id_amount", "class": "form-control", "step": "1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "batch" in self.fields:
            self.fields["batch"].empty_label = "Ferme générale (charges fixes)"
            if not self.instance.pk and "initial" not in kwargs:
                from .models import Batch
                active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
                if active_batch:
                    self.fields["batch"].initial = active_batch

        if not self.instance.pk and "date" in self.fields and not self.initial.get("date"):
            self.fields["date"].initial = timezone.localdate()

        if self.instance.pk and hasattr(self.instance, "feed_inventory") and self.instance.feed_inventory:
            inv = self.instance.feed_inventory
            self.fields["feed_type"].initial = inv.feed_type
            self.fields["feed_purchase_mode"].initial = inv.purchase_mode
            self.fields["feed_quantity_kg"].initial = inv.quantity_kg
            self.fields["feed_bags_purchased"].initial = inv.bags_purchased
            self.fields["feed_bag_weight_kg"].initial = inv.bag_weight_kg
            self.fields["feed_supplier"].initial = inv.supplier

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        amount = cleaned_data.get("amount")

        is_feed = bool(category and "aliment" in category.name.lower())
        if is_feed:
            feed_type = cleaned_data.get("feed_type")
            if not feed_type:
                designation = (cleaned_data.get("designation") or "").lower()
                if "démarrage" in designation or "demarrage" in designation:
                    cleaned_data["feed_type"] = FeedType.STARTER
                elif "croissance" in designation:
                    cleaned_data["feed_type"] = FeedType.GROWER
                elif "finition" in designation:
                    cleaned_data["feed_type"] = FeedType.FINISHER
                else:
                    self.add_error("feed_type", "Pour la catégorie Aliment, veuillez sélectionner le type d'aliment (Démarrage, Croissance ou Finition) pour impacter le stock.")

            mode = cleaned_data.get("feed_purchase_mode") or "bags"
            if mode == "retail":
                qty = cleaned_data.get("feed_quantity_kg")
                if not qty or qty <= 0:
                    self.add_error("feed_quantity_kg", "Veuillez renseigner le poids acheté en kg au détail (ex: 10 kg).")
            else:
                bags = cleaned_data.get("feed_bags_purchased")
                weight = cleaned_data.get("feed_bag_weight_kg")
                if not bags or bags <= 0:
                    self.add_error("feed_bags_purchased", "Veuillez renseigner le nombre de sacs achetés.")
                if not weight or weight <= 0:
                    self.add_error("feed_bag_weight_kg", "Veuillez renseigner le poids par sac en kg (ex: 25 ou 50).")

        return cleaned_data

    def save(self, commit=True):
        expense = super().save(commit=commit)
        if commit:
            self.sync_stock(expense)
        return expense

    def sync_stock(self, expense):
        from .models import FeedInventory
        category = expense.category
        if category and "aliment" in category.name.lower() and self.cleaned_data.get("feed_type"):
            feed_type = self.cleaned_data["feed_type"]
            mode = self.cleaned_data.get("feed_purchase_mode") or "bags"
            supplier = self.cleaned_data.get("feed_supplier") or ""

            inv = getattr(expense, "feed_inventory", None)
            if not inv:
                inv = FeedInventory.objects.filter(expense=expense).first()
            if not inv:
                inv = FeedInventory(expense=expense)

            inv.batch = expense.batch
            inv.feed_type = feed_type
            inv.purchase_date = expense.date
            inv.purchase_mode = mode
            inv.supplier = supplier

            if mode == "retail":
                qty = self.cleaned_data.get("feed_quantity_kg") or Decimal("1.00")
                inv.quantity_kg = qty
                inv.unit_price = (expense.amount / qty).quantize(Decimal("0.01"))
                inv.bags_purchased = None
                inv.bag_weight_kg = None
            else:
                bags = self.cleaned_data.get("feed_bags_purchased") or 1
                weight = self.cleaned_data.get("feed_bag_weight_kg") or Decimal("50.00")
                inv.bags_purchased = bags
                inv.bag_weight_kg = weight
                inv.unit_price = (expense.amount / Decimal(bags)).quantize(Decimal("0.01"))
                inv.quantity_kg = None

            inv._skip_sync_signal = True
            inv.save()
        else:
            if hasattr(expense, "feed_inventory") and expense.feed_inventory:
                expense.feed_inventory.delete()


class FeedInventoryForm(StyledModelForm):
    class Meta:
        model = FeedInventory
        fields = [
            "batch", "feed_type", "purchase_date", "purchase_mode",
            "bags_purchased", "bag_weight_kg", "quantity_kg",
            "unit_price", "supplier", "notes"
        ]
        widgets = {
            "purchase_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}),
            "purchase_mode": forms.Select(attrs={"class": "form-select", "id": "id_purchase_mode"}),
            "bags_purchased": forms.NumberInput(attrs={"class": "form-control", "min": "1", "placeholder": "ex. 2", "id": "id_bags_purchased"}),
            "bag_weight_kg": forms.NumberInput(attrs={"class": "form-control", "step": "0.5", "placeholder": "ex. 50 ou 25", "id": "id_bag_weight_kg"}),
            "quantity_kg": forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "ex. 10", "id": "id_quantity_kg"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-control", "step": "1", "placeholder": "ex. 1000 ou 22500", "id": "id_unit_price"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "batch" in self.fields:
            self.fields["batch"].empty_label = "Ferme générale (sans lot spécifique)"
            if not self.instance.pk and "initial" not in kwargs:
                from .models import Batch
                active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
                if active_batch:
                    self.fields["batch"].initial = active_batch

        self.fields["purchase_mode"].required = False
        self.fields["purchase_mode"].initial = FeedInventory.PurchaseMode.BAGS
        self.fields["bags_purchased"].required = False
        self.fields["bag_weight_kg"].required = False
        self.fields["quantity_kg"].required = False

        if not self.instance.pk:
            if not self.initial.get("purchase_date"):
                self.fields["purchase_date"].initial = timezone.localdate()
            if not self.initial.get("bag_weight_kg"):
                self.fields["bag_weight_kg"].initial = Decimal("50.00")

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("purchase_mode") or FeedInventory.PurchaseMode.BAGS
        cleaned_data["purchase_mode"] = mode
        unit_price = cleaned_data.get("unit_price")

        if not unit_price or unit_price <= 0:
            self.add_error("unit_price", "Le prix unitaire doit être supérieur à zéro.")

        if mode == FeedInventory.PurchaseMode.RETAIL:
            qty = cleaned_data.get("quantity_kg")
            if not qty or qty <= 0:
                self.add_error("quantity_kg", "Renseignez la quantité en kg achetée au détail (ex: 10 kg).")
            cleaned_data["bags_purchased"] = None
            cleaned_data["bag_weight_kg"] = None
        else:
            bags = cleaned_data.get("bags_purchased")
            weight = cleaned_data.get("bag_weight_kg")
            if not bags or bags <= 0:
                self.add_error("bags_purchased", "Renseignez le nombre de sacs achetés (ex: 2).")
            if not weight or weight <= 0:
                self.add_error("bag_weight_kg", "Renseignez le poids par sac en kg (ex: 25 ou 50).")
            cleaned_data["quantity_kg"] = None

        return cleaned_data


class SaleForm(StyledModelForm):
    class Meta:
        model = Sale
        fields = [
            "batch",
            "sale_type",
            "date",
            "quantity_sold",
            "total_weight_kg",
            "unit_price",
            "total_amount",
            "customer_name",
            "payment_status",
            "quantity_paid",
            "amount_paid",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "sale_type": forms.Select(attrs={"id": "id_sale_type"}),
            "quantity_sold": forms.NumberInput(attrs={"min": "1", "step": "1", "id": "id_quantity_sold"}),
            "total_weight_kg": forms.NumberInput(attrs={"min": "0", "step": "0.01", "id": "id_total_weight_kg", "placeholder": "ex. 1 ou 1.5"}),
            "unit_price": forms.NumberInput(attrs={"min": "0", "step": "any", "id": "id_unit_price", "placeholder": "ex. 2000 ou 2500"}),
            "total_amount": forms.NumberInput(attrs={"min": "0", "step": "any", "id": "id_total_amount"}),
            "payment_status": forms.Select(attrs={"id": "id_payment_status"}),
            "quantity_paid": forms.NumberInput(attrs={"min": "0", "step": "1", "id": "id_quantity_paid", "placeholder": "ex. 22 sur 40"}),
            "amount_paid": forms.NumberInput(attrs={"min": "0", "step": "any", "id": "id_amount_paid", "placeholder": "Montant déjà encaissé"}),
        }
        labels = {
            "sale_type": "Type de vente / Produit",
            "quantity_sold": "Quantité commandée / vendue (nombre de poulets)",
            "total_weight_kg": "Poids vendu (kg)",
            "unit_price": "Prix unitaire / Prix du kg (FCFA)",
            "total_amount": "Montant total de la commande (FCFA)",
            "payment_status": "Statut du paiement",
            "quantity_paid": "Nombre de sujets déjà payés",
            "amount_paid": "Montant déjà encaissé en caisse (FCFA)",
        }
        help_texts = {
            "sale_type": "Choisissez « Abats mélangés (gésiers, cœurs & foies) » pour vendre au kg ou par lot sans diminuer l'effectif vivant des poulets.",
            "quantity_sold": "Nombre total de poulets livrés / commandés (ex: 40 sujets).",
            "total_weight_kg": "Obligatoire pour une vente d'abats au kg (ex: 1 kg, 1.5 kg, 2 kg...). Optionnel pour les poulets.",
            "unit_price": "Prix d'un poulet ou prix d'un kilogramme d'abats (modifiable librement selon le client).",
            "total_amount": "Montant total de la vente (Quantité totale × Prix unitaire).",
            "payment_status": "Choisissez « Paiement partiel / Avance » si seulement une partie de la commande (ex: 22 sujets sur 40) a été payée.",
            "quantity_paid": "Ex: si le client prend 40 sujets mais n'en paie que 22 aujourd'hui, indiquez 22. L'argent des 22 sera comptabilisé en caisse et les 18 restants seront en attente.",
            "amount_paid": "Calculé automatiquement (Nombre de sujets payés × Prix unitaire), ou saisissez directement la somme encaissée.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sale_type"].required = False
        self.fields["sale_type"].initial = Sale.SaleType.CHICKEN
        self.fields["quantity_sold"].required = False
        self.fields["quantity_paid"].required = False
        self.fields["amount_paid"].required = False
        # Override min="0.01" injected by ModelForm from MinValueValidator so HTML5 accepts any integer or decimal
        for fname in ("total_weight_kg", "unit_price", "total_amount", "amount_paid"):
            if fname in self.fields:
                self.fields[fname].widget.attrs["min"] = "0"
                self.fields[fname].widget.attrs["step"] = "any"
        if not self.instance.pk:
            if "date" in self.fields and not self.initial.get("date"):
                self.fields["date"].initial = timezone.localdate()
            if "batch" in self.fields and not self.initial.get("batch"):
                active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
                if active_batch:
                    self.fields["batch"].initial = active_batch
            if "quantity_sold" in self.fields and not self.initial.get("quantity_sold"):
                self.fields["quantity_sold"].initial = 1

    def clean(self):
        cleaned_data = super().clean()
        sale_type = cleaned_data.get("sale_type") or Sale.SaleType.CHICKEN
        cleaned_data["sale_type"] = sale_type
        quantity = cleaned_data.get("quantity_sold")
        total_weight_kg = cleaned_data.get("total_weight_kg")
        batch = cleaned_data.get("batch")
        unit_price = cleaned_data.get("unit_price")
        total_amount = cleaned_data.get("total_amount")
        payment_status = cleaned_data.get("payment_status") or Sale.PaymentStatus.PAID
        quantity_paid = cleaned_data.get("quantity_paid")
        amount_paid = cleaned_data.get("amount_paid")

        if sale_type == Sale.SaleType.CHICKEN:
            if not quantity or quantity < 1:
                self.add_error("quantity_sold", "Veuillez indiquer le nombre de poulets vendus.")
                return cleaned_data
            if batch:
                available = batch.current_stock
                if (
                    self.instance.pk
                    and self.instance.batch_id == batch.pk
                    and self.instance.sale_type == Sale.SaleType.CHICKEN
                ):
                    available += self.instance.quantity_sold
                if quantity > available:
                    self.add_error(
                        "quantity_sold",
                        f"La quantité dépasse l'effectif disponible ({available}).",
                    )
        else:
            if not quantity or quantity < 1:
                quantity = 1
                cleaned_data["quantity_sold"] = 1

        if unit_price:
            if sale_type == Sale.SaleType.GIBLETS and total_weight_kg and total_weight_kg > 0 and quantity == 1:
                multiplier = total_weight_kg
            else:
                multiplier = Decimal(quantity or 1)

            calculated_total = (unit_price * multiplier).quantize(Decimal("0.01"))
            if not total_amount:
                total_amount = calculated_total
                cleaned_data["total_amount"] = calculated_total
            elif self.instance.pk:
                qty_changed = quantity != self.instance.quantity_sold
                weight_changed = total_weight_kg != self.instance.total_weight_kg
                price_changed = unit_price != self.instance.unit_price
                total_unchanged = total_amount == self.instance.total_amount
                if (qty_changed or weight_changed or price_changed) and total_unchanged:
                    total_amount = calculated_total
                    cleaned_data["total_amount"] = calculated_total

        if quantity_paid is not None and quantity and quantity_paid > quantity:
            self.add_error(
                "quantity_paid",
                f"Le nombre de sujets payés ({quantity_paid}) ne peut pas dépasser la quantité vendue ({quantity}).",
            )
            return cleaned_data

        # Reconcile payment_status, quantity_paid, and amount_paid
        if total_amount and unit_price and quantity:
            status_explicitly_set_to_paid = (
                self.instance.pk
                and payment_status == Sale.PaymentStatus.PAID
                and self.instance.payment_status != Sale.PaymentStatus.PAID
                and quantity_paid == self.instance.quantity_paid
            )
            qty_paid_changed = (
                not self.instance.pk and quantity_paid is not None
            ) or (
                self.instance.pk and quantity_paid != self.instance.quantity_paid
            )
            amt_paid_changed = (
                not self.instance.pk and amount_paid is not None
            ) or (
                self.instance.pk and amount_paid != self.instance.amount_paid
            )

            if status_explicitly_set_to_paid:
                cleaned_data["payment_status"] = Sale.PaymentStatus.PAID
                cleaned_data["quantity_paid"] = quantity
                cleaned_data["amount_paid"] = total_amount
            elif qty_paid_changed and quantity_paid is not None:
                if quantity_paid >= quantity:
                    cleaned_data["payment_status"] = Sale.PaymentStatus.PAID
                    cleaned_data["quantity_paid"] = quantity
                    cleaned_data["amount_paid"] = total_amount
                elif quantity_paid == 0:
                    if amt_paid_changed and amount_paid and amount_paid > 0:
                        cleaned_data["payment_status"] = (
                            Sale.PaymentStatus.PAID if amount_paid >= total_amount else Sale.PaymentStatus.PARTIAL
                        )
                        cleaned_data["amount_paid"] = min(amount_paid, total_amount)
                    else:
                        cleaned_data["payment_status"] = Sale.PaymentStatus.CREDIT
                        cleaned_data["quantity_paid"] = 0
                        cleaned_data["amount_paid"] = Decimal("0.00")
                else:
                    # 0 < quantity_paid < quantity (e.g. 22 paid out of 40!)
                    cleaned_data["payment_status"] = Sale.PaymentStatus.PARTIAL
                    cleaned_data["quantity_paid"] = quantity_paid
                    if not amt_paid_changed or not amount_paid:
                        cleaned_data["amount_paid"] = (unit_price * Decimal(quantity_paid)).quantize(Decimal("0.01"))
                    else:
                        cleaned_data["amount_paid"] = min(amount_paid, total_amount)
            elif amt_paid_changed and amount_paid is not None:
                if amount_paid >= total_amount:
                    cleaned_data["payment_status"] = Sale.PaymentStatus.PAID
                    cleaned_data["quantity_paid"] = quantity
                    cleaned_data["amount_paid"] = total_amount
                elif amount_paid <= Decimal("0.00"):
                    cleaned_data["payment_status"] = Sale.PaymentStatus.CREDIT
                    cleaned_data["quantity_paid"] = 0
                    cleaned_data["amount_paid"] = Decimal("0.00")
                else:
                    cleaned_data["payment_status"] = Sale.PaymentStatus.PARTIAL
                    cleaned_data["amount_paid"] = amount_paid
                    if unit_price > 0:
                        cleaned_data["quantity_paid"] = min(int(amount_paid // unit_price), quantity)
            else:
                # Neither quantity_paid nor amount_paid was specifically changed
                if payment_status == Sale.PaymentStatus.PAID:
                    cleaned_data["quantity_paid"] = quantity
                    cleaned_data["amount_paid"] = total_amount
                elif payment_status == Sale.PaymentStatus.CREDIT:
                    cleaned_data["quantity_paid"] = 0
                    cleaned_data["amount_paid"] = Decimal("0.00")
                elif payment_status == Sale.PaymentStatus.PARTIAL:
                    if quantity_paid is not None and 0 < quantity_paid < quantity:
                        cleaned_data["amount_paid"] = (unit_price * Decimal(quantity_paid)).quantize(Decimal("0.01"))
                    elif amount_paid is not None and Decimal("0.00") < amount_paid < total_amount:
                        cleaned_data["amount_paid"] = amount_paid
                    else:
                        cleaned_data["quantity_paid"] = 0
                        cleaned_data["amount_paid"] = Decimal("0.00")

        return cleaned_data


class DailyEventForm(forms.Form):
    date = forms.DateField(
        label="Date", initial=timezone.localdate,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"})
    )
    mortality_count = forms.IntegerField(
        label="Morts du jour", min_value=0, initial=0, required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0"})
    )
    cause_suspected = forms.ChoiceField(
        label="Cause présumée", choices=Mortality.Cause.choices, required=False,
        widget=forms.Select(attrs={"class": "form-select"})
    )
    mortality_notes = forms.CharField(
        label="Notes mortalité", required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2})
    )

    # Multi-feed distribution fields
    starter_kg = forms.DecimalField(
        label="Démarrage (kg)", min_value=Decimal("0.00"), decimal_places=2,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "0.0", "id": "id_feed_starter_kg"})
    )
    starter_bags = forms.DecimalField(
        label="Démarrage (sacs)", min_value=Decimal("0.000"), decimal_places=3,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "0.0", "id": "id_feed_starter_bags"})
    )
    grower_kg = forms.DecimalField(
        label="Croissance (kg)", min_value=Decimal("0.00"), decimal_places=2,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "0.0", "id": "id_feed_grower_kg"})
    )
    grower_bags = forms.DecimalField(
        label="Croissance (sacs)", min_value=Decimal("0.000"), decimal_places=3,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "0.0", "id": "id_feed_grower_bags"})
    )
    finisher_kg = forms.DecimalField(
        label="Finition (kg)", min_value=Decimal("0.00"), decimal_places=2,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1", "placeholder": "0.0", "id": "id_feed_finisher_kg"})
    )
    finisher_bags = forms.DecimalField(
        label="Finition (sacs)", min_value=Decimal("0.000"), decimal_places=3,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "0.0", "id": "id_feed_finisher_bags"})
    )

    # Legacy fields (maintained for backward-compatibility with tests / APIs)
    feed_type = forms.ChoiceField(
        label="Type d'aliment", choices=[("", "---------"), *FeedType.choices], required=False,
        widget=forms.Select(attrs={"class": "form-select"})
    )
    bags_consumed = forms.DecimalField(
        label="Sacs distribués", min_value=Decimal("0.001"), decimal_places=3,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"})
    )
    kg_consumed = forms.DecimalField(
        label="Kilogrammes distribués", min_value=Decimal("0.01"), decimal_places=2,
        required=False, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"})
    )
    treatments = forms.ModelMultipleChoiceField(
        label="Soins administrés", queryset=TreatmentLog.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, batch, target_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch = batch
        from django.db.models import Q
        date_val = target_date or self.initial.get("date") or timezone.localdate()
        self.fields["treatments"].queryset = TreatmentLog.objects.filter(batch=batch).filter(
            Q(is_completed=False) | Q(date_administered=date_val) | Q(date_planned=date_val)
        ).distinct().order_by("target_day", "date_planned")

    def clean(self):
        cleaned = super().clean()
        count = cleaned.get("mortality_count") or 0

        # Synchronize legacy fields with multi-feed fields
        feed_type = cleaned.get("feed_type")
        legacy_bags = cleaned.get("bags_consumed")
        legacy_kg = cleaned.get("kg_consumed")

        if feed_type:
            if legacy_kg and not cleaned.get(f"{feed_type}_kg"):
                cleaned[f"{feed_type}_kg"] = legacy_kg
            if legacy_bags and not cleaned.get(f"{feed_type}_bags"):
                cleaned[f"{feed_type}_bags"] = legacy_bags
        else:
            # If multi-feed fields are filled, set legacy fields to first active feed for backwards compatibility
            for ft in [FeedType.STARTER, FeedType.GROWER, FeedType.FINISHER]:
                kg = cleaned.get(f"{ft}_kg")
                bags = cleaned.get(f"{ft}_bags")
                if (kg and kg > 0) or (bags and bags > 0):
                    cleaned["feed_type"] = ft
                    if kg and kg > 0:
                        cleaned["kg_consumed"] = kg
                    elif bags and bags > 0:
                        cleaned["bags_consumed"] = bags
                    break

        if count > self.batch.current_stock:
            self.add_error("mortality_count", "Le nombre dépasse l'effectif vivant du lot.")
        if cleaned.get("treatments") and any(item.batch_id != self.batch.pk for item in cleaned["treatments"]):
            self.add_error("treatments", "Un soin sélectionné n'appartient pas à ce lot.")
        return cleaned


class DailyMedicationForm(forms.Form):
    start_day = forms.IntegerField(
        label="Jour (J)",
        min_value=1,
        max_value=45,
        initial=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "45"}),
        help_text="Ex. 1 pour le 1er jour (J1), 2 pour le 2ème jour (J2), 4 pour J4..."
    )
    end_day = forms.IntegerField(
        label="Jusqu'au jour (optionnel pour plusieurs jours pareils)",
        min_value=1,
        max_value=45,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "45"}),
        help_text="Pour appliquer le même médicament sur plusieurs jours (ex. Tétracolivit du jour 1 au jour 2 ou 3), indiquez le jour de fin."
    )
    medicine_name = forms.CharField(
        label="Médicament / Produit",
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "ex. Tétracolivit, Vaccin Gumboro, Anticoccidien..."}),
        help_text="Nom du produit ou du vaccin à administrer."
    )
    administration_route = forms.ChoiceField(
        label="Voie d'administration",
        initial="Eau de boisson",
        choices=[
            ("Eau de boisson", "Eau de boisson"),
            ("Aliment", "Aliment"),
            ("Injection", "Injection"),
            ("Goutte oculaire", "Goutte oculaire"),
            ("Pulvérisation", "Pulvérisation / Nébulisation"),
            ("Autre", "Autre"),
        ],
        widget=forms.Select(attrs={"class": "form-select"})
    )
    dosage = forms.CharField(
        label="Posologie / Dosage",
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "ex. 1 g / litre d'eau, 1 dose / sujet..."}),
        help_text="Quantité ou dilution recommandée."
    )
    notes = forms.CharField(
        label="Consignes & Remarques",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "ex. Distribuer le matin pendant 4h, faire jeûner 2h avant le vaccin..."}),
        help_text="Instructions pratiques pour la distribution."
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_day")
        end = cleaned.get("end_day")
        if start and end and end < start:
            raise ValidationError("Le jour de fin doit être supérieur ou égal au jour de début.")
        if start and not end:
            cleaned["end_day"] = start
        return cleaned


# Alias for compatibility if imported elsewhere
ProphylaxisProgramForm = DailyMedicationForm


class TreatmentLogForm(StyledModelForm):
    class Meta:
        model = TreatmentLog
        fields = [
            "batch",
            "target_day",
            "date_planned",
            "medicine_name",
            "dosage",
            "administration_route",
            "is_completed",
            "notes",
        ]
        labels = {
            "batch": "Lot concerné",
            "target_day": "Jour d'âge ciblé (J)",
            "date_planned": "Date prévue",
            "medicine_name": "Médicament / Soin",
            "dosage": "Dosage",
            "administration_route": "Voie d'administration",
            "is_completed": "Déjà administré",
            "notes": "Remarques",
        }
        widgets = {
            "date_planned": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "target_day": forms.NumberInput(attrs={"min": "1", "max": "60"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        cleaned = super().clean()
        batch = cleaned.get("batch")
        target_day = cleaned.get("target_day")
        date_planned = cleaned.get("date_planned")
        if batch and target_day and not date_planned:
            cleaned["date_planned"] = batch.start_date + timedelta(days=target_day - 1)
        return cleaned


class FeedFormulaForm(StyledModelForm):
    class Meta:
        model = FeedFormula
        fields = ["name", "feed_type", "target_total_kg", "commercial_bag_reference_price", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "ex. Formule Croissance Standard 300 kg"}),
            "feed_type": forms.Select(attrs={"class": "form-select"}),
            "target_total_kg": forms.NumberInput(attrs={"class": "form-control", "step": "1", "min": "1"}),
            "commercial_bag_reference_price": forms.NumberInput(attrs={"class": "form-control", "step": "500", "min": "0"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Instructions particulières de broyage ou mélange..."}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class FeedPreparationForm(StyledModelForm):
    class Meta:
        model = FeedPreparation
        fields = [
            "batch", "formula", "feed_type", "date",
            "total_produced_kg", "milling_cost",
            "commercial_bag_reference_price", "notes"
        ]
        widgets = {
            "batch": forms.Select(attrs={"class": "form-select", "id": "id_prep_batch"}),
            "formula": forms.Select(attrs={"class": "form-select", "id": "id_prep_formula"}),
            "feed_type": forms.Select(attrs={"class": "form-select", "id": "id_prep_feed_type"}),
            "date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control", "id": "id_prep_date"}),
            "total_produced_kg": forms.NumberInput(attrs={"class": "form-control", "step": "0.5", "min": "1", "id": "id_prep_total_produced_kg"}),
            "milling_cost": forms.NumberInput(attrs={"class": "form-control", "step": "100", "min": "0", "id": "id_prep_milling_cost", "placeholder": "0"}),
            "commercial_bag_reference_price": forms.NumberInput(attrs={"class": "form-control", "step": "500", "min": "0", "id": "id_prep_ref_bag_price"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Observations sur la session..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "batch" in self.fields:
            self.fields["batch"].empty_label = "Ferme générale (sans lot spécifique)"
            if not self.instance.pk and "initial" not in kwargs:
                active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
                if active_batch:
                    self.fields["batch"].initial = active_batch

        if "formula" in self.fields:
            self.fields["formula"].empty_label = "-- Sélectionner une formule enregistrée ou saisir librement --"
            self.fields["formula"].required = False

        if not self.instance.pk:
            if not self.initial.get("date"):
                self.fields["date"].initial = timezone.localdate()
            if not self.initial.get("commercial_bag_reference_price"):
                self.fields["commercial_bag_reference_price"] = forms.DecimalField(
                    initial=Decimal("18000.00"),
                    widget=forms.NumberInput(attrs={"class": "form-control", "step": "500", "min": "0", "id": "id_prep_ref_bag_price"})
                )
            if not self.initial.get("total_produced_kg"):
                self.fields["total_produced_kg"].initial = Decimal("300.00")

