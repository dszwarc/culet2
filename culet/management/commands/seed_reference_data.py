from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction


SEED_COMMANDS = [
    "seed_roles",
    "seed_departments",
    "seed_locations",
    "seed_job_statuses",
    "seed_activity_steps",
    "seed_failure_types",
    "seed_movementtypes",
]


class Command(BaseCommand):
    help = "Seed all Culet reference data."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Starting Culet reference data seed"
            )
        )

        for command in SEED_COMMANDS:
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(f"Running {command}...")
            )
            call_command(command)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Reference data seeded successfully."
            )
        )