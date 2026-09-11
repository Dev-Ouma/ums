from django.core.management.base import BaseCommand

from university.security_testing_services import run_automated_checks


class Command(BaseCommand):
    help = "Run the available local pre-go-live security checks."

    def handle(self, *args, **options):
        for result in run_automated_checks():
            self.stdout.write(f"[{result['status']}] {result['name']}: {result['detail']}")
