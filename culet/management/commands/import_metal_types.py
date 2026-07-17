from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import LegacyRecordMap, MetalType


METAL_TYPE_SQL = """
    SELECT
        id,
        name,
        abbreviation
    FROM metal_type
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import metal types from the old Culet database while preserving "
        "legacy IDs through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(METAL_TYPE_SQL)
        total = len(rows)

        self.stdout.write(
            f"Found {total:,} old metal type rows."
        )

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_metal_type(row)
            except Exception as exc:
                self.record_error(
                    f"Metal type row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Metal types")

    def import_metal_type(self, row):
        legacy_id = row["id"]
        name = clean_text(row["name"])

        if not name:
            reason = "Metal type name was blank."

            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="metal_type",
                legacy_id=legacy_id,
                message=reason,
            )

            self.row_message(
                f"SKIP old metal type {legacy_id}: {reason}"
            )
            return

        max_length = MetalType._meta.get_field("name").max_length

        if len(name) > max_length:
            original_name = name
            name = name[:max_length]

            self.row_message(
                f'TRUNCATE old metal type {legacy_id}: '
                f'"{original_name}" → "{name}"'
            )

        try:
            with transaction.atomic(using="default"):
                mapped_metal_type = self.get_mapped_object(
                    legacy_table="metal_type",
                    legacy_id=legacy_id,
                    model_class=MetalType,
                )

                if mapped_metal_type is not None:
                    self.update_mapped_metal_type(
                        legacy_id=legacy_id,
                        metal_type=mapped_metal_type,
                        desired_name=name,
                    )
                    return

                existing_metal_type = (
                    MetalType.objects
                    .filter(name__iexact=name)
                    .first()
                )

                if existing_metal_type is not None:
                    self.map_existing_metal_type(
                        legacy_id=legacy_id,
                        metal_type=existing_metal_type,
                        source_name=name,
                    )
                    return

                self.create_metal_type(
                    legacy_id=legacy_id,
                    name=name,
                )

        except IntegrityError as exc:
            raise ValueError(
                f'Integrity error importing metal type '
                f'{legacy_id}: "{name}"'
            ) from exc

    def create_metal_type(self, *, legacy_id, name):
        metal_type = MetalType.objects.create(
            name=name,
        )

        self.record_mapping(
            legacy_table="metal_type",
            legacy_id=legacy_id,
            target=metal_type,
            action=LegacyRecordMap.ACTION_CREATED,
        )

        self.stats.created += 1

        self.row_message(
            f'CREATE old metal type {legacy_id}: "{name}"'
        )

    def map_existing_metal_type(
        self,
        *,
        legacy_id,
        metal_type,
        source_name,
    ):
        """
        Map a legacy row to a matching MetalType that already exists.
        """
        self.record_mapping(
            legacy_table="metal_type",
            legacy_id=legacy_id,
            target=metal_type,
            action=LegacyRecordMap.ACTION_UNCHANGED,
        )

        self.stats.unchanged += 1

        self.row_message(
            f'MAP old metal type {legacy_id}: "{source_name}" '
            f'→ existing MetalType #{metal_type.pk} '
            f'"{metal_type.name}"'
        )

    def update_mapped_metal_type(
        self,
        *,
        legacy_id,
        metal_type,
        desired_name,
    ):
        if metal_type.name == desired_name:
            self.stats.unchanged += 1

            self.record_mapping(
                legacy_table="metal_type",
                legacy_id=legacy_id,
                target=metal_type,
                action=LegacyRecordMap.ACTION_UNCHANGED,
            )

            self.row_message(
                f'UNCHANGED old metal type {legacy_id}: '
                f'"{metal_type.name}"'
            )
            return

        conflicting_metal_type = (
            MetalType.objects
            .filter(name__iexact=desired_name)
            .exclude(pk=metal_type.pk)
            .first()
        )

        if conflicting_metal_type is not None:
            self.record_mapping(
                legacy_table="metal_type",
                legacy_id=legacy_id,
                target=conflicting_metal_type,
                action=LegacyRecordMap.ACTION_UPDATED,
            )

            self.stats.updated += 1

            self.row_message(
                f'REMAP old metal type {legacy_id}: '
                f'MetalType #{metal_type.pk} → '
                f'MetalType #{conflicting_metal_type.pk} '
                f'"{conflicting_metal_type.name}"'
            )
            return

        old_name = metal_type.name
        metal_type.name = desired_name
        metal_type.save(update_fields=["name"])

        self.record_mapping(
            legacy_table="metal_type",
            legacy_id=legacy_id,
            target=metal_type,
            action=LegacyRecordMap.ACTION_UPDATED,
        )

        self.stats.updated += 1

        self.row_message(
            f'UPDATE old metal type {legacy_id}: '
            f'"{old_name}" → "{desired_name}"'
        )