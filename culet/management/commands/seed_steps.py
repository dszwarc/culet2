from django.core.management.base import BaseCommand

from culet.models import Step


class Command(BaseCommand):
    help = "Seeds the default production steps."

    STEPS = [
        {
            "name": "After Cleaning",
            "code": "afterclean",
            "order": 0,
            "active": True,
        },
        {
            "name": "After Pre-polish",
            "code": "afterprepolish",
            "order": 1,
            "active": True,
        },
        {
            "name": "After Assembly",
            "code": "afterassm",
            "order": 2,
            "active": True,
        },
        {
            "name": "Before Set",
            "code": "beforeset",
            "order": 3,
            "active": True,
        },
        {
            "name": "After Set",
            "code": "afterset",
            "order": 4,
            "active": True,
        },
        {
            "name": "Final Weight",
            "code": "final",
            "order": 5,
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
                    "order": step_data["order"],
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