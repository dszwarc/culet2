from django.core.management.base import BaseCommand

from culet.models import Step


class Command(BaseCommand):
    help = "Seeds the default production steps."

    STEPS = [
        {
            "name": "After Cleaning",
            "code": "afterclean",
            "sort_order": 0,
            "active": True,
        },
        {
            "name": "After Pre-polish",
            "code": "afterprepolish",
            "sort_order": 1,
            "active": True,
        },
        {
            "name": "After Assembly",
            "code": "afterassm",
            "sort_order": 2,
            "active": True,
        },
        {
            "name": "Before Set",
            "code": "beforeset",
            "sort_order": 3,
            "active": True,
        },
        {
            "name": "After Set",
            "code": "afterset",
            "sort_order": 4,
            "active": True,
        },
        {
            "name": "Final Weight",
            "code": "final",
            "sort_order": 5,
            "active": True,
        },
    ]

    def handle(self, *args, **options):
        created = 0
        updated = 0

        for step_data in self.STEPS:
            step, was_created = Step.objects.update_or_create(
                code=step_data["code"],
                defaults={
                    "name": step_data["name"],
                    "sort_order": step_data["sort_order"],
                    "active": step_data["active"],
                },
            )

            if was_created:
                created += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Created: {step.name}")
                )
            else:
                updated += 1
                self.stdout.write(
                    self.style.WARNING(f"Updated: {step.name}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nFinished: {created} created, {updated} updated."
            )
        )