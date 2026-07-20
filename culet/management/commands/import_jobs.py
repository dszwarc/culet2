from __future__ import annotations

from datetime import date, datetime

from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text, to_boolean
from culet.models import (
    Customer,
    Employee,
    Job,
    JobStatus,
    LegacyRecordMap,
    Style,
)


JOB_SQL = """
    SELECT
        p.id,
        p.rfid,
        p.employee_id AS project_employee_id,
        p.customer_id,
        p.date_added,
        p.date_due,
        p.customer_reference_number,
        p.is_shipped,
        p.notes,
        p.stamp,
        p.ring_size,
        p.qty,
        p.style_id,
        p.is_repair,
        p.is_rework,
        p.is_import,
        p.metal_type_id,
        (
            SELECT pr.employee_id
            FROM progress pr
            WHERE pr.project_id = p.id
              AND pr.action_id IN (2, 39)
              AND pr.employee_id > 0
            ORDER BY pr.date_added DESC, pr.id DESC
            LIMIT 1
        ) AS last_assigned_employee_id,
        (
            SELECT pr.date_added
            FROM progress pr
            WHERE pr.project_id = p.id
              AND pr.action_id IN (2, 39)
              AND pr.employee_id > 0
            ORDER BY pr.date_added DESC, pr.id DESC
            LIMIT 1
        ) AS last_assigned_at,
        (
            SELECT pr.action_id
            FROM progress pr
            WHERE pr.project_id = p.id
              AND pr.action_id IN (2, 39)
              AND pr.employee_id > 0
            ORDER BY pr.date_added DESC, pr.id DESC
            LIMIT 1
        ) AS last_assignment_action_id
    FROM project p
    WHERE p.date_added >= %s
      AND p.date_added < %s
    ORDER BY p.id
"""


