from django.contrib import admin

from .models import (
    Batch,
    Expense,
    ExpenseCategory,
    FeedConsumption,
    FeedInventory,
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
    list_display = ("purchase_date", "feed_type", "bags_purchased", "bag_weight_kg", "unit_price", "supplier")
    list_filter = ("feed_type", "purchase_date")
    search_fields = ("supplier", "notes")


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
