from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import Job, JobMetal, LegacyRecordMap, MetalPart, MetalType


PROJECT_PART_NUMBER_SQL = """
    SELECT
        ppn.id,
        ppn.project_id,
        ppn.metal_lot_id,
        ppn.weight,
        ppn.qty,
        ppn.cost,
        ppn.date_added,
        ppn.is_deleted,
        ppn.is_addition,
        ml.part_number_id,
        ml.metal_type_id,
        pn.part_number AS part_number_text
    FROM project_part_number ppn
    INNER JOIN project p
        ON p.id = ppn.project_id
    LEFT JOIN metal_lot ml
        ON ml.id = ppn.metal_lot_id
    LEFT JOIN part_number pn
        ON pn.id = ml.part_number_id
    WHERE p.date_added >= %s
      AND p.date_added < %s
    ORDER BY ppn.id
"""


METAL_SQL = """
    SELECT
        m.id,
        m.project_id,
        m.metal_type_id,
        m.weight,
        m.qty,
        m.origin,
        m.cost,
        m.part_number,
        m.user_id,
        m.date_added,
        m.description,
        m.employee_id,
        pn.id AS matched_part_number_id
    FROM metal m
    INNER JOIN project p
        ON p.id = m.project_id
    LEFT JOIN part_number pn
        ON TRIM(m.part_number) <> ''
       AND LOWER(TRIM(pn.part_number)) = LOWER(TRIM(m.part_number))
    WHERE p.date_added >= %s
      AND p.date_added < %s
    ORDER BY m.id
"""


