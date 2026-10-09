from django.apps import AppConfig


class PoulaillerCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "poulailler_core"
    verbose_name = "Gestion avicole"

    def ready(self):
        from . import signals  # noqa: F401
