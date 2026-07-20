from django.core.management.base import BaseCommand
from django.db import transaction

from culet.models import MovementType


MOVEMENT_TYPES = [
    {
        "name": "Assigned",
        "code": "assigned",
        "job_field": MovementType.JobField.ASSIGNED_TO,
    },
    {
        "name": "Received",
        "code": "received",
        "job_field": MovementType.JobField.HOLDER,
    },
    {
        "name": "Returned",
        "code": "returned",
        "job_field": MovementType.JobField.HOLDER,
    },
    {
        "name": "Returned to Manager",
        "code": "returned-to-manager",
        "job_field": MovementType.JobField.ASSIGNED_TO,
    },
    {
        "name": "Released",
        "code": "released",
        "job_field": MovementType.JobField.HOLDER,
    },
    {
        "name": "Unassigned",
        "code": "unassigned",
        "job_field": MovementType.JobField.ASSIGNED_TO,
    },
    {
        "name": "Shipped — Assignment Cleared",
        "code": "shipped-unassigned",
        "job_field": MovementType.JobField.ASSIGNED_TO,
    },
    {
        "name": "Shipped — Holder Released",
        "code": "shipped-released",
        "job_field": MovementType.JobField.HOLDER,
    },
]


class Command(BaseCommand):
    help = "Create or update the standard Job movement types."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for movement_data in MOVEMENT_TYPES:
            movement_type, created = MovementType.objects.get_or_create(
                code=movement_data["code"],
                defaults={
                    "name": movement_data["name"],
                    "job_field": movement_data["job_field"],
                },
            )

            if created:
                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created movement type: "
                        f"{movement_type.name} "
                        f"({movement_type.code})"
                    )
                )

                continue

            changed_fields = []

            if movement_type.name != movement_data["name"]:
                movement_type.name = movement_data["name"]
                changed_fields.append("name")

            if movement_type.job_field != movement_data["job_field"]:
                movement_type.job_field = movement_data["job_field"]
                changed_fields.append("job_field")

            if changed_fields:
                movement_type.save(
                    update_fields=changed_fields,
                )

                updated_count += 1

                self.stdout.write(
                    self.style.WARNING(
                        f"Updated movement type: "
                        f"{movement_type.name} "
                        f"({movement_type.code})"
                    )
                )
            else:
                unchanged_count += 1

                self.stdout.write(
                    f"Already current: "
                    f"{movement_type.name} "
                    f"({movement_type.code})"
                )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "Movement type seed complete."
            )
        )

        self.stdout.write(
            f"Created:   {created_count}"
        )

        self.stdout.write(
            f"Updated:   {updated_count}"
        )

        self.stdout.write(
            f"Unchanged: {unchanged_count}"
        )