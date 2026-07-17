from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.models import (
    Job,
    JobStone,
    LegacyRecordMap,
    StoneShape,
    StoneType,
)


JOB_STONE_SQL = """
    SELECT
        id,
        project_id,
        stone_type_id,
        stone_shape_id,
        size,
        weight,
        qty,
        date_release,
        date_added,
        user_id,
        employee_id
    FROM stone
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import legacy job stone requirements from stone while preserving "
        "every source row through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(JOB_STONE_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} legacy stone rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_job_stone(row)
            except Exception as exc:
                self.record_error(
                    f"stone row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(index, total, "Job stones")

    def import_job_stone(self, row):
        legacy_id = int(row["id"])
        legacy_project_id = row.get("project_id")
        legacy_stone_type_id = row.get("stone_type_id")
        legacy_stone_shape_id = row.get("stone_shape_id")

        job = self.resolve_mapping(
            legacy_table="project",
            legacy_id=legacy_project_id,
            model_class=Job,
        )
        if job is None:
            self.skip_row(
                legacy_id,
                f"No imported Job mapping for legacy project {legacy_project_id}.",
            )
            return

        stone_type = self.resolve_mapping(
            legacy_table="stone_type",
            legacy_id=legacy_stone_type_id,
            model_class=StoneType,
        )
        if stone_type is None:
            self.skip_row(
                legacy_id,
                "No imported StoneType mapping for legacy stone type "
                f"{legacy_stone_type_id}.",
            )
            return

        stone_shape = self.resolve_mapping(
            legacy_table="stone_shape",
            legacy_id=legacy_stone_shape_id,
            model_class=StoneShape,
        )
        if stone_shape is None:
            self.skip_row(
                legacy_id,
                "No imported StoneShape mapping for legacy stone shape "
                f"{legacy_stone_shape_id}.",
            )
            return

        qty = self.clean_qty(row.get("qty"))
        if qty is None:
            self.skip_row(
                legacy_id,
                f"Legacy quantity {row.get('qty')!r} was zero or invalid.",
            )
            return

        stone_size = self.clean_size(row.get("size"))

        desired = {
            "job": job,
            "stone_type": stone_type,
            "stone_shape": stone_shape,
            "stone_size": stone_size,
            "qty_req": qty,
        }

        try:
            with transaction.atomic(using="default"):
                target = self.get_mapped_object(
                    legacy_table="stone",
                    legacy_id=legacy_id,
                    model_class=JobStone,
                )

                if target is None:
                    # Reuse an exact existing requirement on reruns or where
                    # duplicate legacy rows describe the same job component.
                    target = (
                        JobStone.objects
                        .filter(**desired)
                        .order_by("id")
                        .first()
                    )

                target, action, changed_fields = self.save_target(
                    target=target,
                    desired=desired,
                )

                message = self.build_mapping_message(row)

                self.record_mapping(
                    legacy_table="stone",
                    legacy_id=legacy_id,
                    target=target,
                    action=action,
                    message=message,
                )

                self.row_message(
                    self.render_result(
                        action=action,
                        legacy_id=legacy_id,
                        target=target,
                        changed_fields=changed_fields,
                    )
                )

        except IntegrityError as exc:
            raise ValueError(
                f"Integrity error importing stone {legacy_id}."
            ) from exc

    def resolve_mapping(self, *, legacy_table, legacy_id, model_class):
        if not legacy_id:
            return None

        return self.get_mapped_object(
            legacy_table=legacy_table,
            legacy_id=int(legacy_id),
            model_class=model_class,
        )

    def clean_qty(self, value):
        try:
            qty = int(value)
        except (TypeError, ValueError):
            return None

        return qty if qty > 0 else None

    def clean_size(self, value):
        """
        Preserve the legacy two-decimal millimeter size as text.

        Examples:
            1.20 -> "1.20"
            0.00 -> "0.00"
        """
        if value in (None, ""):
            return None

        try:
            size = Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            return None

        text = format(size, "f")
        max_length = JobStone._meta.get_field("stone_size").max_length
        return text[:max_length]

    def clean_decimal_for_message(self, value):
        if value in (None, ""):
            return "blank"

        try:
            return format(Decimal(str(value)), "f")
        except (InvalidOperation, TypeError, ValueError):
            return repr(value)

    def build_mapping_message(self, row):
        weight = self.clean_decimal_for_message(row.get("weight"))
        date_release = row.get("date_release") or "blank"
        date_added = row.get("date_added") or "blank"
        user_id = row.get("user_id") or 0
        employee_id = row.get("employee_id") or 0

        return (
            f"Legacy stone weight={weight}; JobStone has no weight field, "
            "so the value was not imported. "
            f"Legacy date_release={date_release}; date_added={date_added}; "
            f"user_id={user_id}; employee_id={employee_id}."
        )

    def save_target(self, *, target, desired):
        if target is None:
            target = JobStone.objects.create(**desired)
            self.stats.created += 1
            return target, LegacyRecordMap.ACTION_CREATED, []

        changed_fields = []

        for field_name, desired_value in desired.items():
            current_value = getattr(target, field_name)
            if current_value != desired_value:
                setattr(target, field_name, desired_value)
                changed_fields.append(field_name)

        if changed_fields:
            target.save(update_fields=changed_fields)
            self.stats.updated += 1
            action = LegacyRecordMap.ACTION_UPDATED
        else:
            self.stats.unchanged += 1
            action = LegacyRecordMap.ACTION_UNCHANGED

        return target, action, changed_fields

    def skip_row(self, legacy_id, reason):
        self.stats.skipped += 1
        self.record_skipped(
            legacy_table="stone",
            legacy_id=legacy_id,
            message=reason,
        )
        self.row_message(f"SKIP stone {legacy_id}: {reason}")

    def render_result(
        self,
        *,
        action,
        legacy_id,
        target,
        changed_fields,
    ):
        verb = {
            LegacyRecordMap.ACTION_CREATED: "CREATE",
            LegacyRecordMap.ACTION_UPDATED: "UPDATE",
            LegacyRecordMap.ACTION_UNCHANGED: "UNCHANGED",
        }[action]

        detail = ""
        if changed_fields:
            detail = f" ({', '.join(changed_fields)})"

        return (
            f"{verb} stone {legacy_id} → JobStone #{target.pk}: "
            f"{target.job.stock_num} / {target.stone_type} / "
            f"{target.stone_shape} / {target.stone_size} mm × "
            f"{target.qty_req}{detail}"
        )