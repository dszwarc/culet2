import os
import re
import secrets
import string
import unicodedata
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import Employee, LegacyRecordMap, Role


EMPLOYEE_SQL = """
    SELECT
        id,
        first_name,
        last_name,
        is_active
    FROM employee
    WHERE is_active = 'Y'
    ORDER BY id
"""

# Known duplicate/test record:
# 1229: Darek (makeup) Szumski
IGNORED_EMPLOYEE_IDS = {
    1229: 'Duplicate "makeup" employee record.',
}

TEMP_PASSWORD_ADJECTIVES = (
    "Amber",
    "Bright",
    "Calm",
    "Clear",
    "Copper",
    "Coral",
    "Golden",
    "Grand",
    "Green",
    "Happy",
    "Ivory",
    "Lucky",
    "Maple",
    "Merry",
    "Noble",
    "Ocean",
    "Orange",
    "Purple",
    "Quick",
    "Quiet",
    "Rapid",
    "Red",
    "Royal",
    "Silver",
    "Smooth",
    "Sunny",
    "Swift",
    "Velvet",
    "Warm",
    "White",
    "Wild",
    "Yellow",
)

TEMP_PASSWORD_NOUNS = (
    "Apple",
    "Badger",
    "Beacon",
    "Bear",
    "Canyon",
    "Cedar",
    "Cherry",
    "Comet",
    "Eagle",
    "Falcon",
    "Forest",
    "Garden",
    "Hammer",
    "Harbor",
    "Hawk",
    "Island",
    "Jasper",
    "Lantern",
    "Lemon",
    "Meadow",
    "Mountain",
    "Otter",
    "Panther",
    "Pearl",
    "Pine",
    "Planet",
    "Rabbit",
    "River",
    "Rocket",
    "Sparrow",
    "Stone",
    "Tiger",
    "Train",
    "Valley",
    "Willow",
    "Wolf",
)

TEMP_PASSWORD_SYMBOLS = "!@#$%"

REAL_CREDENTIAL_PATH = (
    Path(settings.BASE_DIR)
    / "backups"
    / "imported_employee_credentials.xlsx"
)

DRY_RUN_CREDENTIAL_PATH = (
    Path(settings.BASE_DIR)
    / "backups"
    / "imported_employee_credentials_dry_run.xlsx"
)