class Command(BaseImportCommand):
    help = (
        "Import 2026 legacy job metal requirements from project_part_number "
        "and metal. Catalog-part requirements are imported into JobMetal; "
        "legacy lot allocation and cost details are retained only as audit "
        "information in LegacyRecordMap."
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
        start_date = options["start_date"]
        end_date = options["end_date"]

        part_rows = fetch_old_rows(
            PROJECT_PART_NUMBER_SQL,
            [start_date, end_date],
        )
        metal_rows = fetch_old_rows(
            METAL_SQL,
            [start_date, end_date],
        )

        self.stdout.write(
            f"Found {len(part_rows):,} project_part_number rows and "
            f"{len(metal_rows):,} metal rows in the selected project range."
        )

        # A requirement key is the old project plus old catalog part. Multiple
        # lot rows for the same part are intentionally consolidated because
        # JobMetal describes the requirement, not its inventory allocation.
        groups = defaultdict(
            lambda: {
                "part_rows": [],
                "metal_rows": [],
                "part_number_id": None,
                "metal_type_ids": set(),
            }
        )

        processed = 0
        total = len(part_rows) + len(metal_rows)

        for row in part_rows:
            processed += 1
            self.stats.processed += 1

            if self.is_yes(row.get("is_deleted")):
                self.skip_source(
                    "project_part_number",
                    row["id"],
                    "Legacy project-part allocation was marked deleted.",
                )
                self.print_progress(processed, total, "Job metals")
                continue

            part_id = row.get("part_number_id")
            if not part_id:
                self.skip_source(
                    "project_part_number",
                    row["id"],
                    f"Metal lot {row.get('metal_lot_id')} had no catalog part.",
                )
                self.print_progress(processed, total, "Job metals")
                continue

            key = (int(row["project_id"]), int(part_id))
            groups[key]["part_rows"].append(row)
            groups[key]["part_number_id"] = int(part_id)
            if row.get("metal_type_id"):
                groups[key]["metal_type_ids"].add(int(row["metal_type_id"]))

            self.print_progress(processed, total, "Job metals")

        for row in metal_rows:
            processed += 1
            self.stats.processed += 1

            matched_part_id = row.get("matched_part_number_id")
            if not matched_part_id:
                part_text = clean_text(row.get("part_number"))
                reason = (
                    "Legacy metal row had no catalog part number; JobMetal.part "
                    "is required, so the generic metal entry was not imported."
                    if not part_text
                    else f"No imported catalog part matched {part_text!r}."
                )
                self.skip_source("metal", row["id"], reason)
                self.print_progress(processed, total, "Job metals")
                continue

            key = (int(row["project_id"]), int(matched_part_id))
            groups[key]["metal_rows"].append(row)
            groups[key]["part_number_id"] = int(matched_part_id)
            if row.get("metal_type_id"):
                groups[key]["metal_type_ids"].add(int(row["metal_type_id"]))

            self.print_progress(processed, total, "Job metals")

        self.stdout.write(
            f"Consolidated usable rows into {len(groups):,} job/part requirements."
        )

        for index, ((legacy_project_id, legacy_part_id), group) in enumerate(
            groups.items(),
            start=1,
        ):
            try:
                self.import_group(
                    legacy_project_id=legacy_project_id,
                    legacy_part_id=legacy_part_id,
                    group=group,
                )
            except Exception as exc:
                self.record_error(
                    "Job metal group for project "
                    f"{legacy_project_id}, part {legacy_part_id} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(index, len(groups), "Job metal groups")

    def import_group(self, *, legacy_project_id, legacy_part_id, group):
        job = self.get_mapped_object(
            legacy_table="project",
            legacy_id=legacy_project_id,
            model_class=Job,
        )
        if job is None:
            self.skip_group_rows(
                group,
                f"No imported Job mapping for legacy project {legacy_project_id}.",
            )
            return

        part = self.get_mapped_object(
            legacy_table="part_number",
            legacy_id=legacy_part_id,
            model_class=MetalPart,
        )
        if part is None:
            self.skip_group_rows(
                group,
                f"Metal part {legacy_part_id} was not imported or was ignored.",
            )
            return

        metal_type, metal_type_message = self.resolve_metal_type(group)

        # project_part_number represents concrete catalog-part usage and is the
        # preferred source when present. The old metal table is used when no
        # project_part_number rows exist; when both exist it enriches audit
        # information but is not added again, avoiding double counting.
        if group["part_rows"]:
            qty_req = self.sum_positive_ints(
                row.get("qty") for row in group["part_rows"]
            )
            weight_req = self.sum_weights(
                row.get("weight") for row in group["part_rows"]
            )
            value_source = "project_part_number"
        else:
            qty_req = self.sum_positive_ints(
                row.get("qty") for row in group["metal_rows"]
            )
            weight_req = self.sum_weights(
                row.get("weight") for row in group["metal_rows"]
            )
            value_source = "metal"

        desired = {
            "job": job,
            "part": part,
            "qty_req": qty_req,
            "weight_req": weight_req,
            "metal_type": metal_type,
        }

        with transaction.atomic(using="default"):
            target = self.find_existing_target(group)

            if target is None:
                target = (
                    JobMetal.objects
                    .filter(job=job, part=part)
                    .order_by("id")
                    .first()
                )

            target, action, changed_fields = self.save_target(
                target=target,
                desired=desired,
            )

            overlap_message = ""
            if group["part_rows"] and group["metal_rows"]:
                overlap_message = (
                    " Both source tables described this job/part; quantities "
                    "and weight came from project_part_number to avoid double counting."
                )

            base_message = (
                f"Consolidated into JobMetal #{target.pk}; requirement values "
                f"came from {value_source}.{overlap_message} {metal_type_message}"
            ).strip()

            for row in group["part_rows"]:
                message = (
                    f"{base_message} Legacy metal_lot_id={row.get('metal_lot_id')!r}, "
                    f"cost={row.get('cost')!r}, is_addition={row.get('is_addition')!r}; "
                    "lot allocation and cost were not imported."
                )
                self.record_mapping(
                    legacy_table="project_part_number",
                    legacy_id=int(row["id"]),
                    target=target,
                    action=action,
                    message=message,
                )

            for row in group["metal_rows"]:
                message = (
                    f"{base_message} Legacy origin={row.get('origin')!r}, "
                    f"cost={row.get('cost')!r}, user_id={row.get('user_id')!r}, "
                    f"employee_id={row.get('employee_id')!r}, "
                    f"description={clean_text(row.get('description'))!r}; "
                    "audit/cost fields were not imported."
                )
                self.record_mapping(
                    legacy_table="metal",
                    legacy_id=int(row["id"]),
                    target=target,
                    action=action,
                    message=message,
                )

            self.row_message(
                f"{action}: legacy project {legacy_project_id}, part "
                f"{legacy_part_id} -> JobMetal #{target.pk}"
                + (f"; changed {', '.join(changed_fields)}" if changed_fields else "")
            )

    def find_existing_target(self, group):
        for table_name, rows in (
            ("project_part_number", group["part_rows"]),
            ("metal", group["metal_rows"]),
        ):
            for row in rows:
                target = self.get_mapped_object(
                    legacy_table=table_name,
                    legacy_id=int(row["id"]),
                    model_class=JobMetal,
                )
                if target is not None:
                    return target
        return None

    def resolve_metal_type(self, group):
        old_ids = sorted(group["metal_type_ids"])
        resolved = []

        for old_id in old_ids:
            target = self.get_mapped_object(
                legacy_table="metal_type",
                legacy_id=old_id,
                model_class=MetalType,
            )
            if target is not None and target.pk not in {item.pk for item in resolved}:
                resolved.append(target)

        if len(resolved) == 1:
            return resolved[0], f"Resolved metal type from legacy IDs {old_ids}."
        if len(resolved) > 1:
            return (
                resolved[0],
                "Multiple metal types were associated with the consolidated "
                f"requirement ({old_ids}); selected {resolved[0]} deterministically.",
            )
        if old_ids:
            return None, f"No MetalType mapping was available for legacy IDs {old_ids}."
        return None, "No legacy metal type was supplied."

    def save_target(self, *, target, desired):
        if target is None:
            target = JobMetal.objects.create(**desired)
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
            return target, LegacyRecordMap.ACTION_UPDATED, changed_fields

        self.stats.unchanged += 1
        return target, LegacyRecordMap.ACTION_UNCHANGED, []

    def skip_group_rows(self, group, reason):
        for row in group["part_rows"]:
            self.skip_source("project_part_number", row["id"], reason)
        for row in group["metal_rows"]:
            self.skip_source("metal", row["id"], reason)

    def skip_source(self, legacy_table, legacy_id, message):
        self.record_skipped(
            legacy_table=legacy_table,
            legacy_id=int(legacy_id),
            message=message,
        )
        self.stats.skipped += 1
        self.row_message(f"skipped: {legacy_table} #{legacy_id}: {message}")

    @staticmethod
    def is_yes(value):
        return str(value or "").strip().upper() in {"Y", "YES", "1", "TRUE"}

    @staticmethod
    def sum_positive_ints(values):
        total = 0
        found = False
        for value in values:
            try:
                number = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if not number.is_finite() or number <= 0:
                continue
            total += int(number)
            found = True
        return total if found and total > 0 else None

    @staticmethod
    def sum_weights(values):
        total = Decimal("0")
        found = False
        for value in values:
            try:
                number = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if not number.is_finite() or number <= 0:
                continue
            total += number
            found = True
        if not found:
            return None
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)