from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # Registers the user_logged_in receiver that stamps login activity.
        from . import activity  # noqa: F401
        # Registers the login ledger, lockout counters and status enforcement.
        from . import identity  # noqa: F401
