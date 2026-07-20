from django.core.management import call_command
from django.core.management.base import BaseCommand


IMPORT_COMMANDS = [
    # Application reference data
    "seed_reference_data",
    "verify_reference_data",

    # People
    "import_employees",
    "upload_employee_credentials",

    # Master data
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

    # Jobs
    "import_jobs",
    "import_job_metals",
    "import_job_stones",
    "import_job_findings",

    # Derived data
    "populate_job_requirements",

    # Final verification
    "verify_migration",
]


class Command(BaseCommand):
    help = (
        "Import all legacy Culet data into the new Culet database."
    )

    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Beginning legacy Culet import"
            )
        )

        total = len(IMPORT_COMMANDS)

        for index, command in enumerate(IMPORT_COMMANDS, start=1):
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"[{index}/{total}] Running {command}"
                )
            )

            call_command(command)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Legacy Culet import completed successfully."
            )
        )