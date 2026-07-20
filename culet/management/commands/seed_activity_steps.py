from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from culet.models import ActivityStep, Department


ACTIVITY_STEP_DEFINITIONS = [
    {
        "name": "Adding Findings",
        "code": "addfind",
        "departments": ["Jewelry", "Jewelry 37"],
    },
    {
        "name": "After Setter",
        "code": "afterset",
        "departments": ["Jewelry", "Jewelry 37"],
    },
    {
        "name": "Assembly",
        "code": "assm",
        "departments": ["Jewelry", "Jewelry 37"],
    },
    {
        "name": "Cleaning",
        "code": "clean",
        "departments": ["Jewelry", "Jewelry 37"],
    },
    {
        "name": "Final Polish",
        "code": "finalpol",
        "departments": ["Polishing"],
    },
    {
        "name": "Inspection",
        "code": "qc",
        "departments": ["Quality Control"],
    },
    {
        "name": "Polish before stamp",
        "code": "polstamp",
        "departments": ["Polishing"],
    },
    {
        "name": "Pre-polish",
        "code": "prepol",
        "departments": ["Polishing"],
    },
    {
        "name": "Pre-polish for set",
        "code": "prepolset",
        "departments": ["Polishing"],
    },
    {
        "name": "Repair",
        "code": "repair",
        "departments": ["Jewelry", "Jewelry 37", "Polishing"],
    },
    {
        "name": "Set center(s)",
        "code": "setcenter",
        "departments": ["Setting"],
    },
    {
        "name": "Set melee",
        "code": "setmel",
        "departments": ["Setting"],
    },
]


class Command(BaseCommand):
    help = "Create or update the standard Culet activity steps."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        required_department_names = sorted(
            {
                department_name
                for definition in ACTIVITY_STEP_DEFINITIONS
                for department_name in definition["departments"]
            }
        )

        departments_by_name = {
            department.name: department
            for department in Department.objects.filter(
                name__in=required_department_names
            )
        }

        missing_departments = [
            name
            for name in required_department_names
            if name not in departments_by_name
        ]

        if missing_departments:
            raise CommandError(
                "Missing required departments: "
                + ", ".join(missing_departments)
                + ". Run seed_departments first."
            )

        for definition in ACTIVITY_STEP_DEFINITIONS:
            name = definition["name"]
            desired_code = definition["code"]
            desired_department_names = definition["departments"]

            step = ActivityStep.objects.filter(name__iexact=name).first()

            if step is None:
                step = ActivityStep.objects.create(
                    name=name,
                    code=desired_code,
                )
                created = True
                changed_fields = []
            else:
                created = False
                changed_fields = []

                if step.name != name:
                    step.name = name
                    changed_fields.append("name")

                if step.code != desired_code:
                    step.code = desired_code
                    changed_fields.append("code")

                if changed_fields:
                    step.save(update_fields=changed_fields)

            desired_departments = {
                departments_by_name[department_name]
                for department_name in desired_department_names
            }

            current_departments = set(step.departments.all())
            departments_changed = current_departments != desired_departments

            if departments_changed:
                step.departments.set(desired_departments)

            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created activity step: {step.name}"
                    )
                )
            elif changed_fields or departments_changed:
                updated_count += 1

                changes = list(changed_fields)

                if departments_changed:
                    changes.append("departments")

                self.stdout.write(
                    self.style.WARNING(
                        f"Updated activity step: {step.name} "
                        f"({', '.join(changes)})"
                    )
                )
            else:
                unchanged_count += 1
                self.stdout.write(
                    f"Unchanged activity step: {step.name}"
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("Activity step seed summary")
        )
        self.stdout.write(f"Created:   {created_count}")
        self.stdout.write(f"Updated:   {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}")