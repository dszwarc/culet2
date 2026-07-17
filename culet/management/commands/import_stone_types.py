from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import LegacyRecordMap, StoneType


STONE_TYPE_SQL = """
    SELECT
        id,
        name,
        abbreviation
    FROM stone_type
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import stone types from the old Culet database, merging duplicate "
        "names while preserving every legacy ID through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(STONE_TYPE_SQL)
        total = len(rows)

        self.stdout.write(
            f"Found {total:,} old stone type rows."
        )

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_stone_type(row)
            except Exception as exc:
                self.record_error(
                    f"Stone type row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Stone types")

    def import_stone_type(self, row):
        legacy_id = row["id"]
        name = clean_text(row["name"])

        if not name:
            reason = "Stone type name was blank."

            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="stone_type",
                legacy_id=legacy_id,
                message=reason,
            )

            self.row_message(
                f"SKIP old stone type {legacy_id}: {reason}"
            )
            return

        max_length = StoneType._meta.get_field("name").max_length

        if len(name) > max_length:
            original_name = name
            name = name[:max_length]

            self.row_message(
                f'TRUNCATE old stone type {legacy_id}: '
                f'"{original_name}" → "{name}"'
            )

        try:
            with transaction.atomic(using="default"):
                mapped_stone_type = self.get_mapped_object(
                    legacy_table="stone_type",
                    legacy_id=legacy_id,
                    model_class=StoneType,
                )

                if mapped_stone_type is not None:
                    self.update_mapped_stone_type(
                        legacy_id=legacy_id,
                        stone_type=mapped_stone_type,
                        desired_name=name,
                    )
                    return

                existing_stone_type = (
                    StoneType.objects
                    .filter(name__iexact=name)
                    .first()
                )

                if existing_stone_type is not None:
                    self.map_duplicate_name(
                        legacy_id=legacy_id,
                        stone_type=existing_stone_type,
                        source_name=name,
                    )
                    return

                self.create_stone_type(
                    legacy_id=legacy_id,
                    name=name,
                )

        except IntegrityError as exc:
            raise ValueError(
                f'Integrity error importing stone type '
                f'{legacy_id}: "{name}"'
            ) from exc

    def create_stone_type(self, *, legacy_id, name):
        stone_type = StoneType.objects.create(
            name=name,
        )

        self.record_mapping(
            legacy_table="stone_type",
            legacy_id=legacy_id,
            target=stone_type,
            action=LegacyRecordMap.ACTION_CREATED,
        )

        self.stats.created += 1

        self.row_message(
            f'CREATE old stone type {legacy_id}: "{name}"'
        )

    def map_duplicate_name(
        self,
        *,
        legacy_id,
        stone_type,
        source_name,
    ):
        """
        Map a duplicate legacy row to an existing StoneType.

        Example:
            old stone_type 3  Aquamarine ┐
                                         ├→ new Aquamarine
            old stone_type 16 Aquamarine ┘
        """
        self.record_mapping(
            legacy_table="stone_type",
            legacy_id=legacy_id,
            target=stone_type,
            action=LegacyRecordMap.ACTION_UNCHANGED,
        )

        self.stats.unchanged += 1

        self.row_message(
            f'MAP old stone type {legacy_id}: "{source_name}" '
            f'→ existing StoneType #{stone_type.pk} "{stone_type.name}"'
        )

    def update_mapped_stone_type(
        self,
        *,
        legacy_id,
        stone_type,
        desired_name,
    ):
        if stone_type.name == desired_name:
            self.stats.unchanged += 1

            self.record_mapping(
                legacy_table="stone_type",
                legacy_id=legacy_id,
                target=stone_type,
                action=LegacyRecordMap.ACTION_UNCHANGED,
            )

            self.row_message(
                f'UNCHANGED old stone type {legacy_id}: '
                f'"{stone_type.name}"'
            )
            return

        conflicting_stone_type = (
            StoneType.objects
            .filter(name__iexact=desired_name)
            .exclude(pk=stone_type.pk)
            .first()
        )

        if conflicting_stone_type is not None:
            # The old row now has a name already represented by another
            # StoneType. Remap this legacy ID to the canonical object.
            self.record_mapping(
                legacy_table="stone_type",
                legacy_id=legacy_id,
                target=conflicting_stone_type,
                action=LegacyRecordMap.ACTION_UPDATED,
            )

            self.stats.updated += 1

            self.row_message(
                f'REMAP old stone type {legacy_id}: '
                f'StoneType #{stone_type.pk} → '
                f'StoneType #{conflicting_stone_type.pk} '
                f'"{conflicting_stone_type.name}"'
            )
            return

        old_name = stone_type.name
        stone_type.name = desired_name
        stone_type.save(update_fields=["name"])

        self.record_mapping(
            legacy_table="stone_type",
            legacy_id=legacy_id,
            target=stone_type,
            action=LegacyRecordMap.ACTION_UPDATED,
        )

        self.stats.updated += 1

        self.row_message(
            f'UPDATE old stone type {legacy_id}: '
            f'"{old_name}" → "{desired_name}"'
        )