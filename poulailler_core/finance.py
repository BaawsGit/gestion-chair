from decimal import Decimal
from datetime import timedelta

from django.db.models import Sum

from .models import Expense, Sale


CENT = Decimal("0.01")


def batch_financials(batch):
    """Return direct costs, cycle depreciation, revenue, and net result for one batch."""
    expenses = Expense.objects.filter(batch=batch).select_related("category")
    direct_costs = sum(
        (expense.amount for expense in expenses if not expense.category.is_investment),
        Decimal("0.00"),
    )
    chick_expense_recorded = expenses.filter(category__name__iexact="Poussins").exists()
    if not chick_expense_recorded:
        billable_chicks = batch.ordered_quantity if batch.ordered_quantity > 0 else (batch.initial_quantity - batch.bonus_quantity)
        direct_costs += Decimal(max(billable_chicks, 0)) * batch.unit_cost_chick

    investment_expenses = Expense.objects.filter(category__is_investment=True).filter(
        models_q_batch(batch), date__lt=batch.start_date + timedelta(days=batch.target_duration_days)
    ).select_related("category")
    amortization = Decimal("0.00")
    for expense in investment_expenses:
        life_days = Decimal(max(expense.category.useful_life_months, 1)) * Decimal("30.4375")
        life_end = expense.date + timedelta(days=int(life_days))
        period_start = max(batch.start_date, expense.date)
        period_end = min(batch.start_date + timedelta(days=batch.target_duration_days), life_end)
        amortized_days = max((period_end - period_start).days, 0)
        amortization += expense.amount * Decimal(amortized_days) / life_days

    revenue = batch.sales.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    chicken_revenue = batch.sales.filter(sale_type="chicken").aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    giblets_revenue = batch.sales.filter(sale_type="giblets").aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    byproduct_revenue = (revenue - chicken_revenue).quantize(CENT)
    total_costs = direct_costs + amortization
    net = revenue - total_costs
    age = max(batch.current_age_days, 1)
    projected = (net * Decimal(batch.target_duration_days) / Decimal(age)).quantize(CENT)
    return {
        "revenue": revenue,
        "chicken_revenue": chicken_revenue.quantize(CENT),
        "giblets_revenue": giblets_revenue.quantize(CENT),
        "byproduct_revenue": byproduct_revenue,
        "direct_costs": direct_costs.quantize(CENT),
        "amortization": amortization.quantize(CENT),
        "total_costs": total_costs.quantize(CENT),
        "net": net.quantize(CENT),
        "projected_net": projected,
    }


def models_q_batch(batch):
    from django.db.models import Q

    return Q(batch=batch) | Q(batch__isnull=True)


def sync_batch_chick_expense(batch):
    """
    Ensure the chick acquisition cost is recorded as an Expense in the database
    with category 'Poussins' so that it appears in the accounting ledger (Dépenses),
    reports, and summaries.
    """
    from .models import ExpenseCategory

    chick_cost = batch.chick_cost
    cat_chicks, _ = ExpenseCategory.objects.get_or_create(
        name="Poussins",
        defaults={"is_investment": False, "useful_life_months": 36},
    )

    if chick_cost <= 0:
        Expense.objects.filter(batch=batch, category=cat_chicks).delete()
        return None

    designation = f"Achat des {batch.ordered_quantity} poussins d'un jour"
    if batch.bonus_quantity > 0:
        designation += f" (+{batch.bonus_quantity} bonus offerts)"

    chick_expense = Expense.objects.filter(batch=batch, category=cat_chicks).first()
    if chick_expense:
        chick_expense.designation = designation
        chick_expense.amount = chick_cost
        chick_expense.date = batch.start_date
        chick_expense.save(update_fields=["designation", "amount", "date"])
    else:
        chick_expense = Expense.objects.create(
            batch=batch,
            category=cat_chicks,
            date=batch.start_date,
            designation=designation,
            amount=chick_cost,
        )
    return chick_expense


