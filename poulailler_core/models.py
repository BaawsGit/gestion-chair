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
    class PurchaseMode(models.TextChoices):
        BAGS = "bags", "En sac(s)"
        RETAIL = "retail", "Au détail (en kg)"

    batch = models.ForeignKey(
        Batch, verbose_name="lot", related_name="feed_inventories",
        null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Lot concerné (laisser vide pour les charges fixes / stock général de la ferme)."
    )
    expense = models.OneToOneField(
        Expense, verbose_name="dépense associée", related_name="feed_inventory",
        null=True, blank=True, on_delete=models.CASCADE
    )
    feed_type = models.CharField("type d'aliment", max_length=10, choices=FeedType.choices)
    purchase_mode = models.CharField(
        "mode d'achat", max_length=10, choices=PurchaseMode.choices, default=PurchaseMode.BAGS
    )
    purchase_date = models.DateField("date d'achat")
    bags_purchased = models.PositiveIntegerField(
        "sacs achetés", null=True, blank=True, validators=[MinValueValidator(1)]
    )
    bag_weight_kg = models.DecimalField(
        "poids par sac (kg)", max_digits=7, decimal_places=2,
        null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))]
    )
    quantity_kg = models.DecimalField(
        "quantité (kg)", max_digits=10, decimal_places=2,
        null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))]
    )
    unit_price = models.DecimalField(
        "prix unitaire (sac ou kg)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Prix d'un sac (en mode sac) ou prix par kg (en mode détail)."
    )
    supplier = models.CharField("fournisseur", max_length=120, blank=True)
    notes = models.TextField("notes", blank=True)

    @property
    def total_weight_kg(self):
        if self.purchase_mode == self.PurchaseMode.RETAIL:
            return self.quantity_kg or Decimal("0.00")
        bags = Decimal(self.bags_purchased or 0)
        weight = self.bag_weight_kg or Decimal("50.00")
        return (bags * weight).quantize(Decimal("0.01"))

    @property
    def total_cost(self):
        if self.purchase_mode == self.PurchaseMode.RETAIL:
            qty = self.quantity_kg or Decimal("0.00")
            return (qty * self.unit_price).quantize(Decimal("0.01"))
        bags = Decimal(self.bags_purchased or 0)
        return (bags * self.unit_price).quantize(Decimal("0.01"))

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        if self.purchase_mode == self.PurchaseMode.RETAIL:
            if not self.quantity_kg or self.quantity_kg <= 0:
                raise ValidationError({"quantity_kg": "Veuillez renseigner le poids acheté en kg au détail."})
        else:
            if not self.bags_purchased or self.bags_purchased <= 0:
                raise ValidationError({"bags_purchased": "Veuillez renseigner le nombre de sacs achetés."})
            if not self.bag_weight_kg or self.bag_weight_kg <= 0:
                raise ValidationError({"bag_weight_kg": "Veuillez renseigner le poids d'un sac en kg."})

    class Meta:
        ordering = ["-purchase_date", "feed_type"]
        constraints = [
            models.CheckConstraint(condition=Q(unit_price__gt=0), name="feed_unit_price_positive"),
        ]
        verbose_name = "entrée de stock d'aliment"
        verbose_name_plural = "stocks d'aliments"

    def __str__(self):
        if self.purchase_mode == self.PurchaseMode.RETAIL:
            return f"{self.get_feed_type_display()} - {self.quantity_kg} kg ({self.purchase_date})"
        return f"{self.get_feed_type_display()} - {self.bags_purchased} sacs ({self.purchase_date})"

    @classmethod
    def stock_summary(cls):
        summary = {}
        for feed_type, label in FeedType.choices:
            purchases = cls.objects.filter(feed_type=feed_type)
            purchased_kg = Decimal("0.00")
            total_bag_kg = Decimal("0.00")
            total_bags_count = Decimal("0.00")

            for p in purchases:
                purchased_kg += p.total_weight_kg
                if p.purchase_mode == cls.PurchaseMode.BAGS and p.bags_purchased and p.bag_weight_kg:
                    total_bags_count += Decimal(p.bags_purchased)
                    total_bag_kg += Decimal(p.bags_purchased) * p.bag_weight_kg

            average_bag_weight = (total_bag_kg / total_bags_count) if total_bags_count > 0 else Decimal("50.00")

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


