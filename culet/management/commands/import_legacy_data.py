from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction


IMPORT_COMMANDS = [
    "seed_reference_data",
    "verify_reference_data",

    "import_customers",
    "import_vendors",
    "import_metal_types",
    "import_stone_shapes",
    "import_stone_types",
    "import_findings",
    "import_styles",
    "import_style_metals",
    "import_style_stones",
    "infer_style_findings",
    "import_style_findings",

    "import_jobs",
    "import_job_metals",
    "import_job_stones",
    "import_job_findings",

    "populate_job_requirements",

    "verify_migration",
]


class Command(BaseCommand):
    help = (
        "Import all legacy Culet data into a fresh Culet database."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Beginning legacy import"
            )
        )

        total = len(IMPORT_COMMANDS)

        for index, command in enumerate(IMPORT_COMMANDS, start=1):
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"[{index}/{total}] {command}"
                )
            )
            call_command(command)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Legacy import completed successfully."
            )
        )