class Command(BaseImportCommand):
    help = (
        "Import active employees from old Culet, create Django users, "
        "and generate temporary login credentials."
    )

    def run_import(self, *args, **options):
        hourly_role = Role.objects.filter(
            name="Hourly",
            active=True,
        ).first()

        if hourly_role is None:
            raise CommandError(
                'The active role "Hourly" does not exist. '
                "Run `python manage.py seed_roles` first."
            )

        rows = fetch_old_rows(EMPLOYEE_SQL)
        total = len(rows)

        self.stdout.write(
            f"Found {total:,} active old employee rows."
        )

        self.generated_credentials = []
        self.generated_passwords = set()
        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_employee(
                    row=row,
                    hourly_role=hourly_role,
                )
            except Exception as exc:
                self.record_error(
                    f"Employee row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Employees")

        if self.dry_run:
            # This workbook is only a preview. Its passwords will not work
            # because all dry-run database changes are rolled back.
            self.write_credentials_workbook(
                path=DRY_RUN_CREDENTIAL_PATH,
                credentials=self.generated_credentials,
                dry_run=True,
            )
        else:
            # Write credentials only after the PostgreSQL transaction commits.
            credentials = list(self.generated_credentials)

            transaction.on_commit(
                lambda: self.write_credentials_workbook(
                    path=REAL_CREDENTIAL_PATH,
                    credentials=credentials,
                    dry_run=False,
                ),
                using="default",
            )

    def import_employee(self, *, row, hourly_role):
        old_id = row["id"]

        if old_id in IGNORED_EMPLOYEE_IDS:
            reason = IGNORED_EMPLOYEE_IDS[old_id]

            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="employee",
                legacy_id=old_id,
                message=reason,
            )

            self.row_message(
                f"SKIP old employee {old_id}: {reason}"
            )
            return

        first_name = clean_text(row["first_name"])
        last_name = clean_text(row["last_name"])

        if not first_name or not last_name:
            reason = "First name or last name was blank."

            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="employee",
                legacy_id=old_id,
                message=reason,
            )

            self.row_message(
                f"SKIP old employee {old_id}: {reason}"
            )
            return

        first_name = self.truncate_for_user_field(
            first_name,
            "first_name",
        )
        last_name = self.truncate_for_user_field(
            last_name,
            "last_name",
        )

        try:
            with transaction.atomic(using="default"):
                employee = self.get_mapped_object(
                    legacy_table="employee",
                    legacy_id=old_id,
                    model_class=Employee,
                )

                if employee is None:
                    self.create_employee(
                        old_id=old_id,
                        first_name=first_name,
                        last_name=last_name,
                        hourly_role=hourly_role,
                    )
                    return

                self.update_existing_employee(
                    old_id=old_id,
                    employee=employee,
                    first_name=first_name,
                    last_name=last_name,
                )

        except IntegrityError as exc:
            raise ValueError(
                f"Integrity error importing employee {old_id}: "
                f"{first_name} {last_name}"
            ) from exc

    def create_employee(
        self,
        *,
        old_id,
        first_name,
        last_name,
        hourly_role,
    ):
        username = self.generate_unique_username(
            first_name=first_name,
            last_name=last_name,
        )
        temporary_password = self.generate_temporary_password()

        user = User(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email="",
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(temporary_password)
        user.save()

        employee = Employee.objects.create(
            user=user,
            department=None,
            role=hourly_role,
            clocked_in=False,
            must_change_password=True,
        )

        self.record_mapping(
            legacy_table="employee",
            legacy_id=old_id,
            target=employee,
            action=LegacyRecordMap.ACTION_CREATED,
        )

        self.generated_credentials.append(
            {
                "legacy_id": old_id,
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
                "temporary_password": temporary_password,
            }
        )

        self.stats.created += 1

        # Do not print the password to the console.
        self.row_message(
            f"CREATE old employee {old_id}: "
            f"{first_name} {last_name} → {username}"
        )

    def update_existing_employee(
        self,
        *,
        old_id,
        employee,
        first_name,
        last_name,
    ):
        user = employee.user
        user_changed_fields = []

        if user.first_name != first_name:
            user.first_name = first_name
            user_changed_fields.append("first_name")

        if user.last_name != last_name:
            user.last_name = last_name
            user_changed_fields.append("last_name")

        if not user.is_active:
            user.is_active = True
            user_changed_fields.append("is_active")

        if user_changed_fields:
            user.save(update_fields=user_changed_fields)

            self.stats.updated += 1
            action = LegacyRecordMap.ACTION_UPDATED

            self.row_message(
                f"UPDATE old employee {old_id}: "
                f"{user.get_full_name()} "
                f"({', '.join(user_changed_fields)})"
            )
        else:
            self.stats.unchanged += 1
            action = LegacyRecordMap.ACTION_UNCHANGED

            self.row_message(
                f"UNCHANGED old employee {old_id}: "
                f"{user.get_full_name()} → {user.username}"
            )

        # On reruns, deliberately preserve:
        # - username
        # - password
        # - must_change_password
        # - department
        # - role
        #
        # This prevents a migration rerun from undoing manual changes.
        self.record_mapping(
            legacy_table="employee",
            legacy_id=old_id,
            target=employee,
            action=action,
        )

    def generate_unique_username(
        self,
        *,
        first_name,
        last_name,
    ):
        first_initial = self.normalize_username_text(
            first_name
        )[:1]
        normalized_last_name = self.normalize_username_text(
            last_name
        )

        base = f"{first_initial}{normalized_last_name}"

        if not base:
            base = "employee"

        max_length = User._meta.get_field("username").max_length
        base = base[:max_length]

        username = base
        suffix = 2

        while User.objects.filter(
            username__iexact=username
        ).exists():
            suffix_text = str(suffix)
            available_length = max_length - len(suffix_text)

            username = (
                f"{base[:available_length]}{suffix_text}"
            )
            suffix += 1

        return username

    @staticmethod
    def normalize_username_text(value):
        """
        Convert names to lowercase ASCII letters/numbers only.

        Examples:
            O'Brien      -> obrien
            Smith-Jones  -> smithjones
            José         -> jose
            (makeup)     -> makeup
        """
        normalized = unicodedata.normalize(
            "NFKD",
            clean_text(value),
        )
        ascii_value = normalized.encode(
            "ascii",
            "ignore",
        ).decode("ascii")

        return re.sub(
            r"[^a-z0-9]",
            "",
            ascii_value.lower(),
        )

    def generate_temporary_password(self):
        """
        Generate a readable temporary password.

        Example:
            Silver-Tiger-River-482!

        The password contains:
        - one adjective
        - two nouns
        - a three-digit number
        - a symbol

        Confusing characters such as lowercase L, uppercase I,
        uppercase O, and zero are avoided in the word portion.
        """

        while True:
            adjective = secrets.choice(
                TEMP_PASSWORD_ADJECTIVES
            )
            first_noun = secrets.choice(
                TEMP_PASSWORD_NOUNS
            )
            second_noun = secrets.choice(
                TEMP_PASSWORD_NOUNS
            )

            # Avoid passwords such as Tiger-Tiger.
            if first_noun == second_noun:
                continue

            number = secrets.randbelow(900) + 100
            symbol = secrets.choice(
                TEMP_PASSWORD_SYMBOLS
            )

            password = (
                f"{adjective}-"
                f"{first_noun}-"
                f"{second_noun}-"
                f"{number}"
                f"{symbol}"
            )

            if password not in self.generated_passwords:
                self.generated_passwords.add(password)
                return password

    @staticmethod
    def truncate_for_user_field(value, field_name):
        max_length = User._meta.get_field(
            field_name
        ).max_length

        return value[:max_length]

    def write_credentials_workbook(
        self,
        *,
        path,
        credentials,
        dry_run,
    ):
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Employee Credentials"

        title = (
            "DRY-RUN PREVIEW — THESE PASSWORDS WILL NOT WORK"
            if dry_run
            else "Imported Employee Temporary Credentials"
        )

        worksheet.merge_cells("A1:E1")
        worksheet["A1"] = title
        worksheet["A1"].font = Font(
            bold=True,
            color="FFFFFF",
            size=14,
        )
        worksheet["A1"].fill = PatternFill(
            fill_type="solid",
            fgColor="C00000" if dry_run else "1F4E78",
        )

        worksheet.append([])
        worksheet.append(
            [
                "Legacy Employee ID",
                "First Name",
                "Last Name",
                "Username",
                "Temporary Password",
            ]
        )

        for credential in credentials:
            worksheet.append(
                [
                    credential["legacy_id"],
                    credential["first_name"],
                    credential["last_name"],
                    credential["username"],
                    credential["temporary_password"],
                ]
            )

        for cell in worksheet[3]:
            cell.font = Font(
                bold=True,
                color="FFFFFF",
            )
            cell.fill = PatternFill(
                fill_type="solid",
                fgColor="5B9BD5",
            )

        worksheet.freeze_panes = "A4"
        worksheet.auto_filter.ref = (
            f"A3:E{worksheet.max_row}"
        )

        worksheet.column_dimensions["A"].width = 20
        worksheet.column_dimensions["B"].width = 22
        worksheet.column_dimensions["C"].width = 24
        worksheet.column_dimensions["D"].width = 24
        worksheet.column_dimensions["E"].width = 24

        workbook.save(path)

        # Restrict the workbook to the current operating-system user.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

        label = (
            "Dry-run credential preview"
            if dry_run
            else "Employee credential workbook"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"{label} written to: {path}"
            )
        )