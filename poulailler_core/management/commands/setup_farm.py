import sys
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from poulailler_core.models import (
    Batch,
    Expense,
    ExpenseCategory,
    FeedInventory,
    FeedType,
    Mortality,
    ProphylaxisProgram,
)


CATEGORIES = [
    ("Investissement / Infrastructure", True),
    ("Poussins", False),
    ("Aliment", False),
    ("Vétérinaire / Médicaments", False),
    ("Litière (copeaux)", False),
    ("Énergie / Chauffage", False),
    ("Main-d'œuvre", False),
    ("Divers", False),
]

PROTOCOL = [
    {
        "name": "Vitamines / électrolytes",
        "target_day": 1,
        "duration_days": 5,
        "frequency_days": 1,
        "medicine_name": "Vitamines / électrolytes",
        "administration_route": "Eau de boisson",
    },
    {
        "name": "Vaccination Gumboro",
        "target_day": 10,
        "medicine_name": "Vaccin Gumboro",
        "administration_route": "Eau de boisson",
    },
    {
        "name": "Vaccination Newcastle",
        "target_day": 14,
        "medicine_name": "Vaccin Newcastle",
        "administration_route": "Selon notice du vaccin",
    },
]


class Command(BaseCommand):
    help = "Crée les catégories et protocoles de base, puis initialise le premier lot connu."

    def add_arguments(self, parser):
        parser.add_argument("--categories-only", action="store_true", help="Ne pas créer le lot de démarrage.")
        parser.add_argument("--ordered-quantity", type=int, help="Nombre de poussins commandés (facturés).")
        parser.add_argument("--bonus-quantity", type=int, default=0, help="Nombre de poussins bonus / ajout (offerts).")
        parser.add_argument("--initial-quantity", type=int, help="Nombre total de poussins démarrés (rétrocompatibilité).")
        parser.add_argument("--unit-cost", type=Decimal, help="Coût réel d'un poussin.")
        parser.add_argument("--no-input", action="store_true", help="Ne pas demander de saisie interactive.")
        parser.add_argument(
            "--with-initial-expenses",
            action="store_true",
            help="Initialiser également les dépenses préliminaires (tôles, matériel, poussins, aliment) et le stock de départ.",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            for name, is_investment in CATEGORIES:
                ExpenseCategory.objects.update_or_create(
                    name=name,
                    defaults={"is_investment": is_investment, "useful_life_months": 36},
                )
            for item in PROTOCOL:
                defaults = {**item, "notes": "Protocole indicatif à confirmer avec le vétérinaire et la notice du produit."}
                ProphylaxisProgram.objects.update_or_create(name=item["name"], defaults=defaults)

            self.stdout.write(self.style.SUCCESS("Catégories et protocoles prêts."))
            if options["categories_only"]:
                return
            self._create_initial_batch(options)

    def _create_initial_batch(self, options):
        batch_name = "Lot #1 - Octobre 2026"
        is_interactive = not options["no_input"] and sys.stdin.isatty()

        if Batch.objects.filter(name=batch_name).exists():
            batch = Batch.objects.get(name=batch_name)
            if not batch.mortalities.filter(date=date(2026, 10, 7), count=1).exists():
                Mortality.objects.create(
                    batch=batch,
                    date=date(2026, 10, 7),
                    count=1,
                    cause_suspected=Mortality.Cause.UNKNOWN,
                    notes="Décès signalé le jour de l'arrivée.",
                )
            self.stdout.write(self.style.WARNING(f"Le lot {batch_name} existait déjà ; aucune valeur du lot n'a été modifiée."))
        else:
            ordered_qty = options.get("ordered_quantity")
            bonus_qty = options.get("bonus_quantity") or 0
            if ordered_qty is None:
                initial_qty = options.get("initial_quantity")
                if initial_qty is not None:
                    ordered_qty = initial_qty
                elif is_interactive:
                    ordered_qty = self._ask_integer("Nombre de poussins commandés (facturés)", minimum=1)
                    bonus_qty = self._ask_integer("Nombre de poussins bonus / ajout (offerts, 0 FCFA)", minimum=0)
                else:
                    ordered_qty = 400
                    bonus_qty = 8

            if ordered_qty + bonus_qty < 1:
                raise CommandError("L'effectif initial (commandés + bonus) doit être supérieur ou égal à 1.")

            unit_cost = options["unit_cost"]
            if unit_cost is None:
                if is_interactive:
                    unit_cost = self._ask_decimal("Coût unitaire réel du poussin", minimum=Decimal("0"))
                else:
                    unit_cost = Decimal("750.00")

            if unit_cost < 0:
                raise CommandError("Le coût unitaire ne peut pas être négatif.")

            batch = Batch.objects.create(
                name=batch_name,
                start_date=date(2026, 10, 7),
                target_duration_days=45,
                ordered_quantity=ordered_qty,
                bonus_quantity=bonus_qty,
                initial_quantity=ordered_qty + bonus_qty,
                unit_cost_chick=unit_cost,
            )
            Mortality.objects.create(
                batch=batch,
                date=date(2026, 10, 7),
                count=1,
                cause_suspected=Mortality.Cause.UNKNOWN,
                notes="Décès signalé le jour de l'arrivée.",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Lot initial créé : {batch} ({ordered_qty} commandés + {bonus_qty} bonus = {batch.initial_quantity} à {unit_cost} FCFA/sujet facturé)."
                )
            )

        if options.get("with_initial_expenses"):
            self._seed_initial_expenses(batch)

    def _seed_initial_expenses(self, batch):
        cat_immo = ExpenseCategory.objects.get(name="Investissement / Infrastructure")
        cat_chicks = ExpenseCategory.objects.get(name="Poussins")
        cat_feed = ExpenseCategory.objects.get(name="Aliment")
        cat_vet = ExpenseCategory.objects.get(name="Vétérinaire / Médicaments")
        cat_litter = ExpenseCategory.objects.get(name="Litière (copeaux)")

        # 1. Investissements préliminaires (charges fixes poulailler)
        Expense.objects.get_or_create(
            designation="Rénovation du poulailler, tôles et toiture",
            defaults={
                "batch": None,
                "category": cat_immo,
                "date": date(2026, 9, 28),
                "amount": Decimal("150000.00"),
            },
        )
        Expense.objects.get_or_create(
            designation="Matériel d'élevage (mangeoires, abreuvoirs, radiants)",
            defaults={
                "batch": None,
                "category": cat_immo,
                "date": date(2026, 10, 1),
                "amount": Decimal("45000.00"),
            },
        )

        # 2. Charges variables du lot
        chick_label = f"Achat des {batch.ordered_quantity} poussins d'un jour"
        if batch.bonus_quantity > 0:
            chick_label += f" (+{batch.bonus_quantity} bonus gratuits)"
        Expense.objects.get_or_create(
            batch=batch,
            category=cat_chicks,
            designation=chick_label,
            defaults={
                "date": date(2026, 10, 7),
                "amount": (Decimal(batch.ordered_quantity) * batch.unit_cost_chick).quantize(Decimal("0.01")),
            },
        )
        Expense.objects.get_or_create(
            batch=batch,
            category=cat_feed,
            designation="Aliment démarrage (5 sacs de 50 kg)",
            defaults={
                "date": date(2026, 10, 6),
                "amount": Decimal("115000.00"),
            },
        )
        Expense.objects.get_or_create(
            batch=batch,
            category=cat_vet,
            designation="Produits vétérinaires démarrage & vaccins (Gumboro, Newcastle)",
            defaults={
                "date": date(2026, 10, 6),
                "amount": Decimal("25000.00"),
            },
        )
        Expense.objects.get_or_create(
            batch=batch,
            category=cat_litter,
            designation="Litière de copeaux de bois (10 sacs)",
            defaults={
                "date": date(2026, 10, 5),
                "amount": Decimal("15000.00"),
            },
        )

        # 3. Stock initial d'aliment
        FeedInventory.objects.get_or_create(
            feed_type=FeedType.STARTER,
            purchase_date=date(2026, 10, 6),
            bags_purchased=5,
            defaults={
                "bag_weight_kg": Decimal("50.00"),
                "unit_price": Decimal("23000.00"),
                "supplier": "Fournisseur local",
                "notes": "Stock initial démarrage",
            },
        )
        self.stdout.write(self.style.SUCCESS("Dépenses préliminaires et stock initial d'aliment créés avec succès."))

    def _ask_integer(self, label, minimum):
        while True:
            try:
                value = int(input(f"{label} : "))
                if value >= minimum:
                    return value
            except ValueError:
                pass
            self.stderr.write(f"Saisissez un entier supérieur ou égal à {minimum}.")

    def _ask_decimal(self, label, minimum):
        while True:
            try:
                value = Decimal(input(f"{label} : ").replace(",", "."))
                if value.is_finite() and value >= minimum:
                    return value
            except (InvalidOperation, ValueError):
                pass
            self.stderr.write(f"Saisissez un montant supérieur ou égal à {minimum}.")
