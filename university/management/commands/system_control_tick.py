from django.core.management.base import BaseCommand
from django.utils import timezone
from university.control_services import tick, deliver_messages
from university.models import ControlHeartbeat

class Command(BaseCommand):
    help='Reconcile maintenance/messages and deliver notifications. Run every minute.'
    def handle(self,*args,**options):
        tick()
        deliver_messages()
        ControlHeartbeat.objects.update_or_create(key='delivery',defaults={'last_success_at':timezone.now()})
        self.stdout.write(self.style.SUCCESS('System control schedules and delivery reconciled.'))
