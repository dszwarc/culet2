from django.apps import AppConfig


class CuletConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "culet"

    def ready(self):
        import culet.signals  # noqa: F401