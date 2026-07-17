from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import LegacyRecordMap, StoneShape


STONE_SHAPE_SQL = """
    SELECT
        id,
        name,
        abbreviation
    FROM stone_shape
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import stone shapes from the old Culet database while preserving "
        "legacy IDs through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(STONE_SHAPE_SQL)
        total = len(rows)

        self.stdout.write(
            f"Found {total:,} old stone shape rows."
        )

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_stone_shape(row)
            except Exception as exc:
                self.record_error(
                    f"Stone shape row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Stone shapes")

    def import_stone_shape(self, row):
        legacy_id = row["id"]
        name = clean_text(row["name"])

        if not name:
            reason = "Stone shape name was blank."

            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="stone_shape",
                legacy_id=legacy_id,
                message=reason,
            )

            self.row_message(
                f"SKIP old stone shape {legacy_id}: {reason}"
            )
            return

        max_length = StoneShape._meta.get_field("name").max_length

        if len(name) > max_length:
            original_name = name
            name = name[:max_length]

            self.row_message(
                f'TRUNCATE old stone shape {legacy_id}: '
                f'"{original_name}" → "{name}"'
            )

        try:
            with transaction.atomic(using="default"):
                mapped_shape = self.get_mapped_object(
                    legacy_table="stone_shape",
                    legacy_id=legacy_id,
                    model_class=StoneShape,
                )

                if mapped_shape is not None:
                    self.update_mapped_shape(
                        legacy_id=legacy_id,
                        stone_shape=mapped_shape,
                        desired_name=name,
                    )
                    return

                existing_shape = (
                    StoneShape.objects
                    .filter(name__iexact=name)
                    .first()
                )

                if existing_shape is not None:
                    self.map_existing_shape(
                        legacy_id=legacy_id,
                        stone_shape=existing_shape,
                        source_name=name,
                    )
                    return

                self.create_stone_shape(
                    legacy_id=legacy_id,
                    name=name,
                )

        except IntegrityError as exc:
            raise ValueError(
                f'Integrity error importing stone shape '
                f'{legacy_id}: "{name}"'
            ) from exc

    def create_stone_shape(self, *, legacy_id, name):
        stone_shape = StoneShape.objects.create(
            name=name,
        )

        self.record_mapping(
            legacy_table="stone_shape",
            legacy_id=legacy_id,
            target=stone_shape,
            action=LegacyRecordMap.ACTION_CREATED,
        )

        self.stats.created += 1

        self.row_message(
            f'CREATE old stone shape {legacy_id}: "{name}"'
        )

    def map_existing_shape(
        self,
        *,
        legacy_id,
        stone_shape,
        source_name,
    ):
        """
        Map a legacy row to a matching StoneShape that already exists.
        """
        self.record_mapping(
            legacy_table="stone_shape",
            legacy_id=legacy_id,
            target=stone_shape,
            action=LegacyRecordMap.ACTION_UNCHANGED,
        )

        self.stats.unchanged += 1

        self.row_message(
            f'MAP old stone shape {legacy_id}: "{source_name}" '
            f'→ existing StoneShape #{stone_shape.pk} '
            f'"{stone_shape.name}"'
        )

    def update_mapped_shape(
        self,
        *,
        legacy_id,
        stone_shape,
        desired_name,
    ):
        if stone_shape.name == desired_name:
            self.stats.unchanged += 1

            self.record_mapping(
                legacy_table="stone_shape",
                legacy_id=legacy_id,
                target=stone_shape,
                action=LegacyRecordMap.ACTION_UNCHANGED,
            )

            self.row_message(
                f'UNCHANGED old stone shape {legacy_id}: '
                f'"{stone_shape.name}"'
            )
            return

        conflicting_shape = (
            StoneShape.objects
            .filter(name__iexact=desired_name)
            .exclude(pk=stone_shape.pk)
            .first()
        )

        if conflicting_shape is not None:
            self.record_mapping(
                legacy_table="stone_shape",
                legacy_id=legacy_id,
                target=conflicting_shape,
                action=LegacyRecordMap.ACTION_UPDATED,
            )

            self.stats.updated += 1

            self.row_message(
                f'REMAP old stone shape {legacy_id}: '
                f'StoneShape #{stone_shape.pk} → '
                f'StoneShape #{conflicting_shape.pk} '
                f'"{conflicting_shape.name}"'
            )
            return

        old_name = stone_shape.name
        stone_shape.name = desired_name
        stone_shape.save(update_fields=["name"])

        self.record_mapping(
            legacy_table="stone_shape",
            legacy_id=legacy_id,
            target=stone_shape,
            action=LegacyRecordMap.ACTION_UPDATED,
        )

        self.stats.updated += 1

        self.row_message(
            f'UPDATE old stone shape {legacy_id}: '
            f'"{old_name}" → "{desired_name}"'
        )