def sync_feed_inventory_expense(inventory):
    """
    Ensure a corresponding Expense is recorded with category 'Aliment'
    so that feed purchases appear in Dépenses, Dashboard, and batch financials.
    """
    from .models import Batch, Expense, ExpenseCategory, FeedInventory

    cat_feed = ExpenseCategory.objects.filter(name__iexact="Aliment").first()
    if not cat_feed:
        cat_feed = ExpenseCategory.objects.filter(name__icontains="aliment").first()
    if not cat_feed:
        cat_feed = ExpenseCategory.objects.create(
            name="Aliment",
            is_investment=False,
            useful_life_months=1,
        )

    if not inventory.batch_id:
        active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
        if active_batch:
            inventory.batch = active_batch
            FeedInventory.objects.filter(pk=inventory.pk).update(batch=active_batch)

    if hasattr(inventory, "feed_preparation") and inventory.feed_preparation:
        prep = inventory.feed_preparation
        formula_name = prep.formula.name if prep.formula else "Recette personnalisée"
        designation = f"Fabrication provende {prep.get_feed_type_display()} · {prep.total_produced_kg:,.0f} kg ({formula_name})"
        total_amount = prep.total_cost
    elif inventory.purchase_mode == FeedInventory.PurchaseMode.RETAIL:
        total_amount = inventory.total_cost
        qty = inventory.quantity_kg or Decimal("0.00")
        designation = f"Achat aliment {inventory.get_feed_type_display()} au détail ({qty:.1f} kg à {inventory.unit_price:,.0f} F/kg)"
        if inventory.supplier:
            designation += f" - {inventory.supplier}"
    else:
        total_amount = inventory.total_cost
        bags = inventory.bags_purchased or 0
        weight = inventory.bag_weight_kg or Decimal("0.00")
        designation = f"Achat aliment {inventory.get_feed_type_display()} ({bags} sacs de {weight:.0f} kg à {inventory.unit_price:,.0f} F/sac)"
        if inventory.supplier:
            designation += f" - {inventory.supplier}"

    if inventory.expense_id:
        try:
            expense = Expense.objects.get(pk=inventory.expense_id)
            expense.batch = inventory.batch
            expense.category = cat_feed
            expense.date = inventory.purchase_date
            expense.designation = designation
            expense.amount = total_amount
            expense.save(update_fields=["batch", "category", "date", "designation", "amount"])
            return expense
        except Expense.DoesNotExist:
            inventory.expense = None

    expense = Expense.objects.create(
        batch=inventory.batch,
        category=cat_feed,
        date=inventory.purchase_date,
        designation=designation,
        amount=total_amount,
    )
    FeedInventory.objects.filter(pk=inventory.pk).update(expense=expense)
    inventory.expense = expense
    return expense


def sync_all_feed_expenses():
    """
    Ensure all FeedInventory records have their associated Expense synchronized.
    """
    from .models import Batch, FeedInventory

    active_batch = Batch.objects.filter(status=Batch.Status.ACTIVE).first()
    for inv in FeedInventory.objects.select_related("expense", "batch"):
        if not inv.expense_id:
            if not inv.batch_id and active_batch:
                inv.batch = active_batch
                FeedInventory.objects.filter(pk=inv.pk).update(batch=active_batch)
            sync_feed_inventory_expense(inv)


