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
    total_costs = direct_costs + amortization
    net = revenue - total_costs
    age = max(batch.current_age_days, 1)
    projected = (net * Decimal(batch.target_duration_days) / Decimal(age)).quantize(CENT)
    return {
        "revenue": revenue,
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
