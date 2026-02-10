from django.apps import AppConfig


class TopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "recotem.api"

    def ready(self):
        import recotem.api.services.model_service  # noqa: F401 — register signals
