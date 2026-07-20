from django.core.management.base import BaseCommand
from django.db import transaction

from culet.models import JobStatus


JOB_STATUS_DEFINITIONS = [
    {
        "name": "Waiting on Metal",
        "sort_order": 0,
        "active": True,
    },
    {
        "name": "Waiting on Stones",
        "sort_order": 0,
        "active": True,
    },
    {
        "name": "Active",
        "sort_order": 0,
        "active": True,
    },
    {
        "name": "Imported",
        "sort_order": 10,
        "active": True,
    },
    {
        "name": "Shipped",
        "sort_order": 100,
        "active": True,
    },
]


class Command(BaseCommand):
    help = "Create or update the standard Culet job statuses."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for definition in JOB_STATUS_DEFINITIONS:
            name = definition["name"]
            desired_sort_order = definition["sort_order"]
            desired_active = definition["active"]

            status = JobStatus.objects.filter(
                name__iexact=name
            ).first()

            if status is None:
                status = JobStatus.objects.create(
                    name=name,
                    sort_order=desired_sort_order,
                    active=desired_active,
                )
                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created job status: {status.name}"
                    )
                )
                continue

            changed_fields = []

            if status.name != name:
                status.name = name
                changed_fields.append("name")

            if status.sort_order != desired_sort_order:
                status.sort_order = desired_sort_order
                changed_fields.append("sort_order")

            if status.active != desired_active:
                status.active = desired_active
                changed_fields.append("active")

            if changed_fields:
                status.save(update_fields=changed_fields)
                updated_count += 1

                self.stdout.write(
                    self.style.WARNING(
                        f"Updated job status: {status.name} "
                        f"({', '.join(changed_fields)})"
                    )
                )
            else:
                unchanged_count += 1
                self.stdout.write(
                    f"Unchanged job status: {status.name}"
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("Job status seed summary")
        )
        self.stdout.write(f"Created:   {created_count}")
        self.stdout.write(f"Updated:   {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}")