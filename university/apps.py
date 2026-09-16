from django.apps import AppConfig


class UniversityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "university"

    def ready(self):
        # Connects the @receiver-decorated functions in event_receivers.py
        # to the domain-event signals in events.py. Importing the module is
        # what wires up the @receiver decorators -- nothing else needed here.
        from university import event_receivers  # noqa: F401
