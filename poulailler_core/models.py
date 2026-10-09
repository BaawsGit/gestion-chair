from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone


class Batch(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "En cours"
        CLOSED = "closed", "Clôturé"
        ARCHIVED = "archived", "Archivé"

    name = models.CharField("nom du lot", max_length=120, unique=True)
    start_date = models.DateField("date d'arrivée")
    end_date = models.DateField("date de clôture", null=True, blank=True)
    target_duration_days = models.PositiveSmallIntegerField(
        "durée prévue (jours)", default=45, validators=[MinValueValidator(1)]
    )
    ordered_quantity = models.PositiveIntegerField(
        "poussins commandés (facturés)", default=0, validators=[MinValueValidator(0)]
    )
    bonus_quantity = models.PositiveIntegerField(
        "poussins bonus / ajout (offerts)", default=0, validators=[MinValueValidator(0)]
    )
    initial_quantity = models.PositiveIntegerField("effectif démarré / reçus")
    unit_cost_chick = models.DecimalField(
        "coût unitaire du poussin", max_digits=12, decimal_places=2,
        default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    status = models.CharField(
        "statut", max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    notes = models.TextField("notes", blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(target_duration_days__gt=0), name="batch_duration_positive"),
            models.CheckConstraint(condition=Q(unit_cost_chick__gte=0), name="batch_chick_cost_nonnegative"),
        ]
        verbose_name = "lot"
        verbose_name_plural = "lots"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.ordered_quantity or self.bonus_quantity:
            self.initial_quantity = (self.ordered_quantity or 0) + (self.bonus_quantity or 0)
        elif self.initial_quantity and not self.ordered_quantity:
            self.ordered_quantity = self.initial_quantity
        super().save(*args, **kwargs)

    @property
    def chick_cost(self):
        """Coût total des poussins facturés = quantité commandée x prix unitaire."""
        billed_qty = self.ordered_quantity if self.ordered_quantity > 0 else (self.initial_quantity - self.bonus_quantity)
        return (Decimal(max(billed_qty, 0)) * self.unit_cost_chick).quantize(Decimal("0.01"))

    @property
    def effective_unit_cost(self):
        """Coût moyen réel par poussin démarré (tenant compte des bonus gratuits)."""
        if not self.initial_quantity:
            return self.unit_cost_chick
        return (self.chick_cost / Decimal(self.initial_quantity)).quantize(Decimal("0.01"))

    @property
    def current_age_days(self):
        ref_date = self.end_date if (self.status == self.Status.CLOSED and self.end_date) else timezone.localdate()
        return max((ref_date - self.start_date).days + 1, 0)

    @property
    def deaths_count(self):
        return self.mortalities.aggregate(total=Sum("count"))["total"] or 0

    @property
    def sold_count(self):
        return self.sales.aggregate(total=Sum("quantity_sold"))["total"] or 0

    @property
    def current_stock(self):
        return self.initial_quantity - self.deaths_count - self.sold_count

    @property
    def mortality_rate(self):
        if not self.initial_quantity:
            return Decimal("0.00")
        return (Decimal(self.deaths_count) * 100 / Decimal(self.initial_quantity)).quantize(Decimal("0.01"))


class ExpenseCategory(models.Model):
    name = models.CharField("catégorie", max_length=80, unique=True)
    is_investment = models.BooleanField("immobilisation", default=False)
    useful_life_months = models.PositiveSmallIntegerField(
        "durée d'amortissement (mois)", default=36,
        validators=[MinValueValidator(1)]
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie de dépense"
        verbose_name_plural = "catégories de dépenses"

    def __str__(self):
        return self.name


class Expense(models.Model):
    batch = models.ForeignKey(
        Batch, verbose_name="lot", related_name="expenses", null=True, blank=True,
        on_delete=models.PROTECT, help_text="Laisser vide pour une dépense fixe de l'exploitation."
    )
    category = models.ForeignKey(
        ExpenseCategory, verbose_name="catégorie", related_name="expenses",
        on_delete=models.PROTECT
    )
    date = models.DateField("date")
    designation = models.CharField("désignation", max_length=180)
    amount = models.DecimalField(
        "montant", max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    invoice_file = models.FileField("facture", upload_to="invoices/%Y/%m/", blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]
        indexes = [models.Index(fields=["batch", "date"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive")
        ]
        verbose_name = "dépense"
        verbose_name_plural = "dépenses"

    def __str__(self):
        return f"{self.designation} - {self.amount}"


class Mortality(models.Model):
    class Cause(models.TextChoices):
        DISEASE = "disease", "Maladie"
        CRUSHING = "crushing", "Écrasement"
        UNKNOWN = "unknown", "Inconnu"
        OTHER = "other", "Autre"

    batch = models.ForeignKey(Batch, verbose_name="lot", related_name="mortalities", on_delete=models.PROTECT)
    date = models.DateField("date")
    count = models.PositiveSmallIntegerField("nombre de morts", validators=[MinValueValidator(1)])
    cause_suspected = models.CharField(
        "cause présumée", max_length=12, choices=Cause.choices, default=Cause.UNKNOWN
    )
    notes = models.TextField("notes", blank=True)

    class Meta:
        ordering = ["-date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(count__gt=0), name="mortality_count_positive")
        ]
        indexes = [models.Index(fields=["batch", "date"])]
        verbose_name = "mortalité"
        verbose_name_plural = "mortalités"

    def __str__(self):
        return f"{self.batch} - {self.date} : {self.count} mort(s)"


class FeedType(models.TextChoices):
    STARTER = "starter", "Démarrage"
    GROWER = "grower", "Croissance"
    FINISHER = "finisher", "Finition"


class FeedInventory(models.Model):
    feed_type = models.CharField("type d'aliment", max_length=10, choices=FeedType.choices)
    purchase_date = models.DateField("date d'achat")
    bags_purchased = models.PositiveIntegerField("sacs achetés", validators=[MinValueValidator(1)])
    bag_weight_kg = models.DecimalField(
        "poids par sac (kg)", max_digits=7, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    unit_price = models.DecimalField(
        "prix du sac", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    supplier = models.CharField("fournisseur", max_length=120, blank=True)
    notes = models.TextField("notes", blank=True)

    class Meta:
        ordering = ["-purchase_date", "feed_type"]
        constraints = [
            models.CheckConstraint(condition=Q(bags_purchased__gt=0), name="feed_bags_positive"),
            models.CheckConstraint(condition=Q(bag_weight_kg__gt=0), name="feed_bag_weight_positive"),
            models.CheckConstraint(condition=Q(unit_price__gt=0), name="feed_unit_price_positive"),
        ]
        verbose_name = "entrée de stock d'aliment"
        verbose_name_plural = "stocks d'aliments"

    def __str__(self):
        return f"{self.get_feed_type_display()} - {self.bags_purchased} sacs ({self.purchase_date})"

    @classmethod
    def stock_summary(cls):
        summary = {}
        for feed_type, label in FeedType.choices:
            purchases = list(cls.objects.filter(feed_type=feed_type).values_list("bags_purchased", "bag_weight_kg"))
            purchased_bags = sum((Decimal(bags) for bags, _ in purchases), Decimal("0"))
            purchased_kg = sum((Decimal(bags) * weight for bags, weight in purchases), Decimal("0"))
            average_bag_weight = purchased_kg / purchased_bags if purchased_bags else Decimal("0")
            consumption = FeedConsumption.objects.filter(feed_type=feed_type).aggregate(
                bags=Sum("bags_consumed"), kilograms=Sum("kg_consumed")
            )
            used_kg = consumption["kilograms"] or Decimal("0")
            used_kg += (consumption["bags"] or Decimal("0")) * average_bag_weight
            remaining_kg = purchased_kg - used_kg
            summary[feed_type] = {
                "label": label,
                "remaining_kg": remaining_kg.quantize(Decimal("0.01")),
                "remaining_bags": (remaining_kg / average_bag_weight).quantize(Decimal("0.01")) if average_bag_weight else Decimal("0.00"),
            }
        return summary


class FeedConsumption(models.Model):
    batch = models.ForeignKey(Batch, verbose_name="lot", related_name="feed_consumptions", on_delete=models.PROTECT)
    date = models.DateField("date")
    feed_type = models.CharField("type d'aliment", max_length=10, choices=FeedType.choices)
    bags_consumed = models.DecimalField(
        "sacs consommés", max_digits=9, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.001"))]
    )
    kg_consumed = models.DecimalField(
        "kilogrammes consommés", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    notes = models.CharField("notes", max_length=200, blank=True)

    class Meta:
        ordering = ["-date", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(bags_consumed__gt=0, kg_consumed__isnull=True) | Q(kg_consumed__gt=0, bags_consumed__isnull=True)),
                name="feed_consumption_one_positive_measure"
            )
        ]
        indexes = [models.Index(fields=["batch", "date"])]
        verbose_name = "consommation d'aliment"
        verbose_name_plural = "consommations d'aliment"

    def __str__(self):
        quantity = f"{self.bags_consumed} sac(s)" if self.bags_consumed else f"{self.kg_consumed} kg"
        return f"{self.batch} - {self.date} : {quantity}"


class ProphylaxisProgram(models.Model):
    name = models.CharField("soin / protocole", max_length=120)
    target_day = models.PositiveSmallIntegerField("jour cible", validators=[MinValueValidator(1)])
    duration_days = models.PositiveSmallIntegerField("durée (jours)", default=1, validators=[MinValueValidator(1)])
    frequency_days = models.PositiveSmallIntegerField("intervalle (jours)", default=1, validators=[MinValueValidator(1)])
    medicine_name = models.CharField("produit", max_length=120)
    dosage = models.CharField("dosage", max_length=120, blank=True)
    administration_route = models.CharField("voie d'administration", max_length=80, default="Eau de boisson")
    is_active = models.BooleanField("actif", default=True)
    notes = models.TextField("notes", blank=True)

    class Meta:
        ordering = ["target_day", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(target_day__gt=0), name="prophylaxis_target_day_positive"),
            models.CheckConstraint(condition=Q(duration_days__gt=0), name="prophylaxis_duration_positive"),
            models.CheckConstraint(condition=Q(frequency_days__gt=0), name="prophylaxis_frequency_positive"),
        ]
        verbose_name = "protocole prophylactique"
        verbose_name_plural = "protocoles prophylactiques"

    def __str__(self):
        return f"J{self.target_day} - {self.name}"


class TreatmentLog(models.Model):
    batch = models.ForeignKey(Batch, verbose_name="lot", related_name="treatments", on_delete=models.PROTECT)
    program = models.ForeignKey(
        ProphylaxisProgram, verbose_name="protocole", related_name="logs",
        null=True, blank=True, on_delete=models.SET_NULL
    )
    target_day = models.PositiveSmallIntegerField("jour d'âge ciblé", validators=[MinValueValidator(1)])
    date_planned = models.DateField("date prévue")
    date_administered = models.DateField("date administrée", null=True, blank=True)
    medicine_name = models.CharField("médicament / soin", max_length=120)
    dosage = models.CharField("dosage", max_length=120, blank=True)
    administration_route = models.CharField("voie d'administration", max_length=80, blank=True)
    is_completed = models.BooleanField("effectué", default=False)
    notes = models.TextField("notes", blank=True)

    class Meta:
        ordering = ["date_planned", "target_day", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(target_day__gt=0), name="treatment_target_day_positive"),
            models.UniqueConstraint(fields=["batch", "program", "target_day"], name="unique_batch_program_day"),
        ]
        indexes = [models.Index(fields=["batch", "date_planned", "is_completed"])]
        verbose_name = "suivi de soin"
        verbose_name_plural = "suivis de soins"

    def __str__(self):
        return f"{self.batch} - J{self.target_day} : {self.medicine_name}"


class Sale(models.Model):
    class PaymentStatus(models.TextChoices):
        PAID = "paid", "Payé"
        CREDIT = "credit", "Crédit / avance"

    batch = models.ForeignKey(Batch, verbose_name="lot", related_name="sales", on_delete=models.PROTECT)
    date = models.DateField("date")
    quantity_sold = models.PositiveIntegerField("quantité vendue", validators=[MinValueValidator(1)])
    total_weight_kg = models.DecimalField(
        "poids total (kg)", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    unit_price = models.DecimalField(
        "prix unitaire", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    total_amount = models.DecimalField(
        "montant total", max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    customer_name = models.CharField("client", max_length=160, blank=True)
    payment_status = models.CharField(
        "paiement", max_length=8, choices=PaymentStatus.choices, default=PaymentStatus.PAID
    )
    notes = models.TextField("notes", blank=True)

    class Meta:
        ordering = ["-date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity_sold__gt=0), name="sale_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gt=0), name="sale_unit_price_positive"),
            models.CheckConstraint(condition=Q(total_amount__isnull=True) | Q(total_amount__gt=0), name="sale_total_positive_or_empty"),
        ]
        indexes = [models.Index(fields=["batch", "date"])]
        verbose_name = "vente"
        verbose_name_plural = "ventes"

    def save(self, *args, **kwargs):
        if self.total_amount is None:
            self.total_amount = (self.unit_price * self.quantity_sold).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.batch} - {self.quantity_sold} poulet(s) - {self.total_amount}"
