import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from culet.models import Employee, LegacyRecordMap


PIN_PATTERN = re.compile(r"^\d{4}$")


class Command(BaseCommand):
    help = (
        "Set active hourly employees' Django passwords to their "
        "four-digit legacy Culet PINs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Validate mappings and PINs without changing any passwords."
            ),
        )

        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print each employee that is checked.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbose = options["verbose"]

        self.stdout.write(
            self.style.WARNING(
                "DRY RUN — no passwords will be changed."
                if dry_run
                else "LIVE RUN — employee passwords will be changed."
            )
        )

        legacy_rows = self.get_legacy_employee_rows()

        if not legacy_rows:
            raise CommandError(
                "No active employees were found in the legacy database."
            )

        stats = {
            "legacy_rows": len(legacy_rows),
            "updated": 0,
            "unchanged": 0,
            "non_hourly": 0,
            "missing_mapping": 0,
            "missing_employee": 0,
            "inactive_user": 0,
            "invalid_pin": 0,
        }

        with transaction.atomic(using="default"):
            for row in legacy_rows:
                self.process_employee(
                    row=row,
                    stats=stats,
                    dry_run=dry_run,
                    verbose=verbose,
                )

            if dry_run:
                # Roll back all PostgreSQL changes made during the dry run.
                transaction.set_rollback(
                    True,
                    using="default",
                )

        self.print_summary(
            stats=stats,
            dry_run=dry_run,
        )

        if stats["invalid_pin"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nSome active legacy employees did not have a valid "
                    "four-digit PIN. Their passwords were not changed."
                )
            )

        if stats["missing_mapping"] or stats["missing_employee"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nSome legacy employees could not be matched to a "
                    "Culet Employee. Review those records before launch."
                )
            )

    def get_legacy_employee_rows(self):
        sql = """
            SELECT
                id,
                first_name,
                last_name,
                TRIM(pin) AS pin
            FROM employee
            WHERE is_active = 'Y'
            ORDER BY last_name, first_name, id
        """

        try:
            with connections["old_culet"].cursor() as cursor:
                cursor.execute(sql)

                columns = [
                    column[0]
                    for column in cursor.description
                ]

                return [
                    dict(zip(columns, values))
                    for values in cursor.fetchall()
                ]

        except Exception as exc:
            raise CommandError(
                "Could not read employees from the old_culet database. "
                "Confirm that the old_culet database connection is "
                "configured and reachable."
            ) from exc

    def process_employee(
        self,
        *,
        row,
        stats,
        dry_run,
        verbose,
    ):
        legacy_id = row["id"]
        first_name = (row["first_name"] or "").strip()
        last_name = (row["last_name"] or "").strip()
        pin = (row["pin"] or "").strip()

        display_name = (
            f"{first_name} {last_name}".strip()
            or f"Legacy employee {legacy_id}"
        )

        if not PIN_PATTERN.fullmatch(pin):
            stats["invalid_pin"] += 1

            self.stdout.write(
                self.style.ERROR(
                    f"INVALID PIN: legacy employee {legacy_id} — "
                    f"{display_name}"
                )
            )
            return

        mapping = (
            LegacyRecordMap.objects
            .filter(
                legacy_table="employee",
                legacy_id=legacy_id,
                object_id__isnull=False,
            )
            .order_by("-id")
            .first()
        )

        if mapping is None:
            stats["missing_mapping"] += 1

            self.stdout.write(
                self.style.ERROR(
                    f"NO MAPPING: legacy employee {legacy_id} — "
                    f"{display_name}"
                )
            )
            return

        employee = (
            Employee.objects
            .select_related(
                "user",
                "role",
            )
            .filter(pk=mapping.object_id)
            .first()
        )

        if employee is None:
            stats["missing_employee"] += 1

            self.stdout.write(
                self.style.ERROR(
                    f"MISSING EMPLOYEE: legacy employee {legacy_id} "
                    f"maps to missing Employee #{mapping.object_id}"
                )
            )
            return

        # Only change employees whose current Culet role is Hourly.
        if employee.role is None or employee.role.name != "Hourly":
            stats["non_hourly"] += 1

            if verbose:
                role_name = (
                    employee.role.name
                    if employee.role
                    else "No role"
                )

                self.stdout.write(
                    f"SKIP NON-HOURLY: {employee.user.username} — "
                    f"{role_name}"
                )

            return

        if not employee.user.is_active:
            stats["inactive_user"] += 1

            self.stdout.write(
                self.style.WARNING(
                    f"SKIP INACTIVE USER: {employee.user.username} — "
                    f"{display_name}"
                )
            )
            return

        password_already_matches = (
            employee.user.check_password(pin)
        )

        flag_already_false = (
            employee.must_change_password is False
        )

        if password_already_matches and flag_already_false:
            stats["unchanged"] += 1

            if verbose:
                self.stdout.write(
                    f"UNCHANGED: {employee.user.username} — "
                    f"{display_name}"
                )

            return

        if not dry_run:
            employee.user.set_password(pin)
            employee.user.save(
                update_fields=["password"]
            )

            if employee.must_change_password:
                employee.must_change_password = False
                employee.save(
                    update_fields=["must_change_password"]
                )

        stats["updated"] += 1

        action = "WOULD UPDATE" if dry_run else "UPDATED"

        self.stdout.write(
            self.style.SUCCESS(
                f"{action}: {employee.user.username} — "
                f"{display_name}"
            )
        )

    def print_summary(self, *, stats, dry_run):
        heading = (
            "Dry-run summary"
            if dry_run
            else "Password update summary"
        )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(heading))

        self.stdout.write(
            f"Active legacy employees: {stats['legacy_rows']:,}"
        )
        self.stdout.write(
            f"{'Would update' if dry_run else 'Updated'}: "
            f"{stats['updated']:,}"
        )
        self.stdout.write(
            f"Already correct:          {stats['unchanged']:,}"
        )
        self.stdout.write(
            f"Skipped non-hourly:       {stats['non_hourly']:,}"
        )
        self.stdout.write(
            f"Skipped inactive users:   {stats['inactive_user']:,}"
        )
        self.stdout.write(
            f"Invalid or missing PIN:   {stats['invalid_pin']:,}"
        )
        self.stdout.write(
            f"Missing legacy mapping:   {stats['missing_mapping']:,}"
        )
        self.stdout.write(
            f"Missing mapped Employee:  {stats['missing_employee']:,}"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "\nDry run complete. No passwords were changed."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nEmployee PIN passwords were updated successfully."
                )
            )