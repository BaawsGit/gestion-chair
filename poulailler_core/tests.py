from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .finance import batch_financials
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


class BatchWorkflowTests(TestCase):
    def setUp(self):
        self.start_date = timezone.localdate() - timedelta(days=9)
        self.batch = Batch.objects.create(
            name="Lot test",
            start_date=self.start_date,
            initial_quantity=100,
            unit_cost_chick=Decimal("2.50"),
        )

    def test_age_stock_and_mortality_rate(self):
        Mortality.objects.create(batch=self.batch, date=self.start_date, count=2)
        Sale.objects.create(
            batch=self.batch,
            date=timezone.localdate(),
            quantity_sold=10,
            unit_price=Decimal("12.00"),
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.current_age_days, 10)
        self.assertEqual(self.batch.current_stock, 88)
        self.assertEqual(self.batch.mortality_rate, Decimal("2.00"))
        self.assertEqual(self.batch.sales.get().total_amount, Decimal("120.00"))

    def test_batch_creation_generates_treatments_from_program(self):
        program = ProphylaxisProgram.objects.create(
            name="Vaccin test",
            target_day=10,
            medicine_name="Vaccin",
            administration_route="Eau",
        )
        treatment = TreatmentLog.objects.get(batch=self.batch, program=program, target_day=10)
        self.assertEqual(treatment.date_planned, self.start_date + timedelta(days=9))

    def test_completed_treatment_date_is_not_rewritten(self):
        program = ProphylaxisProgram.objects.create(
            name="Soin terminé",
            target_day=3,
            medicine_name="Produit",
        )
        treatment = TreatmentLog.objects.get(batch=self.batch, program=program, target_day=3)
        treatment.is_completed = True
        treatment.date_administered = timezone.localdate()
        treatment.save()
        new_start = self.start_date + timedelta(days=2)
        self.batch.start_date = new_start
        self.batch.save(update_fields=["start_date"])
        treatment.refresh_from_db()
        self.assertEqual(treatment.date_planned, self.start_date + timedelta(days=2))
        self.assertTrue(treatment.is_completed)

    def test_stock_summary_converts_sacks_and_kilograms(self):
        FeedInventory.objects.create(
            feed_type=FeedType.STARTER,
            purchase_date=self.start_date,
            bags_purchased=2,
            bag_weight_kg=Decimal("25.00"),
            unit_price=Decimal("20.00"),
        )
        FeedConsumption.objects.create(
            batch=self.batch,
            date=timezone.localdate(),
            feed_type=FeedType.STARTER,
            bags_consumed=Decimal("0.500"),
        )
        FeedConsumption.objects.create(
            batch=self.batch,
            date=timezone.localdate(),
            feed_type=FeedType.STARTER,
            kg_consumed=Decimal("5.00"),
        )
        stock = FeedInventory.stock_summary()[FeedType.STARTER]
        self.assertEqual(stock["remaining_kg"], Decimal("32.50"))
        self.assertEqual(stock["remaining_bags"], Decimal("1.30"))

    def test_financials_include_chick_cost_and_sales(self):
        Sale.objects.create(
            batch=self.batch,
            date=timezone.localdate(),
            quantity_sold=10,
            unit_price=Decimal("12.00"),
        )
        financials = batch_financials(self.batch)
        self.assertEqual(financials["revenue"], Decimal("120.00"))
        self.assertEqual(financials["direct_costs"], Decimal("250.00"))
        self.assertEqual(financials["net"], Decimal("-130.00"))

    def test_setup_command_seeds_known_batch_without_fake_expenses(self):
        Batch.objects.all().delete()
        call_command(
            "setup_farm",
            initial_quantity=250,
            unit_cost=Decimal("1.25"),
            stdout=StringIO(),
        )
        batch = Batch.objects.get(name="Lot #1 - Octobre 2026")
        self.assertEqual(batch.start_date.isoformat(), "2026-10-07")
        self.assertEqual(batch.initial_quantity, 250)
        self.assertEqual(batch.mortalities.get().count, 1)
        self.assertEqual(batch.expenses.count(), 0)

    def test_dashboard_renders_for_authenticated_user(self):
        user = get_user_model().objects.create_user(username="farm-user", password="test-password")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lot test")
        self.assertContains(response, "mortality-labels")
        history_response = self.client.get("/lots/")
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, "Voir le bilan")

    def test_views_expenses_sales_stocks_and_treatments(self):
        user = get_user_model().objects.create_user(username="testadmin", password="password123")
        self.client.force_login(user)

        # Treatments view
        resp = self.client.get("/soins/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Calendrier de prophylaxie")

        # Expenses view
        resp = self.client.get("/depenses/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Livre des dépenses")

        # Sales view
        resp = self.client.get("/ventes/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Historique des ventes")

        # Stocks view
        resp = self.client.get("/stocks/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Disponibilités en stock")

    def test_close_batch_sets_end_date(self):
        user = get_user_model().objects.create_user(username="closer", password="password123")
        self.client.force_login(user)
        resp = self.client.post(f"/lots/{self.batch.pk}/cloturer/")
        self.assertEqual(resp.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.CLOSED)
        self.assertIsNotNone(self.batch.end_date)

    def test_setup_farm_with_initial_expenses(self):
        Batch.objects.all().delete()
        call_command(
            "setup_farm",
            initial_quantity=500,
            unit_cost=Decimal("500.00"),
            with_initial_expenses=True,
            stdout=StringIO(),
        )
        batch = Batch.objects.get(name="Lot #1 - Octobre 2026")
        self.assertEqual(batch.initial_quantity, 500)
        self.assertGreater(batch.expenses.count(), 0)
        self.assertTrue(FeedInventory.objects.exists())

    def test_daily_entry_create_modify_and_delete(self):
        user = get_user_model().objects.create_user(username="journaluser", password="password123")
        self.client.force_login(user)

        entry_date = self.start_date + timedelta(days=1)
        # 1. Create entry
        resp = self.client.post("/journal/", {
            "date": entry_date.isoformat(),
            "mortality_count": 2,
            "cause_suspected": Mortality.Cause.DISEASE,
            "mortality_notes": "Signes de faiblesse",
            "feed_type": FeedType.STARTER,
            "bags_consumed": "0.500",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Mortality.objects.filter(batch=self.batch, date=entry_date).count(), 1)
        self.assertEqual(Mortality.objects.get(batch=self.batch, date=entry_date).count, 2)
        self.assertEqual(FeedConsumption.objects.get(batch=self.batch, date=entry_date).bags_consumed, Decimal("0.500"))

        # 2. Inspect edit page
        resp_get = self.client.get(f"/journal/?date={entry_date.isoformat()}")
        self.assertEqual(resp_get.status_code, 200)
        self.assertTrue(resp_get.context["is_editing"])

        # 3. Modify entry (update mortality to 5 and change bags to 1.0)
        resp_update = self.client.post("/journal/", {
            "date": entry_date.isoformat(),
            "mortality_count": 5,
            "cause_suspected": Mortality.Cause.DISEASE,
            "mortality_notes": "Mise à jour constatée",
            "feed_type": FeedType.STARTER,
            "bags_consumed": "1.000",
        })
        self.assertEqual(resp_update.status_code, 302)
        # Verify no duplicate entries created
        self.assertEqual(Mortality.objects.filter(batch=self.batch, date=entry_date).count(), 1)
        self.assertEqual(Mortality.objects.get(batch=self.batch, date=entry_date).count, 5)
        self.assertEqual(FeedConsumption.objects.get(batch=self.batch, date=entry_date).bags_consumed, Decimal("1.000"))

        # 4. Delete entry
        resp_del = self.client.post(f"/journal/supprimer/{entry_date.isoformat()}/")
        self.assertEqual(resp_del.status_code, 302)
        self.assertFalse(Mortality.objects.filter(batch=self.batch, date=entry_date).exists())
        self.assertFalse(FeedConsumption.objects.filter(batch=self.batch, date=entry_date).exists())

    def test_prophylaxis_daily_program_range_duplicate_and_clear(self):
        user = get_user_model().objects.create_user(username="proguser", password="password123")
        self.client.force_login(user)

        # 1. Access 45-day timetable view
        resp = self.client.get("/prophylaxie/programme/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Programme de Médicaments (J1 à J45)")
        self.assertEqual(len(resp.context["calendar_days"]), 45)

        # 2. Add medication for range J1 to J2: Tetracolivit
        resp_post = self.client.post("/prophylaxie/programme/nouveau/", {
            "start_day": 1,
            "end_day": 2,
            "medicine_name": "Tétracolivit",
            "administration_route": "Eau de boisson",
            "dosage": "1g / litre",
            "notes": "Matin",
        })
        self.assertEqual(resp_post.status_code, 302)
        self.assertTrue(ProphylaxisProgram.objects.filter(target_day=1, medicine_name="Tétracolivit").exists())
        self.assertTrue(ProphylaxisProgram.objects.filter(target_day=2, medicine_name="Tétracolivit").exists())

        # 3. Duplicate day 2 to day 3
        resp_dup = self.client.get("/prophylaxie/programme/jour/2/dupliquer/")
        self.assertEqual(resp_dup.status_code, 302)
        self.assertTrue(ProphylaxisProgram.objects.filter(target_day=3, medicine_name="Tétracolivit").exists())

        # 4. Clear day 1
        resp_clear = self.client.post("/prophylaxie/programme/jour/1/effacer/")
        self.assertEqual(resp_clear.status_code, 302)
        self.assertFalse(ProphylaxisProgram.objects.filter(target_day=1).exists())

    def test_batch_ordered_and_bonus_quantities_and_financials(self):
        batch = Batch.objects.create(
            name="Lot 400 Sujets + 8 Bonus",
            start_date=timezone.localdate(),
            ordered_quantity=400,
            bonus_quantity=8,
            unit_cost_chick=Decimal("750.00"),
        )
        self.assertEqual(batch.initial_quantity, 408)
        self.assertEqual(batch.chick_cost, Decimal("300000.00"))
        self.assertEqual(batch.effective_unit_cost, Decimal("735.29"))

        financials = batch_financials(batch)
        self.assertEqual(financials["direct_costs"], Decimal("300000.00"))

    def test_batch_create_and_edit_views(self):
        user = get_user_model().objects.create_user(username="batchmgr", password="password123")
        self.client.force_login(user)

        # 1. Create a new batch
        resp_create = self.client.post("/lots/nouveau/", {
            "name": "Lot Test Création",
            "start_date": timezone.localdate().isoformat(),
            "target_duration_days": 45,
            "ordered_quantity": 400,
            "bonus_quantity": 8,
            "unit_cost_chick": "750.00",
            "notes": "Test bonus",
        })
        self.assertEqual(resp_create.status_code, 302)
        created_batch = Batch.objects.get(name="Lot Test Création")
        self.assertEqual(created_batch.initial_quantity, 408)
        self.assertEqual(created_batch.ordered_quantity, 400)
        self.assertEqual(created_batch.bonus_quantity, 8)
        self.assertEqual(created_batch.chick_cost, Decimal("300000.00"))

        # 2. Edit the batch
        resp_edit = self.client.post(f"/lots/{created_batch.pk}/modifier/", {
            "name": "Lot Test Modifié",
            "start_date": timezone.localdate().isoformat(),
            "target_duration_days": 42,
            "ordered_quantity": 500,
            "bonus_quantity": 10,
            "unit_cost_chick": "700.00",
            "notes": "Modifié",
        })
        self.assertEqual(resp_edit.status_code, 302)
        created_batch.refresh_from_db()
        self.assertEqual(created_batch.name, "Lot Test Modifié")
        self.assertEqual(created_batch.ordered_quantity, 500)
        self.assertEqual(created_batch.bonus_quantity, 10)
        self.assertEqual(created_batch.initial_quantity, 510)
        self.assertEqual(created_batch.chick_cost, Decimal("350000.00"))

        # 3. Check Expense is created with category Poussins and correct amount
        chick_exp = Expense.objects.get(batch=created_batch, category__name="Poussins")
        self.assertEqual(chick_exp.amount, Decimal("350000.00"))

        # 4. Expense list displays this expense
        resp_expenses = self.client.get(f"/depenses/?batch={created_batch.pk}")
        self.assertEqual(resp_expenses.status_code, 200)
        self.assertContains(resp_expenses, "350000")
        self.assertContains(resp_expenses, "Poussins")



