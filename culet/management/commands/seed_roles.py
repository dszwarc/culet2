from django.core.management.base import BaseCommand
from django.db import transaction

from culet.models import Role


ROLE_DEFINITIONS = [
    {
        "name": "Hourly",
        "level": 0,
        "requires_clock_in": True,
        "can_start_activities": True,
        "can_receive_all_jobs": False,
        "active": True,
    },
    {
        "name": "Department Head",
        "level": 10,
        "requires_clock_in": True,
        "can_start_activities": True,
        "can_receive_all_jobs": False,
        "active": True,
    },
    {
        "name": "Manager",
        "level": 30,
        "requires_clock_in": False,
        "can_start_activities": True,
        "can_receive_all_jobs": True,
        "active": True,
    },
    {
        "name": "Super",
        "level": 50,
        "requires_clock_in": False,
        "can_start_activities": True,
        "can_receive_all_jobs": True,
        "active": True,
    },
]


class Command(BaseCommand):
    help = "Create or update the standard Culet roles."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for definition in ROLE_DEFINITIONS:
            name = definition["name"]
            defaults = {
                key: value
                for key, value in definition.items()
                if key != "name"
            }

            role = Role.objects.filter(name=name).first()

            if role is None:
                role = Role.objects.create(
                    name=name,
                    **defaults,
                )
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created role: {role.name} (level {role.level})"
                    )
                )
                continue

            changed_fields = []

            for field_name, desired_value in defaults.items():
                if getattr(role, field_name) != desired_value:
                    setattr(role, field_name, desired_value)
                    changed_fields.append(field_name)

            if changed_fields:
                role.save(update_fields=changed_fields)
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Updated role: {role.name} "
                        f"({', '.join(changed_fields)})"
                    )
                )
            else:
                unchanged_count += 1
                self.stdout.write(
                    f"Unchanged role: {role.name}"
                )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Role seed summary"))
        self.stdout.write(f"Created:   {created_count}")
        self.stdout.write(f"Updated:   {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}")