class FeedFormula(models.Model):
    name = models.CharField("nom de la formule", max_length=120, unique=True)
    feed_type = models.CharField("stade / type d'aliment", max_length=10, choices=FeedType.choices, default=FeedType.GROWER)
    target_total_kg = models.DecimalField(
        "poids total visé (kg)", max_digits=8, decimal_places=2, default=Decimal("300.00"),
        validators=[MinValueValidator(Decimal("1.00"))]
    )
    commercial_bag_reference_price = models.DecimalField(
        "prix repère sac 50kg du commerce (FCFA)", max_digits=10, decimal_places=2, default=Decimal("18000.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Prix moyen d'un sac de 50 kg industriel équivalent sur le marché pour estimer les économies."
    )
    notes = models.TextField("instructions & composition", blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["feed_type", "name"]
        verbose_name = "formule d'aliment"
        verbose_name_plural = "formules d'aliments"

    def __str__(self):
        return f"{self.name} ({self.get_feed_type_display()} · {self.target_total_kg:,.0f} kg)"

    @property
    def total_ingredients_count(self):
        return self.items.count()

    @property
    def theoretical_total_cost(self):
        return sum((item.theoretical_cost for item in self.items.all()), Decimal("0.00"))

    @property
    def theoretical_cost_per_kg(self):
        if self.target_total_kg > 0:
            return (self.theoretical_total_cost / self.target_total_kg).quantize(Decimal("0.01"))
        return Decimal("0.00")

    @property
    def theoretical_cost_per_50kg_bag(self):
        return (self.theoretical_cost_per_kg * Decimal("50.00")).quantize(Decimal("0.01"))


class FeedFormulaItem(models.Model):
    formula = models.ForeignKey(FeedFormula, verbose_name="formule", related_name="items", on_delete=models.CASCADE)
    ingredient_name = models.CharField("nom de l'ingrédient", max_length=100)
    quantity = models.DecimalField("quantité requise", max_digits=8, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit = models.CharField("unité", max_length=20, default="kg")
    default_unit_cost = models.DecimalField(
        "prix unitaire indicatif (FCFA)", max_digits=10, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Prix d'achat moyen (par kg, sachet ou litre)."
    )
    order = models.PositiveSmallIntegerField("ordre d'affichage", default=0)

    class Meta:
        ordering = ["order", "pk"]
        verbose_name = "ingrédient de formule"
        verbose_name_plural = "ingrédients de formule"

    def __str__(self):
        return f"{self.ingredient_name} ({self.quantity} {self.unit})"

    @property
    def theoretical_cost(self):
        return (self.quantity * self.default_unit_cost).quantize(Decimal("0.01"))


class FeedPreparation(models.Model):
    batch = models.ForeignKey(
        Batch, verbose_name="lot concerné", related_name="feed_preparations",
        null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Lot qui recevra et consommera cet aliment (ou ferme générale)."
    )
    formula = models.ForeignKey(
        FeedFormula, verbose_name="formule utilisée", related_name="preparations",
        null=True, blank=True, on_delete=models.SET_NULL
    )
    feed_type = models.CharField("type d'aliment produit", max_length=10, choices=FeedType.choices, default=FeedType.GROWER)
    date = models.DateField("date de préparation", default=timezone.localdate)
    total_produced_kg = models.DecimalField(
        "quantité finale produite (kg)", max_digits=8, decimal_places=2, default=Decimal("300.00"),
        validators=[MinValueValidator(Decimal("0.10"))],
        help_text="Nombre total de kilogrammes prêts à être distribués aux poulets."
    )
    milling_cost = models.DecimalField(
        "frais de meunier / broyage / mélange (FCFA)", max_digits=10, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    total_cost = models.DecimalField(
        "montant total décaissé (FCFA)", max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Total réel décaissé en caisse pour cette préparation (achats du jour + mouture)."
    )
    commercial_bag_reference_price = models.DecimalField(
        "prix repère sac 50kg du commerce (FCFA)", max_digits=10, decimal_places=2, default=Decimal("18000.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    expense = models.OneToOneField(
        Expense, verbose_name="dépense associée", related_name="feed_preparation",
        null=True, blank=True, on_delete=models.SET_NULL
    )
    feed_inventory = models.OneToOneField(
        FeedInventory, verbose_name="stock d'aliment associé", related_name="feed_preparation",
        null=True, blank=True, on_delete=models.SET_NULL
    )
    notes = models.TextField("notes & observations", blank=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]
        verbose_name = "préparation d'aliment"
        verbose_name_plural = "préparations d'aliments"

    def __str__(self):
        return f"Préparation {self.get_feed_type_display()} · {self.total_produced_kg:,.0f} kg du {self.date}"

    @property
    def cost_per_kg(self):
        if self.total_produced_kg > 0:
            return (self.total_cost / self.total_produced_kg).quantize(Decimal("0.01"))
        return Decimal("0.00")

    @property
    def cost_per_50kg_bag(self):
        return (self.cost_per_kg * Decimal("50.00")).quantize(Decimal("0.01"))

    @property
    def commercial_cost_equivalent(self):
        if self.commercial_bag_reference_price > 0 and self.total_produced_kg > 0:
            return ((self.total_produced_kg / Decimal("50.00")) * self.commercial_bag_reference_price).quantize(Decimal("0.01"))
        return Decimal("0.00")

    @property
    def savings_amount(self):
        return (self.commercial_cost_equivalent - self.total_cost).quantize(Decimal("0.01"))

    @property
    def savings_percent(self):
        if self.commercial_cost_equivalent > 0:
            return ((self.savings_amount / self.commercial_cost_equivalent) * Decimal("100.00")).quantize(Decimal("0.1"))
        return Decimal("0.0")


class FeedPreparationItem(models.Model):
    preparation = models.ForeignKey(FeedPreparation, verbose_name="session de préparation", related_name="items", on_delete=models.CASCADE)
    ingredient_name = models.CharField("nom de l'ingrédient", max_length=100)
    recipe_quantity = models.DecimalField("quantité incorporée", max_digits=8, decimal_places=3, null=True, blank=True)
    recipe_unit = models.CharField("unité incorporée", max_length=20, default="kg")
    purchased_today = models.BooleanField(
        "acheté aujourd'hui", default=True,
        help_text="Coché = achat décaissé ce jour. Décoché = pris sur réserve existante (0 FCFA)."
    )
    purchased_description = models.CharField("conditionnement acheté", max_length=150, blank=True, help_text="Ex: 180 kg, 1 sachet de 1 kg, 2 bidons de 1.5 L...")
    purchase_cost = models.DecimalField(
        "montant payé (FCFA)", max_digits=10, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    order = models.PositiveSmallIntegerField("ordre", default=0)

    class Meta:
        ordering = ["order", "pk"]
        verbose_name = "ingrédient de préparation"
        verbose_name_plural = "ingrédients de préparation"

    def __str__(self):
        return f"{self.ingredient_name} : {self.purchase_cost} FCFA"

