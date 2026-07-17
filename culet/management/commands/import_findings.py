from decimal import Decimal

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import FindingStock, FindingType, LegacyRecordMap


FINDING_SQL = """
    SELECT
        id,
        name
    FROM finding
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import the legacy finding catalog as FindingType and FindingStock "
        "records while preserving both mappings through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(FINDING_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} old finding rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_finding(row)
            except Exception as exc:
                self.record_error(
                    f"Finding row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Findings")

    def import_finding(self, row):
        legacy_id = row["id"]
        name = clean_text(row["name"])

        if not name:
            reason = "Finding name was blank."
            self.stats.skipped += 1
            self.record_skipped(
                legacy_table="finding",
                legacy_id=legacy_id,
                message=reason,
            )
            self.row_message(f"SKIP old finding {legacy_id}: {reason}")
            return

        name = self.truncate_name(name, legacy_id)

        try:
            with transaction.atomic(using="default"):
                finding_type, type_action = self.sync_finding_type(
                    legacy_id=legacy_id,
                    name=name,
                )
                finding_stock, stock_action = self.sync_finding_stock(
                    legacy_id=legacy_id,
                    name=name,
                    finding_type=finding_type,
                )

                actions = {type_action, stock_action}
                if LegacyRecordMap.ACTION_CREATED in actions:
                    self.stats.created += 1
                    result = "CREATE"
                elif LegacyRecordMap.ACTION_UPDATED in actions:
                    self.stats.updated += 1
                    result = "UPDATE"
                else:
                    self.stats.unchanged += 1
                    result = "UNCHANGED"

                self.row_message(
                    f'{result} old finding {legacy_id}: "{name}" '
                    f'→ FindingType #{finding_type.pk}, '
                    f'FindingStock #{finding_stock.pk}'
                )

        except IntegrityError as exc:
            raise ValueError(
                f'Integrity error importing finding {legacy_id}: "{name}"'
            ) from exc

    def truncate_name(self, name, legacy_id):
        type_max = FindingType._meta.get_field("name").max_length
        stock_max = FindingStock._meta.get_field("name").max_length
        max_length = min(type_max, stock_max)

        if len(name) <= max_length:
            return name

        shortened = name[:max_length]
        self.row_message(
            f'TRUNCATE old finding {legacy_id}: "{name}" → "{shortened}"'
        )
        return shortened

    def sync_finding_type(self, *, legacy_id, name):
        finding_type = self.get_mapped_object(
            legacy_table="finding",
            legacy_id=legacy_id,
            model_class=FindingType,
        )

        if finding_type is None:
            finding_type = FindingType.objects.filter(name__iexact=name).first()

            if finding_type is None:
                finding_type = FindingType.objects.create(
                    name=name,
                    unit="pcs",
                )
                action = LegacyRecordMap.ACTION_CREATED
            else:
                action = self.update_finding_type(finding_type, name)
        else:
            action = self.update_finding_type(finding_type, name)

        self.record_mapping(
            legacy_table="finding",
            legacy_id=legacy_id,
            target=finding_type,
            action=action,
            message="Legacy finding mapped to FindingType.",
        )
        return finding_type, action

    def update_finding_type(self, finding_type, desired_name):
        changed_fields = []

        if finding_type.name != desired_name:
            conflict = (
                FindingType.objects
                .filter(name__iexact=desired_name)
                .exclude(pk=finding_type.pk)
                .first()
            )
            if conflict is not None:
                return LegacyRecordMap.ACTION_UNCHANGED

            finding_type.name = desired_name
            changed_fields.append("name")

        if finding_type.unit != "pcs":
            finding_type.unit = "pcs"
            changed_fields.append("unit")

        if changed_fields:
            finding_type.save(update_fields=changed_fields)
            return LegacyRecordMap.ACTION_UPDATED

        return LegacyRecordMap.ACTION_UNCHANGED

    def sync_finding_stock(self, *, legacy_id, name, finding_type):
        finding_stock = self.get_mapped_object(
            legacy_table="finding",
            legacy_id=legacy_id,
            model_class=FindingStock,
        )

        if finding_stock is None:
            finding_stock = FindingStock.objects.filter(name__iexact=name).first()

            if finding_stock is None:
                finding_stock = FindingStock.objects.create(
                    finding_type=finding_type,
                    name=name,
                    sku="",
                    metal_type=None,
                    qty_on_hand=Decimal("0"),
                    active=True,
                )
                action = LegacyRecordMap.ACTION_CREATED
            else:
                action = self.update_finding_stock(
                    finding_stock,
                    finding_type=finding_type,
                    desired_name=name,
                )
        else:
            action = self.update_finding_stock(
                finding_stock,
                finding_type=finding_type,
                desired_name=name,
            )

        self.record_mapping(
            legacy_table="finding",
            legacy_id=legacy_id,
            target=finding_stock,
            action=action,
            message="Legacy finding mapped to FindingStock.",
        )
        return finding_stock, action

    def update_finding_stock(
        self,
        finding_stock,
        *,
        finding_type,
        desired_name,
    ):
        changed_fields = []

        if finding_stock.name != desired_name:
            conflict = (
                FindingStock.objects
                .filter(name__iexact=desired_name)
                .exclude(pk=finding_stock.pk)
                .first()
            )
            if conflict is not None:
                return LegacyRecordMap.ACTION_UNCHANGED

            finding_stock.name = desired_name
            changed_fields.append("name")

        if finding_stock.finding_type_id != finding_type.pk:
            finding_stock.finding_type = finding_type
            changed_fields.append("finding_type")

        if finding_stock.sku:
            finding_stock.sku = ""
            changed_fields.append("sku")

        if finding_stock.metal_type_id is not None:
            finding_stock.metal_type = None
            changed_fields.append("metal_type")

        if finding_stock.qty_on_hand != Decimal("0"):
            finding_stock.qty_on_hand = Decimal("0")
            changed_fields.append("qty_on_hand")

        if not finding_stock.active:
            finding_stock.active = True
            changed_fields.append("active")

        if changed_fields:
            finding_stock.save(update_fields=changed_fields)
            return LegacyRecordMap.ACTION_UPDATED

        return LegacyRecordMap.ACTION_UNCHANGED