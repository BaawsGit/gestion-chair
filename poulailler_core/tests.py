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
        user = get_user_model().objects.create_user(username="closer", password="password123", is_staff=True)
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
        user = get_user_model().objects.create_user(username="journaluser", password="password123", is_staff=True)
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

    def test_daily_entry_multi_feed_transition_and_stock_shortage_notification(self):
        user = get_user_model().objects.create_user(username="transition_user", password="password123", is_staff=True)
        self.client.force_login(user)

        # 1. Provide small starter stock (10 kg) and grower stock (20 kg)
        FeedInventory.objects.create(
            batch=self.batch,
            feed_type=FeedType.STARTER,
            purchase_date=self.start_date,
            purchase_mode=FeedInventory.PurchaseMode.RETAIL,
            quantity_kg=Decimal("10.00"),
            unit_price=Decimal("400.00"),
        )
        FeedInventory.objects.create(
            batch=self.batch,
            feed_type=FeedType.GROWER,
            purchase_date=self.start_date,
            purchase_mode=FeedInventory.PurchaseMode.RETAIL,
            quantity_kg=Decimal("20.00"),
            unit_price=Decimal("400.00"),
        )

        entry_date = self.start_date + timedelta(days=5)

        # 2. Record daily entry with multiple feeds on same day:
        # Starter: 15 kg (which is > 10 kg available stock -> triggers stock shortage notification!)
        # Grower: 12 kg (which is <= 20 kg available stock)
        resp = self.client.post("/journal/", {
            "date": entry_date.isoformat(),
            "mortality_count": 0,
            "starter_kg": "15.00",
            "grower_kg": "12.00",
        }, follow=True)

        self.assertEqual(resp.status_code, 200)

        # Verify warning message notification in response
        messages_list = list(resp.context["messages"])
        warning_messages = [m.message for m in messages_list if "Alerte stock" in m.message or "Stock insuffisant" in m.message]
        self.assertTrue(len(warning_messages) >= 1)
        self.assertIn("Démarrage", warning_messages[0])

        # Verify database has TWO feed consumptions on this EXACT date for this batch
        consumptions = FeedConsumption.objects.filter(batch=self.batch, date=entry_date).order_by("feed_type")
        self.assertEqual(consumptions.count(), 2)

        starter_c = consumptions.get(feed_type=FeedType.STARTER)
        grower_c = consumptions.get(feed_type=FeedType.GROWER)
        self.assertEqual(starter_c.kg_consumed, Decimal("15.00"))
        self.assertEqual(grower_c.kg_consumed, Decimal("12.00"))

        # 3. Check edit view loads both feeds
        resp_edit = self.client.get(f"/journal/?date={entry_date.isoformat()}")
        self.assertEqual(resp_edit.status_code, 200)
        self.assertEqual(resp_edit.context["form"]["starter_kg"].value(), Decimal("15.00"))
        self.assertEqual(resp_edit.context["form"]["grower_kg"].value(), Decimal("12.00"))

    def test_expense_creation_medication_date_handling(self):
        user = get_user_model().objects.create_user(username="vetuser", password="password123", is_staff=True)
        self.client.force_login(user)

        from .models import ExpenseCategory
        cat_vet, _ = ExpenseCategory.objects.get_or_create(name="Vétérinaire / Médicaments")

        today_str = timezone.localdate().isoformat()
        resp = self.client.post("/depenses/nouvelle/", {
            "batch": self.batch.pk,
            "category": cat_vet.pk,
            "date": today_str,
            "designation": "Vaccin Gumboro",
            "amount": "4500",
        }, follow=True)

        self.assertEqual(resp.status_code, 200)
        expense = Expense.objects.filter(designation="Vaccin Gumboro").first()
        self.assertIsNotNone(expense)
        self.assertEqual(expense.amount, Decimal("4500.00"))
        self.assertEqual(expense.category, cat_vet)

    def test_prophylaxis_daily_program_range_duplicate_and_clear(self):
        user = get_user_model().objects.create_user(username="proguser", password="password123", is_staff=True)
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
        user = get_user_model().objects.create_user(username="batchmgr", password="password123", is_staff=True)
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
        self.assertContains(resp_expenses, "350\xa0000")
        self.assertContains(resp_expenses, "Poussins")

    def test_feed_purchase_creates_and_syncs_expense(self):
        user = get_user_model().objects.create_user(username="stockmgr", password="password123", is_staff=True)
        self.client.force_login(user)

        # 1. Add feed stock via form
        post_data = {
            "batch": self.batch.pk,
            "feed_type": FeedType.STARTER,
            "purchase_date": timezone.localdate().isoformat(),
            "bags_purchased": 4,
            "bag_weight_kg": "50.00",
            "unit_price": "22500.00",
            "supplier": "Provendeur Central",
            "notes": "4 sacs démarrage",
        }
        resp = self.client.post("/stocks/aliments/ajouter/", post_data)
        self.assertEqual(resp.status_code, 302)

        # 2. Verify FeedInventory was created
        inventory = FeedInventory.objects.get(supplier="Provendeur Central")
        self.assertEqual(inventory.bags_purchased, 4)
        self.assertEqual(inventory.total_cost, Decimal("90000.00"))
        self.assertIsNotNone(inventory.expense)

        # 3. Verify Expense was automatically created
        expense = inventory.expense
        self.assertEqual(expense.amount, Decimal("90000.00"))
        self.assertEqual(expense.category.name, "Aliment")
        self.assertEqual(expense.batch, self.batch)

        # 4. Verify Expense appears in Dépenses view
        resp_exp = self.client.get("/depenses/")
        self.assertEqual(resp_exp.status_code, 200)
        self.assertContains(resp_exp, "90\xa0000")
        self.assertContains(resp_exp, "Aliment")

        # 5. Verify Stocks view displays purchase with linked expense
        resp_stocks = self.client.get("/stocks/")
        self.assertEqual(resp_stocks.status_code, 200)
        self.assertContains(resp_stocks, "90\xa0000")
        self.assertContains(resp_stocks, "Liée")

        # 6. Verify Dashboard displays this expense in direct costs
        resp_dash = self.client.get("/")
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, "Aliment")

        # 7. Edit the feed purchase
        edit_data = post_data.copy()
        edit_data["bags_purchased"] = 6  # 6 * 22500 = 135000
        resp_edit = self.client.post(f"/stocks/aliments/{inventory.pk}/modifier/", edit_data)
        self.assertEqual(resp_edit.status_code, 302)
        inventory.refresh_from_db()
        self.assertEqual(inventory.bags_purchased, 6)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("135000.00"))

        # 8. Delete the feed purchase
        resp_del = self.client.post(f"/stocks/aliments/{inventory.pk}/supprimer/")
        self.assertEqual(resp_del.status_code, 302)
        self.assertFalse(FeedInventory.objects.filter(pk=inventory.pk).exists())
        self.assertFalse(Expense.objects.filter(pk=expense.pk).exists())

    def test_read_only_user_permissions(self):
        # Visitor account: is_staff=False, is_superuser=False
        readonly_user = get_user_model().objects.create_user(
            username="readonly_visitor", password="user123!", is_staff=False, is_superuser=False
        )
        self.client.force_login(readonly_user)

        # 1. Verification of read-only access on all consultation views (HTTP 200)
        consult_urls = [
            "/",
            "/lots/",
            "/journal/",
            "/soins/",
            "/depenses/",
            "/stocks/",
            "/ventes/",
            "/prophylaxie/programme/",
            f"/lots/{self.batch.pk}/bilan/",
        ]
        for url in consult_urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"Expected 200 for {url}")

        # 2. Blocked from mutations (redirected with warning, zero database changes)
        # Attempt to create a batch
        resp_batch = self.client.post("/lots/nouveau/", {
            "name": "Lot Non Autorisé",
            "start_date": timezone.localdate().isoformat(),
            "target_duration_days": 45,
            "ordered_quantity": 100,
            "bonus_quantity": 0,
            "unit_cost_chick": "500.00",
        }, follow=True)
        self.assertFalse(Batch.objects.filter(name="Lot Non Autorisé").exists())
        self.assertContains(resp_batch, "Action non autorisée")

        # Attempt daily entry
        resp_daily = self.client.post("/journal/", {
            "date": timezone.localdate().isoformat(),
            "mortality_count": 10,
            "cause_suspected": Mortality.Cause.DISEASE,
        }, follow=True)
        self.assertFalse(Mortality.objects.filter(count=10).exists())
        self.assertContains(resp_daily, "Action non autorisée")

        # Attempt close batch
        self.client.post(f"/lots/{self.batch.pk}/cloturer/")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.ACTIVE)

        # Attempt feed stock purchase
        self.client.post("/stocks/aliments/ajouter/", {
            "batch": self.batch.pk,
            "feed_type": FeedType.STARTER,
            "purchase_date": timezone.localdate().isoformat(),
            "bags_purchased": 5,
            "bag_weight_kg": "50.00",
            "unit_price": "20000.00",
        })
        self.assertFalse(FeedInventory.objects.filter(bags_purchased=5).exists())

        # Attempt new expense
        self.client.post("/depenses/nouvelle/", {
            "designation": "Dépense interdite",
            "amount": "15000.00",
            "date": timezone.localdate().isoformat(),
        })
        self.assertFalse(Expense.objects.filter(designation="Dépense interdite").exists())

        # Attempt new sale
        self.client.post("/ventes/nouvelle/", {
            "batch": self.batch.pk,
            "date": timezone.localdate().isoformat(),
            "quantity_sold": 20,
            "unit_price": "3000.00",
        })
        self.assertFalse(Sale.objects.filter(quantity_sold=20).exists())

    def test_feed_purchase_retail_mode_creates_stock_and_expense(self):
        user = get_user_model().objects.create_user(username="retail_admin", password="password123", is_staff=True)
        self.client.force_login(user)

        # 1. Add 10 kg at 1,000 FCFA/kg = 10,000 FCFA
        resp = self.client.post("/stocks/aliments/ajouter/", {
            "batch": self.batch.pk,
            "feed_type": FeedType.STARTER,
            "purchase_date": timezone.localdate().isoformat(),
            "purchase_mode": "retail",
            "quantity_kg": "10.00",
            "unit_price": "1000.00",
            "supplier": "Boutique du Coin",
            "notes": "10kg au détail",
        })
        self.assertEqual(resp.status_code, 302)

        inventory = FeedInventory.objects.get(supplier="Boutique du Coin")
        self.assertEqual(inventory.purchase_mode, FeedInventory.PurchaseMode.RETAIL)
        self.assertEqual(inventory.quantity_kg, Decimal("10.00"))
        self.assertEqual(inventory.total_weight_kg, Decimal("10.00"))
        self.assertEqual(inventory.total_cost, Decimal("10000.00"))
        self.assertIsNotNone(inventory.expense)

        expense = inventory.expense
        self.assertEqual(expense.amount, Decimal("10000.00"))
        self.assertIn("10.0 kg", expense.designation)
        self.assertIn("Boutique du Coin", expense.designation)

        summary = FeedInventory.stock_summary()[FeedType.STARTER]
        self.assertGreaterEqual(summary["remaining_kg"], Decimal("10.00"))

    def test_expense_creation_with_aliment_syncs_to_stock_retail_and_bags(self):
        from .models import ExpenseCategory
        user = get_user_model().objects.create_user(username="exp_admin", password="password123", is_staff=True)
        self.client.force_login(user)

        cat_aliment, _ = ExpenseCategory.objects.get_or_create(name="Aliment", defaults={"useful_life_months": 1})

        # Test retail sync from Dépenses
        resp_retail = self.client.post("/depenses/nouvelle/", {
            "batch": self.batch.pk,
            "category": cat_aliment.pk,
            "date": timezone.localdate().isoformat(),
            "designation": "10kg aliment croissance au marché",
            "amount": "10000.00",
            "feed_type": FeedType.GROWER,
            "feed_purchase_mode": "retail",
            "feed_quantity_kg": "10.00",
            "feed_supplier": "Marché central",
        })
        self.assertEqual(resp_retail.status_code, 302)

        exp_retail = Expense.objects.get(designation="10kg aliment croissance au marché")
        self.assertTrue(hasattr(exp_retail, "feed_inventory"))
        inv_retail = exp_retail.feed_inventory
        self.assertEqual(inv_retail.feed_type, FeedType.GROWER)
        self.assertEqual(inv_retail.purchase_mode, "retail")
        self.assertEqual(inv_retail.quantity_kg, Decimal("10.00"))
        self.assertEqual(inv_retail.unit_price, Decimal("1000.00"))

        summary_grower = FeedInventory.stock_summary()[FeedType.GROWER]
        self.assertEqual(summary_grower["remaining_kg"], Decimal("10.00"))

        # Test bags sync from Dépenses
        resp_bags = self.client.post("/depenses/nouvelle/", {
            "batch": self.batch.pk,
            "category": cat_aliment.pk,
            "date": timezone.localdate().isoformat(),
            "designation": "2 sacs aliment finition",
            "amount": "40000.00",
            "feed_type": FeedType.FINISHER,
            "feed_purchase_mode": "bags",
            "feed_bags_purchased": 2,
            "feed_bag_weight_kg": "50.00",
            "feed_supplier": "Fournisseur Pro",
        })
        self.assertEqual(resp_bags.status_code, 302)

        exp_bags = Expense.objects.get(designation="2 sacs aliment finition")
        self.assertTrue(hasattr(exp_bags, "feed_inventory"))
        inv_bags = exp_bags.feed_inventory
        self.assertEqual(inv_bags.feed_type, FeedType.FINISHER)
        self.assertEqual(inv_bags.bags_purchased, 2)
        self.assertEqual(inv_bags.bag_weight_kg, Decimal("50.00"))
        self.assertEqual(inv_bags.unit_price, Decimal("20000.00"))

        summary_finisher = FeedInventory.stock_summary()[FeedType.FINISHER]
        self.assertEqual(summary_finisher["remaining_kg"], Decimal("100.00"))

        # Test expense deletion cleans up linked stock
        resp_delete = self.client.post(f"/depenses/{exp_retail.pk}/supprimer/")
        self.assertEqual(resp_delete.status_code, 302)
        self.assertFalse(FeedInventory.objects.filter(pk=inv_retail.pk).exists())
        self.assertEqual(FeedInventory.stock_summary()[FeedType.GROWER]["remaining_kg"], Decimal("0.00"))


class FeedFormulationAndPreparationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("admin_prep", "admin@test.com", "pass1234")
        self.client.force_login(self.admin)
        self.batch = Batch.objects.create(
            name="Lot Provende #1",
            start_date=timezone.localdate() - timedelta(days=15),
            initial_quantity=500,
            unit_cost_chick=Decimal("500.00"),
        )
        self.formula = FeedFormula.objects.create(
            name="Formule Test 300kg",
            feed_type=FeedType.GROWER,
            target_total_kg=Decimal("300.00"),
            commercial_bag_reference_price=Decimal("18000.00"),
        )
        FeedFormulaItem.objects.create(formula=self.formula, ingredient_name="Maïs", quantity=Decimal("180"), unit="kg", default_unit_cost=Decimal("200"), order=1)
        FeedFormulaItem.objects.create(formula=self.formula, ingredient_name="Soja", quantity=Decimal("60"), unit="kg", default_unit_cost=Decimal("600"), order=2)
        FeedFormulaItem.objects.create(formula=self.formula, ingredient_name="Lysine", quantity=Decimal("0.450"), unit="kg", default_unit_cost=Decimal("5000"), order=3)

    def test_formula_properties(self):
        # 180*200 + 60*600 + 0.45*5000 = 36000 + 36000 + 2250 = 74250
        self.assertEqual(self.formula.items.count(), 3)
        self.assertEqual(self.formula.theoretical_total_cost, Decimal("74250.00"))
        self.assertEqual(self.formula.theoretical_cost_per_kg, Decimal("247.50"))

    def test_feed_preparation_creation_and_synchronization(self):
        # Post preparation: Mais and Soja bought today, Lysine from existing stock (0 F)
        post_data = {
            "batch": self.batch.pk,
            "formula": self.formula.pk,
            "feed_type": FeedType.GROWER,
            "date": timezone.localdate().isoformat(),
            "total_produced_kg": "300.00",
            "milling_cost": "1500.00",
            "commercial_bag_reference_price": "18000.00",
            "notes": "Session test provende",
            # Items arrays
            "item_name[]": ["Maïs", "Soja", "Lysine"],
            "item_recipe_qty[]": ["180", "60", "0.450"],
            "item_recipe_unit[]": ["kg", "kg", "kg"],
            "item_purchased[]": ["1", "1", "0"],
            "item_desc[]": ["180 kg", "60 kg", "Pris sur réserve existante"],
            "item_cost[]": ["36000", "36000", "0"],
        }
        resp = self.client.post("/provende/preparer/", post_data)
        self.assertEqual(resp.status_code, 302)

        prep = FeedPreparation.objects.get(batch=self.batch)
        # Total cost: 36000 + 36000 + 0 + 1500 = 73500 FCFA
        self.assertEqual(prep.total_cost, Decimal("73500.00"))
        self.assertEqual(prep.items.count(), 3)
        self.assertEqual(prep.cost_per_kg, Decimal("245.00"))
        self.assertEqual(prep.cost_per_50kg_bag, Decimal("12250.00"))

        # Commercial equivalent = (300 / 50) * 18000 = 108000 FCFA
        self.assertEqual(prep.commercial_cost_equivalent, Decimal("108000.00"))
        # Savings = 108000 - 73500 = 34500 FCFA
        self.assertEqual(prep.savings_amount, Decimal("34500.00"))

        # Verify global expense created
        self.assertIsNotNone(prep.expense)
        exp = prep.expense
        self.assertEqual(exp.amount, Decimal("73500.00"))
        self.assertEqual(exp.category.name, "Aliment")
        self.assertIn("Fabrication provende Croissance", exp.designation)

        # Verify feed inventory created
        self.assertIsNotNone(prep.feed_inventory)
        inv = prep.feed_inventory
        self.assertEqual(inv.quantity_kg, Decimal("300.00"))
        self.assertEqual(inv.feed_type, FeedType.GROWER)

        # Verify stock summary has +300 kg
        stock = FeedInventory.stock_summary()[FeedType.GROWER]
        self.assertEqual(stock["remaining_kg"], Decimal("300.00"))

        # Verify deletion cascades cleanly
        prep_pk = prep.pk
        exp_pk = exp.pk
        inv_pk = inv.pk
        resp_del = self.client.post(f"/provende/preparations/{prep_pk}/supprimer/")
        self.assertEqual(resp_del.status_code, 302)
        self.assertFalse(FeedPreparation.objects.filter(pk=prep_pk).exists())
        self.assertFalse(Expense.objects.filter(pk=exp_pk).exists())
        self.assertFalse(FeedInventory.objects.filter(pk=inv_pk).exists())