class Command(BaseImportCommand):
    help = (
        "Import old Culet projects as Jobs for a selected date range. "
        "Both assigned_to and holder are set to the most recently assigned "
        "legacy employee. Projects with unknown customers or styles are "
        "preserved using UNKNOWN and LEGACY-UNKNOWN placeholders."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--start-date",
            default="2026-01-01",
            help="Inclusive legacy project date_added boundary (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end-date",
            default="2027-01-01",
            help="Exclusive legacy project date_added boundary (YYYY-MM-DD).",
        )

    def run_import(self, *args, **options):
        start_date = self.parse_date(options["start_date"], "--start-date")
        end_date = self.parse_date(options["end_date"], "--end-date")

        if end_date <= start_date:
            raise CommandError("--end-date must be later than --start-date.")

        self.open_status = self.get_or_create_status("Imported", sort_order=10)
        self.shipped_status = self.get_or_create_status(
            "Shipped",
            sort_order=100,
        )

        rows = fetch_old_rows(
            JOB_SQL,
            [start_date.isoformat(), end_date.isoformat()],
        )
        total = len(rows)

        self.stdout.write(
            f"Found {total:,} legacy projects from {start_date} "
            f"through {end_date} (exclusive)."
        )

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1
            try:
                self.import_job(row)
            except Exception as exc:
                self.record_error(
                    f"Project row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(index, total, "Jobs")

    def import_job(self, row):
        old_id = int(row["id"])

        customer, customer_message = self.resolve_customer(row)
        style, style_message = self.resolve_style(
            row=row,
            customer=customer,
        )

        employee, employee_message = self.resolve_last_employee(row)
        shipped = to_boolean(row.get("is_shipped"))
        created_at = self.coerce_datetime(row.get("date_added"))
        due_date = self.coerce_due_date(
            row.get("date_due"),
            created_at,
        )

        # The physical Code 128 labels contain project.id, not project.rfid.
        barcode, barcode_message = self.prepare_barcode(
            row.get("id"),
            old_id=old_id,
        )
        stock_num, stock_message = self.prepare_stock_num(
            row.get("customer_reference_number"),
            old_id=old_id,
        )

        defaults = {
            "name": self.truncate(
                style.name or f"Legacy Job {old_id}",
                80,
            ),
            "customer": customer,
            "barcode": barcode,
            "stock_num": stock_num,
            "size": self.truncate(
                clean_text(row.get("ring_size")),
                80,
            ),
            "stamp": self.truncate(
                clean_text(row.get("stamp")),
                80,
            ),
            "notes": clean_text(row.get("notes")),
            "active": True,
            "shipped": shipped,
            "in_work": False,
            "style": style,
            "due": due_date,
            "assigned_to": employee,
            "holder": employee,
            "location": None,
            "status": (
                self.shipped_status
                if shipped
                else self.open_status
            ),
            "is_piecework": False,
            "is_repair": to_boolean(row.get("is_repair")),
        }

        with transaction.atomic(using="default"):
            job = self.get_mapped_object(
                legacy_table="project",
                legacy_id=old_id,
                model_class=Job,
            )

            if job is None:
                job = Job.objects.create(
                    created=created_at,
                    **defaults,
                )
                action = LegacyRecordMap.ACTION_CREATED
                self.stats.created += 1
            else:
                changed = self.apply_changes(job, defaults)

                # `created` is editable=False, but migration imports should retain
                # the actual legacy creation timestamp.
                if created_at and job.created != created_at:
                    Job.objects.filter(pk=job.pk).update(
                        created=created_at,
                    )
                    job.created = created_at
                    changed = True

                if changed:
                    job.save()
                    action = LegacyRecordMap.ACTION_UPDATED
                    self.stats.updated += 1
                else:
                    action = LegacyRecordMap.ACTION_UNCHANGED
                    self.stats.unchanged += 1

            message_parts = [
                customer_message,
                style_message,
                employee_message,
                barcode_message,
                stock_message,
                f"legacy rfid={row.get('rfid')}",
                f"legacy qty={row.get('qty')}",
                f"legacy is_rework={row.get('is_rework')}",
                f"legacy is_import={row.get('is_import')}",
                f"legacy metal_type_id={row.get('metal_type_id')}",
            ]
            message = "; ".join(
                part for part in message_parts if part
            )

            # Remove an earlier skipped record for this project before creating
            # the authoritative project -> Job mapping.
            LegacyRecordMap.objects.filter(
                legacy_table="project",
                legacy_id=old_id,
                content_type__isnull=True,
            ).delete()

            self.record_mapping(
                legacy_table="project",
                legacy_id=old_id,
                target=job,
                action=action,
                message=message,
            )

        self.row_message(
            f"{action.upper()} project {old_id} -> Job {job.pk} "
            f"({job.stock_num or 'no stock number'}; "
            f"customer={customer}; "
            f"style={style}; "
            f"employee={employee or 'none'})"
        )

    def resolve_customer(self, row):
        """
        Resolve the mapped legacy customer.

        When the old customer is blank, zero, or unmapped, retain the project
        by assigning the shared UNKNOWN customer rather than skipping it.
        """
        legacy_customer_id = self.positive_int(row.get("customer_id"))

        customer = None
        if legacy_customer_id is not None:
            customer = self.get_mapped_object(
                legacy_table="customer",
                legacy_id=legacy_customer_id,
                model_class=Customer,
            )

        if customer is not None:
            return customer, ""

        customer, created = Customer.objects.get_or_create(
            name="UNKNOWN",
            defaults={
                "address": "",
                "email": "",
                "phone": "",
                "number": None,
            },
        )

        source_id = legacy_customer_id or 0
        action = "created" if created else "used"
        message = (
            f"Legacy customer #{source_id} had no mapping; "
            f"{action} UNKNOWN customer."
        )
        return customer, message

    def resolve_style(self, *, row, customer):
        """
        Resolve the mapped legacy style.

        Missing styles use a placeholder attached to the resolved customer.
        Style.name is globally unique, so known customers receive a unique
        LEGACY-UNKNOWN-C<customer_pk> name. The UNKNOWN customer receives the
        exact name LEGACY-UNKNOWN.
        """
        legacy_style_id = self.positive_int(row.get("style_id"))

        style = None
        if legacy_style_id is not None:
            style = self.get_mapped_object(
                legacy_table="style",
                legacy_id=legacy_style_id,
                model_class=Style,
            )

        if style is not None:
            return style, ""

        if customer.name.strip().upper() == "UNKNOWN":
            placeholder_name = "LEGACY-UNKNOWN"
        else:
            placeholder_name = f"LEGACY-UNKNOWN-C{customer.pk}"

        placeholder_name = self.truncate(placeholder_name, 50)
        description = (
            "Placeholder style created during legacy migration for projects "
            f"using unmapped legacy style #{legacy_style_id or 0}. "
            f"Customer: {customer.name}."
        )

        style, created = Style.objects.get_or_create(
            name=placeholder_name,
            defaults={
                "customer": customer,
                "stamp": "",
                "description": description,
                "product": None,
            },
        )

        changed_fields = []

        if style.customer_id != customer.pk:
            style.customer = customer
            changed_fields.append("customer")

        if not style.description:
            style.description = description
            changed_fields.append("description")

        if changed_fields:
            style.save(update_fields=changed_fields)

        source_id = legacy_style_id or 0
        action = "created" if created else "used"
        message = (
            f"Legacy style #{source_id} had no mapping; "
            f"{action} placeholder style {style.name!r} attached to "
            f"customer {customer.name!r}."
        )
        return style, message

    def resolve_last_employee(self, row):
        assigned_old_id = row.get("last_assigned_employee_id")
        source = "progress action 2/39"

        if not assigned_old_id:
            assigned_old_id = row.get("project_employee_id")
            source = "project.employee_id fallback"

        if not assigned_old_id:
            return None, "No legacy assigned employee."

        employee = self.get_mapped_object(
            legacy_table="employee",
            legacy_id=int(assigned_old_id),
            model_class=Employee,
        )

        if employee is None:
            return (
                None,
                f"Legacy employee #{assigned_old_id} from {source} had no "
                "mapping; assigned_to and holder left blank.",
            )

        assigned_at = row.get("last_assigned_at")
        detail = (
            f"Last employee #{assigned_old_id} selected from {source}"
        )
        if assigned_at:
            detail += f" at {assigned_at}"
        detail += "; assigned_to and holder both set to that employee."
        return employee, detail

    def prepare_barcode(self, value, *, old_id):
        text = clean_text(value)
        if not text:
            return None, "Legacy project ID was blank; barcode left blank."

        if not text.isdigit():
            return (
                None,
                f"Legacy project ID {text!r} was nonnumeric; "
                "barcode left blank.",
            )

        barcode = int(text)
        if barcode > 2_147_483_647:
            return (
                None,
                f"Legacy project ID {text!r} exceeded IntegerField range; "
                "barcode left blank.",
            )

        conflict = Job.objects.filter(barcode=barcode).exclude(
            pk=self.mapped_job_pk(old_id)
        ).exists()
        if conflict:
            return (
                None,
                f"Legacy project ID {text!r} conflicted with an existing "
                "barcode; left blank.",
            )

        return barcode, ""

    def prepare_stock_num(self, value, *, old_id):
        raw = clean_text(value)
        candidate = raw or f"LEGACY-{old_id}"
        candidate = self.truncate(candidate, 50)

        mapped_pk = self.mapped_job_pk(old_id)
        if not Job.objects.filter(stock_num=candidate).exclude(
            pk=mapped_pk
        ).exists():
            message = (
                ""
                if raw
                else "Blank legacy customer reference replaced with "
                "generated stock number."
            )
            return candidate, message

        suffix = f"-{old_id}"
        candidate = f"{candidate[:50 - len(suffix)]}{suffix}"
        return (
            candidate,
            "Duplicate legacy customer reference was made unique with the "
            "legacy project ID.",
        )

    def mapped_job_pk(self, old_id):
        job = self.get_mapped_object(
            legacy_table="project",
            legacy_id=old_id,
            model_class=Job,
        )
        return job.pk if job else None

    def get_or_create_status(self, name, *, sort_order):
        status = JobStatus.objects.filter(name__iexact=name).first()
        if status:
            return status
        return JobStatus.objects.create(
            name=name,
            active=True,
            sort_order=sort_order,
        )

    @staticmethod
    def apply_changes(instance, defaults):
        changed = False
        for field, value in defaults.items():
            if getattr(instance, field) != value:
                setattr(instance, field, value)
                changed = True
        return changed

    @staticmethod
    def truncate(value, max_length):
        return clean_text(value)[:max_length]

    @staticmethod
    def positive_int(value):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def parse_date(value, option_name):
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise CommandError(
                f"{option_name} must use YYYY-MM-DD format."
            ) from exc

    @staticmethod
    def coerce_datetime(value):
        if value is None:
            return timezone.now()
        if isinstance(value, datetime):
            if timezone.is_naive(value):
                return timezone.make_aware(
                    value,
                    timezone.get_current_timezone(),
                )
            return value
        parsed = datetime.fromisoformat(str(value))
        if timezone.is_naive(parsed):
            return timezone.make_aware(
                parsed,
                timezone.get_current_timezone(),
            )
        return parsed

    @staticmethod
    def coerce_due_date(value, created_at):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value:
            try:
                return datetime.fromisoformat(str(value)).date()
            except ValueError:
                pass
        return created_at.date()