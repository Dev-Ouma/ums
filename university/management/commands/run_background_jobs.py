import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from university.control_services import deliver_messages, tick
from university.control_models import ControlHeartbeat


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run UMS scheduled maintenance, backup, retention, and notification jobs without a browser session."

    def handle(self, *args, **options):
        try:
            tick()
            deliver_messages()
            ControlHeartbeat.objects.update_or_create(
                key="job_runner", defaults={"last_success_at": timezone.now()}
            )
        except Exception as exc:
            logger.exception("Background job runner failed")
            raise CommandError("Background job runner failed; details were recorded in server logs.") from exc
        self.stdout.write(self.style.SUCCESS("UMS background jobs completed independently of an administrator session."))
