from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("journal/", views.daily_entry, name="daily-entry"),
    path("journal/supprimer/<str:date_str>/", views.daily_entry_delete, name="daily-entry-delete"),
    path("lots/", views.batch_history, name="batch-history"),
    path("lots/nouveau/", views.batch_create, name="batch-create"),
    path("lots/<int:pk>/modifier/", views.batch_edit, name="batch-edit"),
    path("lots/<int:pk>/cloturer/", views.close_batch, name="batch-close"),
    path("lots/<int:pk>/bilan/", views.batch_report, name="batch-report"),
    path("soins/", views.treatment_list, name="treatment-list"),
    path("soins/ajouter/", views.treatment_create, name="treatment-create"),
    path("soins/<int:pk>/valider/", views.complete_treatment, name="treatment-complete"),
    path("soins/<int:pk>/modifier/", views.treatment_edit, name="treatment-edit"),
    path("soins/<int:pk>/supprimer/", views.treatment_delete, name="treatment-delete"),
    path("lots/<int:pk>/synchroniser-soins/", views.treatment_sync_batch, name="treatment-sync-batch"),
    path("prophylaxie/programme/", views.prophylaxis_program_list, name="prophylaxis-program-list"),
    path("prophylaxie/programme/nouveau/", views.prophylaxis_program_create, name="prophylaxis-program-create"),
    path("prophylaxie/programme/jour/<int:day>/dupliquer/", views.prophylaxis_program_duplicate, name="prophylaxis-program-duplicate"),
    path("prophylaxie/programme/jour/<int:day>/effacer/", views.prophylaxis_program_clear_day, name="prophylaxis-program-clear-day"),
    path("prophylaxie/programme/<int:pk>/modifier/", views.prophylaxis_program_edit, name="prophylaxis-program-edit"),
    path("prophylaxie/programme/<int:pk>/supprimer/", views.prophylaxis_program_delete, name="prophylaxis-program-delete"),
    path("depenses/", views.expense_list, name="expense-list"),
    path("depenses/nouvelle/", views.expense_create, name="expense-create"),
    path("ventes/", views.sale_list, name="sale-list"),
    path("ventes/nouvelle/", views.sale_create, name="sale-create"),
    path("stocks/", views.feed_stock_view, name="feed-stock"),
    path("stocks/aliments/ajouter/", views.feed_purchase, name="feed-purchase"),
]
