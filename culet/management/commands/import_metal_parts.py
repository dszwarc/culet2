from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import Customer, LegacyRecordMap, MetalPart


METAL_PART_SQL = """
    SELECT
        id,
        part_number,
        customer_id,
        mold_no,
        mold_location,
        caster,
        is_deleted
    FROM part_number
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import metal parts from the old Culet part_number table while "
        "preserving legacy IDs through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(METAL_PART_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} old metal part rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_metal_part(row)
            except Exception as exc:
                self.record_error(
                    f"Metal part row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Metal parts")

    def import_metal_part(self, row):
        legacy_id = row["id"]
        sku = clean_text(row["part_number"])
        is_deleted = clean_text(row["is_deleted"]).upper()

        if is_deleted == "Y":
            self.skip_row(legacy_id, "Legacy metal part was marked deleted.")
            return

        if not sku:
            self.skip_row(legacy_id, "Metal part SKU was blank.")
            return

        sku_max = MetalPart._meta.get_field("sku").max_length
        if len(sku) > sku_max:
            self.skip_row(
                legacy_id,
                f"Metal part SKU exceeded the {sku_max}-character limit.",
            )
            return

        description = self.build_description(row)
        customer = self.resolve_customer(row.get("customer_id"))

        desired_values = {
            "description": description or None,
            "customer": customer,
        }

        try:
            with transaction.atomic(using="default"):
                metal_part = self.get_mapped_object(
                    legacy_table="part_number",
                    legacy_id=legacy_id,
                    model_class=MetalPart,
                )

                if metal_part is None:
                    metal_part = (
                        MetalPart.objects
                        .filter(sku__iexact=sku)
                        .first()
                    )

                if metal_part is None:
                    metal_part = MetalPart.objects.create(
                        sku=sku,
                        **desired_values,
                    )
                    self.stats.created += 1
                    action = LegacyRecordMap.ACTION_CREATED
                    self.row_message(
                        f'CREATE old metal part {legacy_id}: "{sku}"'
                    )
                else:
                    changed_fields = []

                    if metal_part.sku != sku:
                        conflict = (
                            MetalPart.objects
                            .filter(sku__iexact=sku)
                            .exclude(pk=metal_part.pk)
                            .first()
                        )

                        if conflict is not None:
                            metal_part = conflict
                        else:
                            metal_part.sku = sku
                            changed_fields.append("sku")

                    for field_name, desired_value in desired_values.items():
                        if getattr(metal_part, field_name) != desired_value:
                            setattr(metal_part, field_name, desired_value)
                            changed_fields.append(field_name)

                    if changed_fields:
                        metal_part.save(update_fields=changed_fields)
                        self.stats.updated += 1
                        action = LegacyRecordMap.ACTION_UPDATED
                        self.row_message(
                            f'UPDATE old metal part {legacy_id}: "{sku}" '
                            f'({", ".join(changed_fields)})'
                        )
                    else:
                        self.stats.unchanged += 1
                        action = LegacyRecordMap.ACTION_UNCHANGED
                        self.row_message(
                            f'UNCHANGED old metal part {legacy_id}: "{sku}"'
                        )

                self.record_mapping(
                    legacy_table="part_number",
                    legacy_id=legacy_id,
                    target=metal_part,
                    action=action,
                )

        except IntegrityError as exc:
            raise ValueError(
                f'Integrity error importing metal part {legacy_id}: "{sku}"'
            ) from exc

    def resolve_customer(self, legacy_customer_id):
        if not legacy_customer_id:
            return None

        customer = self.get_mapped_object(
            legacy_table="customer",
            legacy_id=legacy_customer_id,
            model_class=Customer,
        )

        if customer is None:
            raise ValueError(
                f"No imported Customer mapping exists for legacy "
                f"customer ID {legacy_customer_id}."
            )

        return customer

    def build_description(self, row):
        parts = []

        mold_no = clean_text(row.get("mold_no"))
        mold_location = clean_text(row.get("mold_location"))
        caster = clean_text(row.get("caster"))

        if mold_no:
            parts.append(f"Mold: {mold_no}")
        if mold_location:
            parts.append(f"Location: {mold_location}")
        if caster:
            parts.append(f"Caster: {caster}")

        description = " | ".join(parts)
        max_length = MetalPart._meta.get_field("description").max_length

        if max_length and len(description) > max_length:
            description = description[:max_length]

        return description

    def skip_row(self, legacy_id, reason):
        self.stats.skipped += 1

        self.record_skipped(
            legacy_table="part_number",
            legacy_id=legacy_id,
            message=reason,
        )

        self.row_message(
            f"SKIP old metal part {legacy_id}: {reason}"
        )