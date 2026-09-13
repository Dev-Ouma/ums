from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from university.control_services import start_maintenance_mode

class Command(BaseCommand):
    help = 'Activate controlled system-wide maintenance mode with authoritative server timer and automatic resume.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--duration',
            type=int,
            default=2,
            help='Maintenance window duration in minutes (default: 2 minutes).'
        )
        parser.add_argument(
            '--reason',
            type=str,
            default=None,
            help='Internal operational reason for the maintenance window.'
        )
        parser.add_argument(
            '--message',
            type=str,
            default=None,
            help='Public customer-facing maintenance explanation message.'
        )

    def handle(self, *args, **options):
        duration = options['duration']
        reason = options['reason']
        public_message = options['message']

        # Select administrative user for audit trail if available
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).first() or User.objects.filter(username='admin').first()

        restriction = start_maintenance_mode(
            duration_minutes=duration,
            user=admin_user,
            reason=reason,
            public_message=public_message,
        )

        self.stdout.write(self.style.SUCCESS(
            f"SYSTEM STATUS → Maintenance Mode Activated\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Restriction ID:    {restriction.pk}\n"
            f"Duration:          {duration} minutes\n"
            f"Server Start Time: {restriction.starts_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Authoritative End: {restriction.ends_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Scope:             Entire UMS System-Wide\n"
            f"Session Policy:    BLOCK (Sessions Preserved)\n"
            f"Auto-Resume:       Enabled (Server-authoritative)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Normal operations will automatically resume when the {duration}-minute window expires."
        ))
