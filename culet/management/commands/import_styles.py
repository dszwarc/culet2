from collections import defaultdict
from pathlib import Path

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.spreadsheet import ReviewWorkbook
from culet.importer.utils import clean_text
from culet.models import Customer, LegacyRecordMap, Style


STYLE_SQL = """
    SELECT
        id,
        name,
        description,
        stamp
    FROM style
    ORDER BY id
"""


STYLE_CUSTOMER_USAGE_SQL = """
    SELECT
        style_id,
        customer_id,
        COUNT(*) AS total_jobs,
        SUM(
            CASE
                WHEN date_added >= '2026-01-01'
                 AND date_added < '2027-01-01'
                THEN 1
                ELSE 0
            END
        ) AS jobs_2026
    FROM project
    GROUP BY style_id, customer_id
"""


class Command(BaseImportCommand):
    help = (
        "Import approved styles from the old Culet database. The command "
        "uses the curated review workbook for IGNORE decisions, derives "
        "each style's customer from project history, merges duplicate style "
        "names, and preserves every legacy style ID through LegacyRecordMap."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--review-workbook",
            type=Path,
            help=(
                "Optional path to the style review workbook. Defaults to "
                "backups/ignore_old_culet_styles_and_parts.xlsx."
            ),
        )

    def run_import(self, *args, **options):
        workbook = ReviewWorkbook(options.get("review_workbook"))
        review_rows = workbook.read_sheet("Styles")

        approved_ids = {
            row.old_id
            for row in review_rows
            if not row.ignored and row.old_id is not None
        }
        ignored_ids = {
            row.old_id
            for row in review_rows
            if row.ignored and row.old_id is not None
        }
        reviewed_ids = approved_ids | ignored_ids

        rows = fetch_old_rows(STYLE_SQL)
        usage_rows = fetch_old_rows(STYLE_CUSTOMER_USAGE_SQL)
        total = len(rows)

        self.rows_by_id = {row["id"]: row for row in rows}
        self.normalized_name_by_id = {
            row["id"]: self.normalize_name(row.get("name"))
            for row in rows
        }

        self.canonical_row_by_name = self.build_canonical_rows(
            rows=rows,
            approved_ids=approved_ids,
        )
        self.customer_id_by_name = self.build_customer_preferences(
            usage_rows=usage_rows,
        )

        self.stdout.write(f"Found {total:,} old style rows.")
        self.stdout.write(
            f"Review workbook: {len(approved_ids):,} approved, "
            f"{len(ignored_ids):,} ignored."
        )
        self.stdout.write(
            f"Approved unique style names: "
            f"{len(self.canonical_row_by_name):,}."
        )

        # Import approved rows first. This guarantees that an ignored duplicate
        # can later map to the approved canonical Style rather than becoming an
        # unresolved skipped reference.
        approved_rows = [
            row for row in rows if row["id"] in approved_ids
        ]
        ignored_or_unreviewed_rows = [
            row for row in rows if row["id"] not in approved_ids
        ]

        processed = 0

        for row in approved_rows:
            processed += 1
            self.stats.processed += 1

            try:
                self.import_approved_style(row)
            except Exception as exc:
                self.record_error(
                    f"Style row {row.get('id')} could not be imported",
                    exc,
                )

                if self.fail_fast:
                    raise

            self.print_progress(processed, total, "Styles")

        for row in ignored_or_unreviewed_rows:
            processed += 1
            self.stats.processed += 1

            try:
                if row["id"] in ignored_ids:
                    self.handle_ignored_style(row)
                elif row["id"] not in reviewed_ids:
                    self.skip_style(
                        row["id"],
                        "Style was not listed in the review workbook.",
                    )
            except Exception as exc:
                self.record_error(
                    f"Style row {row.get('id')} could not be finalized",
                    exc,
                )

                if self.fail_fast:
                    raise

            self.print_progress(processed, total, "Styles")

    def build_canonical_rows(self, *, rows, approved_ids):
        """
        Select one deterministic source row for each approved style name.

        Duplicate names are merged because Style.name is unique. The approved
        row with the lowest legacy ID supplies the canonical name, description,
        and stamp. Every duplicate legacy ID maps to that same target object.
        """
        canonical = {}

        for row in rows:
            legacy_id = row["id"]

            if legacy_id not in approved_ids:
                continue

            normalized_name = self.normalize_name(row.get("name"))

            if not normalized_name:
                continue

            existing = canonical.get(normalized_name)

            if existing is None or legacy_id < existing["id"]:
                canonical[normalized_name] = row

        return canonical

    def build_customer_preferences(self, *, usage_rows):
        """
        Derive one customer per canonical style name.

        Usage is aggregated across all legacy style IDs having the same
        normalized name, including ignored duplicate rows that will map to the
        approved canonical Style.

        Selection priority:
          1. Highest number of jobs created in 2026.
          2. Highest number of jobs across all years.
          3. Lowest legacy customer ID as a deterministic tie-breaker.
        """
        totals_by_name = defaultdict(
            lambda: defaultdict(lambda: {"jobs_2026": 0, "total_jobs": 0})
        )

        for usage in usage_rows:
            style_id = usage.get("style_id")
            normalized_name = self.normalized_name_by_id.get(style_id)

            if not normalized_name:
                continue

            # Only calculate preferences for names that have an approved
            # canonical style.
            if normalized_name not in self.canonical_row_by_name:
                continue

            customer_id = usage.get("customer_id")

            if not customer_id:
                continue

            totals = totals_by_name[normalized_name][customer_id]
            totals["jobs_2026"] += int(usage.get("jobs_2026") or 0)
            totals["total_jobs"] += int(usage.get("total_jobs") or 0)

        preferred = {}

        for normalized_name, customer_totals in totals_by_name.items():
            customer_id, _ = max(
                customer_totals.items(),
                key=lambda item: (
                    item[1]["jobs_2026"],
                    item[1]["total_jobs"],
                    -int(item[0]),
                ),
            )
            preferred[normalized_name] = customer_id

        return preferred

    def import_approved_style(self, row):
        legacy_id = row["id"]
        normalized_name = self.normalized_name_by_id.get(legacy_id, "")

        if not normalized_name:
            self.skip_style(legacy_id, "Style name was blank.")
            return

        canonical_row = self.canonical_row_by_name[normalized_name]
        name = clean_text(canonical_row.get("name"))
        description = clean_text(canonical_row.get("description"))
        stamp = clean_text(canonical_row.get("stamp"))

        self.validate_lengths(
            legacy_id=legacy_id,
            name=name,
            description=description,
            stamp=stamp,
        )

        customer = self.resolve_customer(normalized_name)
        desired_values = {
            "customer": customer,
            "description": description or None,
            "stamp": stamp,
            "product": None,
        }

        try:
            with transaction.atomic(using="default"):
                style = self.get_mapped_object(
                    legacy_table="style",
                    legacy_id=legacy_id,
                    model_class=Style,
                )

                if style is None:
                    style = (
                        Style.objects
                        .filter(name__iexact=name)
                        .first()
                    )

                if style is None:
                    style = Style.objects.create(
                        name=name,
                        **desired_values,
                    )
                    self.stats.created += 1
                    action = LegacyRecordMap.ACTION_CREATED

                    self.row_message(
                        f'CREATE old style {legacy_id}: "{name}"'
                    )
                else:
                    style, changed_fields = self.update_style(
                        style=style,
                        desired_name=name,
                        desired_values=desired_values,
                    )

                    if changed_fields:
                        style.save(update_fields=changed_fields)
                        self.stats.updated += 1
                        action = LegacyRecordMap.ACTION_UPDATED

                        self.row_message(
                            f'UPDATE old style {legacy_id}: "{name}" '
                            f'({", ".join(changed_fields)})'
                        )
                    else:
                        self.stats.unchanged += 1
                        action = LegacyRecordMap.ACTION_UNCHANGED

                        if legacy_id == canonical_row["id"]:
                            verb = "UNCHANGED"
                        else:
                            verb = "MAP DUPLICATE"

                        self.row_message(
                            f'{verb} old style {legacy_id}: "{name}" '
                            f'→ Style #{style.pk}'
                        )

                duplicate_message = ""
                if legacy_id != canonical_row["id"]:
                    duplicate_message = (
                        f"Duplicate style name merged with canonical legacy "
                        f"style {canonical_row['id']}."
                    )

                self.record_mapping(
                    legacy_table="style",
                    legacy_id=legacy_id,
                    target=style,
                    action=action,
                    message=duplicate_message,
                )

        except IntegrityError as exc:
            raise ValueError(
                f'Integrity error importing style {legacy_id}: "{name}"'
            ) from exc

    def update_style(self, *, style, desired_name, desired_values):
        changed_fields = []

        if style.name != desired_name:
            conflict = (
                Style.objects
                .filter(name__iexact=desired_name)
                .exclude(pk=style.pk)
                .first()
            )

            if conflict is not None:
                # A previously mapped object now conflicts with the canonical
                # natural key. Remap this legacy ID to the canonical object.
                style = conflict
            else:
                style.name = desired_name
                changed_fields.append("name")

        for field_name, desired_value in desired_values.items():
            current_value = getattr(style, field_name)

            if current_value != desired_value:
                setattr(style, field_name, desired_value)
                changed_fields.append(field_name)

        return style, changed_fields

    def handle_ignored_style(self, row):
        legacy_id = row["id"]
        normalized_name = self.normalized_name_by_id.get(legacy_id, "")
        canonical_row = self.canonical_row_by_name.get(normalized_name)

        if canonical_row is None:
            self.skip_style(
                legacy_id,
                "Style was marked IGNORE in the review workbook.",
            )
            return

        canonical_style = self.get_mapped_object(
            legacy_table="style",
            legacy_id=canonical_row["id"],
            model_class=Style,
        )

        if canonical_style is None:
            raise ValueError(
                f"Ignored duplicate style {legacy_id} could not map to "
                f"approved canonical legacy style {canonical_row['id']}."
            )

        self.record_mapping(
            legacy_table="style",
            legacy_id=legacy_id,
            target=canonical_style,
            action=LegacyRecordMap.ACTION_UNCHANGED,
            message=(
                "Style was marked IGNORE, but its duplicate name maps to "
                f"approved canonical legacy style {canonical_row['id']}."
            ),
        )
        self.stats.unchanged += 1

        self.row_message(
            f'MAP IGNORED DUPLICATE old style {legacy_id}: '
            f'"{clean_text(row.get("name"))}" → '
            f'Style #{canonical_style.pk}'
        )

    def resolve_customer(self, normalized_name):
        legacy_customer_id = self.customer_id_by_name.get(normalized_name)

        if not legacy_customer_id:
            return None

        customer = self.get_mapped_object(
            legacy_table="customer",
            legacy_id=legacy_customer_id,
            model_class=Customer,
        )

        if customer is None:
            raise ValueError(
                f"No imported Customer mapping exists for preferred legacy "
                f"customer ID {legacy_customer_id}. Run import_customers first."
            )

        return customer

    def validate_lengths(self, *, legacy_id, name, description, stamp):
        limits = {
            "name": Style._meta.get_field("name").max_length,
            "description": Style._meta.get_field("description").max_length,
            "stamp": Style._meta.get_field("stamp").max_length,
        }
        values = {
            "name": name,
            "description": description,
            "stamp": stamp,
        }

        for field_name, max_length in limits.items():
            value = values[field_name]

            if max_length and len(value) > max_length:
                raise ValueError(
                    f"Style {legacy_id} {field_name} exceeds the current "
                    f"{max_length}-character limit."
                )

    def skip_style(self, legacy_id, reason):
        self.stats.skipped += 1

        self.record_skipped(
            legacy_table="style",
            legacy_id=legacy_id,
            message=reason,
        )

        self.row_message(f"SKIP old style {legacy_id}: {reason}")

    @staticmethod
    def normalize_name(value):
        return clean_text(value).casefold()