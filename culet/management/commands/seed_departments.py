from django.core.management.base import BaseCommand
from django.db import transaction

from culet.models import Department


DEPARTMENT_DEFINITIONS = [
    {
        "name": "Polishing",
        "active": True,
    },
    {
        "name": "Setting",
        "active": True,
    },
    {
        "name": "Quality Control",
        "active": True,
    },
    {
        "name": "Office",
        "active": True,
    },
    {
        "name": "Laser",
        "active": True,
    },
    {
        "name": "Production Management",
        "active": True,
    },
    {
        "name": "Jewelry",
        "active": True,
    },
    {
        "name": "Jewelry 37",
        "active": True,
    },
    {
        "name": "Homework",
        "active": True,
    },
]


class Command(BaseCommand):
    help = "Create or update the standard Culet departments."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for definition in DEPARTMENT_DEFINITIONS:
            name = definition["name"]
            desired_active = definition["active"]

            department = Department.objects.filter(
                name__iexact=name
            ).first()

            if department is None:
                department = Department.objects.create(
                    name=name,
                    active=desired_active,
                )
                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created department: {department.name}"
                    )
                )
                continue

            changed_fields = []

            if department.name != name:
                department.name = name
                changed_fields.append("name")

            if department.active != desired_active:
                department.active = desired_active
                changed_fields.append("active")

            if changed_fields:
                department.save(update_fields=changed_fields)
                updated_count += 1

                self.stdout.write(
                    self.style.WARNING(
                        f"Updated department: {department.name} "
                        f"({', '.join(changed_fields)})"
                    )
                )
            else:
                unchanged_count += 1
                self.stdout.write(
                    f"Unchanged department: {department.name}"
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("Department seed summary")
        )
        self.stdout.write(f"Created:   {created_count}")
        self.stdout.write(f"Updated:   {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}")