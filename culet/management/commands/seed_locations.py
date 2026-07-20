from django.core.management.base import BaseCommand
from django.db import transaction

from culet.models import Location


LOCATION_DEFINITIONS = [
    {"name": "Office", "active": True},
    {"name": "Jewelry", "active": True},
    {"name": "Jewelry 37", "active": True},
    {"name": "Setting", "active": True},
    {"name": "Polishing", "active": True},
    {"name": "Laser", "active": True},
    {"name": "Quality Control", "active": True},
    {"name": "Production Management", "active": True},
    {"name": "Homework", "active": True},
    {"name": "Piecework", "active": True},
    {"name": "Safe 1", "active": True},
    {"name": "Safe 2", "active": True},
    {"name": "Safe 3", "active": True},
    {"name": "Unknown", "active": True},
]


class Command(BaseCommand):
    help = "Create or update the standard Culet locations."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for definition in LOCATION_DEFINITIONS:
            name = definition["name"]
            desired_active = definition["active"]

            location = Location.objects.filter(
                name__iexact=name
            ).first()

            if location is None:
                Location.objects.create(
                    name=name,
                    active=desired_active,
                )
                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created location: {name}"
                    )
                )
                continue

            changed_fields = []

            if location.name != name:
                location.name = name
                changed_fields.append("name")

            if location.active != desired_active:
                location.active = desired_active
                changed_fields.append("active")

            if changed_fields:
                location.save(update_fields=changed_fields)
                updated_count += 1

                self.stdout.write(
                    self.style.WARNING(
                        f"Updated location: {location.name} "
                        f"({', '.join(changed_fields)})"
                    )
                )
            else:
                unchanged_count += 1
                self.stdout.write(
                    f"Unchanged location: {location.name}"
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("Location seed summary")
        )
        self.stdout.write(f"Created:   {created_count}")
        self.stdout.write(f"Updated:   {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}")