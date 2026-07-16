from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import LegacyRecordMap, Vendor


VENDOR_SQL = """
    SELECT
        id,
        name,
        date_added,
        date_modified
    FROM vendor
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = "Import vendors from the restored old Culet MySQL database."

    def run_import(self, *args, **options):
        rows = fetch_old_rows(VENDOR_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} old vendor rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_vendor(row)
            except Exception as exc:
                self.record_error(
                    f"Vendor row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Vendors")

    def import_vendor(self, row):
        old_id = row["id"]
        name = clean_text(row["name"])

        if not name:
            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="vendor",
                legacy_id=old_id,
                message="Vendor name was blank.",
            )

            self.row_message(
                f"SKIP old vendor {old_id}: vendor name is blank."
            )
            return

        name_max = Vendor._meta.get_field("name").max_length

        if len(name) > name_max:
            original_name = name
            name = name[:name_max]

            self.row_message(
                f"TRUNCATE old vendor {old_id}: "
                f"{original_name!r} -> {name!r}"
            )

        desired_values = {
            "address": "",
            "email": "",
            "phone": "",
            "number": old_id,
        }

        try:
            with transaction.atomic(using="default"):
                vendor = self.get_mapped_object(
                    legacy_table="vendor",
                    legacy_id=old_id,
                    model_class=Vendor,
                )

                if vendor is None:
                    vendor = Vendor.objects.filter(name=name).first()

                if vendor is None:
                    vendor = Vendor.objects.create(
                        name=name,
                        **desired_values,
                    )

                    self.stats.created += 1
                    action = LegacyRecordMap.ACTION_CREATED

                    self.row_message(
                        f"CREATE old vendor {old_id}: {vendor.name}"
                    )

                else:
                    changed_fields = []

                    if vendor.name != name:
                        vendor.name = name
                        changed_fields.append("name")

                    for field_name, desired_value in desired_values.items():
                        if getattr(vendor, field_name) != desired_value:
                            setattr(vendor, field_name, desired_value)
                            changed_fields.append(field_name)

                    if changed_fields:
                        vendor.save(update_fields=changed_fields)
                        self.stats.updated += 1
                        action = LegacyRecordMap.ACTION_UPDATED

                        self.row_message(
                            f"UPDATE old vendor {old_id}: "
                            f"{vendor.name} "
                            f"({', '.join(changed_fields)})"
                        )
                    else:
                        self.stats.unchanged += 1
                        action = LegacyRecordMap.ACTION_UNCHANGED

                        self.row_message(
                            f"UNCHANGED old vendor {old_id}: {vendor.name}"
                        )

                self.record_mapping(
                    legacy_table="vendor",
                    legacy_id=old_id,
                    target=vendor,
                    action=action,
                )

        except IntegrityError as exc:
            raise ValueError(
                f"Integrity error for old vendor {old_id} ({name!r})"
            ) from exc