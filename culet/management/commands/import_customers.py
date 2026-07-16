from django.db import IntegrityError, transaction
from culet.models import Customer, LegacyRecordMap
from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_email, clean_phone, clean_text
from culet.models import Customer


CUSTOMER_SQL = """
    SELECT
        id,
        name,
        email,
        phone,
        date_added
    FROM customer
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = "Import customers from the restored old Culet MySQL database."

    def run_import(self, *args, **options):
        rows = fetch_old_rows(CUSTOMER_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} old customer rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_customer(row)
            except Exception as exc:
                self.record_error(
                    f"Customer row {row.get('id')} could not be imported",
                    exc,
                )

            self.print_progress(index, total, "Customers")

    def import_customer(self, row):
        old_id = row["id"]
        name = clean_text(row["name"])
        email = clean_email(row["email"])
        phone = clean_phone(row["phone"])

        if not name:
            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="customer",
                legacy_id=old_id,
                message="Customer name was blank.",
            )

            self.row_message(
                f"SKIP old customer {old_id}: customer name is blank."
            )
            return

        name_max = Customer._meta.get_field("name").max_length
        email_max = Customer._meta.get_field("email").max_length
        phone_max = Customer._meta.get_field("phone").max_length

        if len(name) > name_max:
            original_name = name
            name = name[:name_max]

            self.row_message(
                f"TRUNCATE old customer {old_id}: "
                f"{original_name!r} -> {name!r}"
            )

        if len(email) > email_max:
            self.stats.skipped += 1

            self.record_skipped(
                legacy_table="customer",
                legacy_id=old_id,
                message=(
                    f"Email exceeded the current maximum length "
                    f"of {email_max}."
                ),
            )

            self.row_message(
                f"SKIP old customer {old_id}: email is too long."
            )
            return

        if len(phone) > phone_max:
            self.row_message(
                f"TRUNCATE old customer {old_id}: "
                f"phone {phone!r} exceeds {phone_max} characters."
            )
            phone = phone[:phone_max]

        desired_values = {
            "address": "",
            "email": email,
            "phone": phone,
            "number": old_id,
        }

        try:
            with transaction.atomic(using="default"):
                # First try the deterministic old-ID mapping.
                customer = self.get_mapped_object(
                    legacy_table="customer",
                    legacy_id=old_id,
                    model_class=Customer,
                )

                # For the first import, there will not be a mapping yet.
                # Fall back to the model's unique natural key.
                if customer is None:
                    customer = Customer.objects.filter(name=name).first()

                if customer is None:
                    customer = Customer.objects.create(
                        name=name,
                        **desired_values,
                    )

                    self.stats.created += 1
                    action = LegacyRecordMap.ACTION_CREATED

                    self.row_message(
                        f"CREATE old customer {old_id}: {customer.name}"
                    )

                else:
                    changed_fields = []

                    if customer.name != name:
                        customer.name = name
                        changed_fields.append("name")

                    for field_name, desired_value in desired_values.items():
                        if getattr(customer, field_name) != desired_value:
                            setattr(customer, field_name, desired_value)
                            changed_fields.append(field_name)

                    if changed_fields:
                        customer.save(update_fields=changed_fields)
                        self.stats.updated += 1
                        action = LegacyRecordMap.ACTION_UPDATED

                        self.row_message(
                            f"UPDATE old customer {old_id}: "
                            f"{customer.name} "
                            f"({', '.join(changed_fields)})"
                        )
                    else:
                        self.stats.unchanged += 1
                        action = LegacyRecordMap.ACTION_UNCHANGED

                        self.row_message(
                            f"UNCHANGED old customer {old_id}: "
                            f"{customer.name}"
                        )

                self.record_mapping(
                    legacy_table="customer",
                    legacy_id=old_id,
                    target=customer,
                    action=action,
                )

        except IntegrityError as exc:
            raise ValueError(
                f"Integrity error for old customer {old_id} ({name!r})"
            ) from exc