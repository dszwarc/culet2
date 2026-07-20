from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Upload the generated employee credential workbook to configured storage."

    def handle(self, *args, **options):
        local_path = (
            Path(settings.BASE_DIR)
            / "backups"
            / "imported_employee_credentials.xlsx"
        )

        if not local_path.exists():
            raise CommandError(
                f"Credential workbook was not found: {local_path}"
            )

        storage_name = (
            "private-migration/"
            "imported_employee_credentials.xlsx"
        )

        if default_storage.exists(storage_name):
            default_storage.delete(storage_name)

        with local_path.open("rb") as file_handle:
            saved_name = default_storage.save(
                storage_name,
                File(file_handle),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Credential workbook uploaded as: {saved_name}"
            )
        )