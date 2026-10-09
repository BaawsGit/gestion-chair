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
    FeedInventory,
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
            "start_date": forms.DateInput(attrs={"type": "date"}),
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

    def clean(self):
        cleaned_data = super().clean()
        ordered = cleaned_data.get("ordered_quantity") or 0
        bonus = cleaned_data.get("bonus_quantity") or 0
        if ordered + bonus < 1:
            raise ValidationError("L'effectif total démarré (commandés + bonus) doit être au minimum de 1 poussin.")
        return cleaned_data


class ExpenseForm(StyledModelForm):
    class Meta:
        model = Expense
        fields = ["batch", "category", "date", "designation", "amount", "invoice_file"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class FeedInventoryForm(StyledModelForm):
    class Meta:
        model = FeedInventory
        fields = ["feed_type", "purchase_date", "bags_purchased", "bag_weight_kg", "unit_price", "supplier", "notes"]
        widgets = {"purchase_date": forms.DateInput(attrs={"type": "date"})}


class SaleForm(StyledModelForm):
    class Meta:
        model = Sale
        fields = ["batch", "date", "quantity_sold", "total_weight_kg", "unit_price", "total_amount", "customer_name", "payment_status", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean_quantity_sold(self):
        quantity = self.cleaned_data["quantity_sold"]
        batch = self.cleaned_data.get("batch")
        if batch:
            available = batch.current_stock
            if self.instance.pk:
                available += self.instance.quantity_sold
            if quantity > available:
                raise ValidationError(f"La quantité dépasse l'effectif disponible ({available}).")
        return quantity


class DailyEventForm(forms.Form):
    date = forms.DateField(
        label="Date", initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
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
        bags = cleaned.get("bags_consumed")
        kilograms = cleaned.get("kg_consumed")
        feed_type = cleaned.get("feed_type")
        if feed_type and (bags is None) == (kilograms is None):
            raise ValidationError("Renseignez soit le nombre de sacs, soit le poids distribué en kg.")
        if not feed_type and (bags is not None or kilograms is not None):
            raise ValidationError("Choisissez un type d'aliment pour enregistrer la distribution.")
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
            "date_planned": forms.DateInput(attrs={"type": "date"}),
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
