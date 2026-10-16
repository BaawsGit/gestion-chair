from django.contrib import admin

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
    Mortality,
    ProphylaxisProgram,
    Sale,
    TreatmentLog,
)


class TreatmentLogInline(admin.TabularInline):
    model = TreatmentLog
    extra = 0
    fields = ("target_day", "date_planned", "medicine_name", "is_completed", "date_administered")
    readonly_fields = ("target_day", "date_planned", "medicine_name")


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "start_date",
        "ordered_quantity",
        "bonus_quantity",
        "initial_quantity",
        "unit_cost_chick",
        "current_stock",
        "mortality_rate",
        "status",
    )
    list_filter = ("status", "start_date")
    search_fields = ("name", "notes")
    readonly_fields = ("created_at", "initial_quantity")
    inlines = (TreatmentLogInline,)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "designation", "category", "batch", "amount")
    list_filter = ("category", "date")
    search_fields = ("designation", "batch__name")
    date_hierarchy = "date"


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_investment", "useful_life_months")
    list_filter = ("is_investment",)
    search_fields = ("name",)


@admin.register(Mortality)
class MortalityAdmin(admin.ModelAdmin):
    list_display = ("date", "batch", "count", "cause_suspected")
    list_filter = ("cause_suspected", "date")
    search_fields = ("batch__name", "notes")
    date_hierarchy = "date"


@admin.register(FeedInventory)
class FeedInventoryAdmin(admin.ModelAdmin):
    list_display = ("purchase_date", "batch", "feed_type", "purchase_mode", "bags_purchased", "bag_weight_kg", "quantity_kg", "unit_price", "total_cost", "expense", "supplier")
    list_filter = ("purchase_mode", "feed_type", "batch", "purchase_date")
    search_fields = ("supplier", "notes", "batch__name")


@admin.register(FeedConsumption)
class FeedConsumptionAdmin(admin.ModelAdmin):
    list_display = ("date", "batch", "feed_type", "bags_consumed", "kg_consumed")
    list_filter = ("feed_type", "date")
    search_fields = ("batch__name",)
    date_hierarchy = "date"


@admin.register(ProphylaxisProgram)
class ProphylaxisProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "target_day", "duration_days", "frequency_days", "medicine_name", "is_active")
    list_filter = ("is_active", "target_day")
    search_fields = ("name", "medicine_name")


@admin.register(TreatmentLog)
class TreatmentLogAdmin(admin.ModelAdmin):
    list_display = ("date_planned", "batch", "target_day", "medicine_name", "is_completed", "date_administered")
    list_filter = ("is_completed", "date_planned", "administration_route")
    search_fields = ("batch__name", "medicine_name")
    date_hierarchy = "date_planned"


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("date", "batch", "quantity_sold", "total_weight_kg", "total_amount", "customer_name", "payment_status")
    list_filter = ("payment_status", "date")
    search_fields = ("batch__name", "customer_name")
    date_hierarchy = "date"


class FeedFormulaItemInline(admin.TabularInline):
    model = FeedFormulaItem
    extra = 1
    fields = ("order", "ingredient_name", "quantity", "unit", "default_unit_cost")


@admin.register(FeedFormula)
class FeedFormulaAdmin(admin.ModelAdmin):
    list_display = ("name", "feed_type", "target_total_kg", "commercial_bag_reference_price", "is_active", "created_at")
    list_filter = ("feed_type", "is_active")
    search_fields = ("name", "notes")
    inlines = (FeedFormulaItemInline,)


class FeedPreparationItemInline(admin.TabularInline):
    model = FeedPreparationItem
    extra = 0
    fields = ("order", "ingredient_name", "recipe_quantity", "recipe_unit", "purchased_today", "purchased_description", "purchase_cost")


@admin.register(FeedPreparation)
class FeedPreparationAdmin(admin.ModelAdmin):
    list_display = ("date", "feed_type", "batch", "formula", "total_produced_kg", "total_cost", "cost_per_kg", "cost_per_50kg_bag", "savings_amount")
    list_filter = ("feed_type", "date", "batch")
    search_fields = ("notes", "batch__name", "formula__name")
    date_hierarchy = "date"
    inlines = (FeedPreparationItemInline,)

