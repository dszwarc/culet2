from django.core.management.base import BaseCommand
from django.db import transaction

from culet.models import FailureType


FAILURE_TYPE_DEFINITIONS = [
    {"name": "Assembly Issues", "active": True},
    {"name": "Chipped Stones", "active": True},
    {"name": "Cracks", "active": True},
    {"name": "Crooked Stones", "active": True},
    {"name": "Design Incorrect", "active": True},
    {"name": "Excess Metal", "active": True},
    {"name": "Functionality Issue", "active": True},
    {"name": "Insufficient Prong Contact", "active": True},
    {"name": "Loose Stones", "active": True},
    {"name": "Over-polished", "active": True},
    {"name": "Over-worked Stamp", "active": True},
    {"name": "Plating Issues", "active": True},
    {"name": "Porosity", "active": True},
    {"name": "Re-Cast (Internal Repair)", "active": True},
    {"name": "Scratches", "active": True},
    {"name": "Tool Marks", "active": True},
    {"name": "Undefined prongs/beads unfinished", "active": True},
    {"name": "Under-polished", "active": True},
    {"name": "Uneven prongs", "active": True},
    {"name": "Wrong / Bad Stamp", "active": True},
]


class Command(BaseCommand):
    help = "Create or update the standard Culet failure types."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for definition in FAILURE_TYPE_DEFINITIONS:
            name = definition["name"]
            desired_active = definition["active"]

            failure_type = FailureType.objects.filter(
                name__iexact=name
            ).first()

            if failure_type is None:
                failure_type = FailureType.objects.create(
                    name=name,
                    active=desired_active,
                )
                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created failure type: {failure_type.name}"
                    )
                )
                continue

            changed_fields = []

            if failure_type.name != name:
                failure_type.name = name
                changed_fields.append("name")

            if failure_type.active != desired_active:
                failure_type.active = desired_active
                changed_fields.append("active")

            if changed_fields:
                failure_type.save(update_fields=changed_fields)
                updated_count += 1

                self.stdout.write(
                    self.style.WARNING(
                        f"Updated failure type: {failure_type.name} "
                        f"({', '.join(changed_fields)})"
                    )
                )
            else:
                unchanged_count += 1
                self.stdout.write(
                    f"Unchanged failure type: {failure_type.name}"
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("Failure type seed summary")
        )
        self.stdout.write(f"Created:   {created_count}")
        self.stdout.write(f"Updated:   {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}")