def treasury_summary(batch_id=None):
    """
    Compute complete treasury (Caisse) and loans/lenders summary:
    - paid_sales_total: total cash collected from PAID sales
    - pending_sales_total: total sales waiting for payment (CREDIT)
    - cash_expenses_total: expenses (or parts of expenses) taken directly from the cash box
    - cash_repayments_total: loan repayments taken from the cash box
    - total_cash_outflow: cash_expenses_total + cash_repayments_total
    - cash_balance: paid_sales_total - total_cash_outflow (what truly remains in the cash box!)
    - total_borrowed: total amount financed via loans by lenders
    - total_repaid: total amount of loans already repaid
    - total_remaining_debt: how much we still owe to all lenders combined
    - lenders_breakdown: list of dicts per lender (borrowed, repaid, remaining, count)
    """
    from .models import Expense, ExpenseFunding, Sale

    sales_qs = Sale.objects.all()
    expenses_qs = Expense.objects.prefetch_related("fundings").all()
    fundings_qs = ExpenseFunding.objects.select_related("expense").all()

    if batch_id:
        if batch_id == "general":
            expenses_qs = expenses_qs.filter(batch__isnull=True)
            fundings_qs = fundings_qs.filter(expense__batch__isnull=True)
            sales_qs = sales_qs.none()
        else:
            sales_qs = sales_qs.filter(batch_id=batch_id)
            expenses_qs = expenses_qs.filter(batch_id=batch_id)
            fundings_qs = fundings_qs.filter(expense__batch_id=batch_id)

    paid_sales_total = Decimal("0.00")
    pending_sales_total = Decimal("0.00")
    for s in sales_qs:
        paid_sales_total += s.effective_amount_paid
        pending_sales_total += s.remaining_amount_due

    cash_expenses_total = Decimal("0.00")
    cash_repayments_total = Decimal("0.00")
    total_borrowed = Decimal("0.00")
    total_repaid = Decimal("0.00")
    total_remaining_debt = Decimal("0.00")

    lenders_map = {}

    for f in fundings_qs:
        if f.source_type == ExpenseFunding.SourceType.CASH:
            cash_expenses_total += f.amount
        else:
            # Loan or Repaid loan
            borrowed = f.amount
            repaid = f.effective_repaid
            remaining = f.remaining_to_repay

            total_borrowed += borrowed
            total_repaid += repaid
            total_remaining_debt += remaining

            if f.repaid_from_cash:
                cash_repayments_total += repaid

            lender_key = (f.lender_name or "Prêteur non spécifié").strip()
            entry = lenders_map.setdefault(lender_key, {
                "name": lender_key,
                "borrowed": Decimal("0.00"),
                "repaid": Decimal("0.00"),
                "remaining": Decimal("0.00"),
                "count": 0,
                "funding_ids": [],
            })
            entry["borrowed"] += borrowed
            entry["repaid"] += repaid
            entry["remaining"] += remaining
            entry["count"] += 1
            entry["funding_ids"].append(f.pk)

    total_expenses_amount = Decimal("0.00")
    tagged_expenses_amount = Decimal("0.00")
    untagged_expenses_count = 0
    for exp in expenses_qs:
        total_expenses_amount += exp.amount
        funded = sum((f.amount for f in exp.fundings.all()), Decimal("0.00"))
        tagged_expenses_amount += min(funded, exp.amount)
        if funded < exp.amount:
            untagged_expenses_count += 1

    untagged_expenses_amount = max(total_expenses_amount - tagged_expenses_amount, Decimal("0.00"))

    total_cash_outflow = cash_expenses_total + cash_repayments_total
    cash_balance = paid_sales_total - total_cash_outflow

    lenders_breakdown = sorted(
        lenders_map.values(),
        key=lambda x: (-x["remaining"], -x["borrowed"], x["name"].lower()),
    )

    return {
        "paid_sales_total": paid_sales_total.quantize(CENT),
        "pending_sales_total": pending_sales_total.quantize(CENT),
        "cash_expenses_total": cash_expenses_total.quantize(CENT),
        "cash_repayments_total": cash_repayments_total.quantize(CENT),
        "total_cash_outflow": total_cash_outflow.quantize(CENT),
        "cash_balance": cash_balance.quantize(CENT),
        "total_borrowed": total_borrowed.quantize(CENT),
        "total_repaid": total_repaid.quantize(CENT),
        "total_remaining_debt": total_remaining_debt.quantize(CENT),
        "total_expenses_amount": total_expenses_amount.quantize(CENT),
        "untagged_expenses_amount": untagged_expenses_amount.quantize(CENT),
        "untagged_expenses_count": untagged_expenses_count,
        "lenders_breakdown": lenders_breakdown,
    }

