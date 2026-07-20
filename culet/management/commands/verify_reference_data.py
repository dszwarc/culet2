from django.core.management.base import BaseCommand, CommandError

from culet.models import (
    ActivityStep,
    Department,
    FailureType,
    JobStatus,
    Location,
    Role,
)


EXPECTED_ROLES = {
    "Hourly": {
        "level": 0,
        "requires_clock_in": True,
        "can_start_activities": True,
        "can_receive_all_jobs": False,
        "active": True,
    },
    "Department Head": {
        "level": 10,
        "requires_clock_in": True,
        "can_start_activities": True,
        "can_receive_all_jobs": False,
        "active": True,
    },
    "Manager": {
        "level": 30,
        "requires_clock_in": False,
        "can_start_activities": True,
        "can_receive_all_jobs": True,
        "active": True,
    },
    "Super": {
        "level": 50,
        "requires_clock_in": False,
        "can_start_activities": True,
        "can_receive_all_jobs": True,
        "active": True,
    },
}


EXPECTED_DEPARTMENTS = {
    "Polishing",
    "Setting",
    "Quality Control",
    "Office",
    "Laser",
    "Production Management",
    "Jewelry",
    "Jewelry 37",
    "Homework",
}


EXPECTED_LOCATIONS = {
    "Office",
    "Jewelry",
    "Setting",
    "Polishing",
    "Laser",
    "Quality Control",
    "Production Management",
    "Homework",
    "Piecework",
    "Safe 1",
    "Safe 2",
    "Safe 3",
    "Unknown",
}


EXPECTED_JOB_STATUSES = {
    "Waiting on Metal": {
        "sort_order": 0,
        "active": True,
    },
    "Waiting on Stones": {
        "sort_order": 0,
        "active": True,
    },
    "Active": {
        "sort_order": 0,
        "active": True,
    },
    "Imported": {
        "sort_order": 10,
        "active": True,
    },
    "Shipped": {
        "sort_order": 100,
        "active": True,
    },
}


EXPECTED_ACTIVITY_STEPS = {
    "Adding Findings": {
        "code": "addfind",
        "departments": {"Jewelry", "Jewelry 37"},
    },
    "After Setter": {
        "code": "afterset",
        "departments": {"Jewelry", "Jewelry 37"},
    },
    "Assembly": {
        "code": "assm",
        "departments": {"Jewelry", "Jewelry 37"},
    },
    "Cleaning": {
        "code": "clean",
        "departments": {"Jewelry", "Jewelry 37"},
    },
    "Final Polish": {
        "code": "finalpol",
        "departments": {"Polishing"},
    },
    "Inspection": {
        "code": "qc",
        "departments": {"Quality Control"},
    },
    "Polish before stamp": {
        "code": "polstamp",
        "departments": {"Polishing"},
    },
    "Pre-polish": {
        "code": "prepol",
        "departments": {"Polishing"},
    },
    "Pre-polish for set": {
        "code": "prepolset",
        "departments": {"Polishing"},
    },
    "Repair": {
        "code": "repair",
        "departments": {"Jewelry", "Jewelry 37", "Polishing"},
    },
    "Set center(s)": {
        "code": "setcenter",
        "departments": {"Setting"},
    },
    "Set melee": {
        "code": "setmel",
        "departments": {"Setting"},
    },
}


EXPECTED_FAILURE_TYPES = {
    "Assembly Issues",
    "Chipped Stones",
    "Cracks",
    "Crooked Stones",
    "Design Incorrect",
    "Excess Metal",
    "Functionality Issue",
    "Insufficient Prong Contact",
    "Loose Stones",
    "Over-polished",
    "Over-worked Stamp",
    "Plating Issues",
    "Porosity",
    "Re-Cast (Internal Repair)",
    "Scratches",
    "Tool Marks",
    "Undefined prongs/beads unfinished",
    "Under-polished",
    "Uneven prongs",
    "Wrong / Bad Stamp",
}


class Command(BaseCommand):
    help = "Verify that standard Culet reference data is complete and correct."

    def handle(self, *args, **options):
        errors = []

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Culet reference data verification"
            )
        )

        self.verify_roles(errors)
        self.verify_named_active_records(
            label="Departments",
            model=Department,
            expected_names=EXPECTED_DEPARTMENTS,
            errors=errors,
        )
        self.verify_named_active_records(
            label="Locations",
            model=Location,
            expected_names=EXPECTED_LOCATIONS,
            errors=errors,
        )
        self.verify_job_statuses(errors)
        self.verify_activity_steps(errors)
        self.verify_named_active_records(
            label="Failure types",
            model=FailureType,
            expected_names=EXPECTED_FAILURE_TYPES,
            errors=errors,
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("Verification summary")
        )

        if errors:
            self.stdout.write(
                self.style.ERROR(
                    f"FAILED: {len(errors)} problem(s) found."
                )
            )

            for error in errors:
                self.stdout.write(self.style.ERROR(f"- {error}"))

            raise CommandError(
                "Reference data verification failed. "
                "Run seed_reference_data and review the errors above."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "PASSED: All expected reference data is present and correct."
            )
        )

    def verify_roles(self, errors):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("Roles"))

        records = {
            role.name: role
            for role in Role.objects.all()
        }

        for name, expected in EXPECTED_ROLES.items():
            role = records.get(name)

            if role is None:
                errors.append(f"Missing role: {name}")
                self.stdout.write(self.style.ERROR(f"Missing: {name}"))
                continue

            mismatches = []

            for field_name, expected_value in expected.items():
                actual_value = getattr(role, field_name)

                if actual_value != expected_value:
                    mismatches.append(
                        f"{field_name}={actual_value!r}, "
                        f"expected {expected_value!r}"
                    )

            if mismatches:
                errors.append(
                    f"Role {name}: " + "; ".join(mismatches)
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"{name}: " + "; ".join(mismatches)
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK: {name} "
                        f"(level={role.level})"
                    )
                )

        self.stdout.write(
            f"Expected: {len(EXPECTED_ROLES)} | "
            f"Database total: {Role.objects.count()}"
        )

    def verify_named_active_records(
        self,
        *,
        label,
        model,
        expected_names,
        errors,
    ):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(label))

        records = {
            record.name: record
            for record in model.objects.all()
        }

        for name in sorted(expected_names):
            record = records.get(name)

            if record is None:
                errors.append(f"Missing {label.lower()[:-1]}: {name}")
                self.stdout.write(self.style.ERROR(f"Missing: {name}"))
                continue

            if not record.active:
                errors.append(
                    f"Inactive {label.lower()[:-1]}: {name}"
                )
                self.stdout.write(
                    self.style.ERROR(f"Inactive: {name}")
                )
                continue

            self.stdout.write(self.style.SUCCESS(f"OK: {name}"))

        self.stdout.write(
            f"Expected: {len(expected_names)} | "
            f"Database total: {model.objects.count()}"
        )

    def verify_job_statuses(self, errors):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("Job statuses"))

        records = {
            status.name: status
            for status in JobStatus.objects.all()
        }

        for name, expected in EXPECTED_JOB_STATUSES.items():
            status = records.get(name)

            if status is None:
                errors.append(f"Missing job status: {name}")
                self.stdout.write(self.style.ERROR(f"Missing: {name}"))
                continue

            mismatches = []

            for field_name, expected_value in expected.items():
                actual_value = getattr(status, field_name)

                if actual_value != expected_value:
                    mismatches.append(
                        f"{field_name}={actual_value!r}, "
                        f"expected {expected_value!r}"
                    )

            if mismatches:
                errors.append(
                    f"Job status {name}: " + "; ".join(mismatches)
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"{name}: " + "; ".join(mismatches)
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK: {name} "
                        f"(sort_order={status.sort_order})"
                    )
                )

        self.stdout.write(
            f"Expected: {len(EXPECTED_JOB_STATUSES)} | "
            f"Database total: {JobStatus.objects.count()}"
        )

    def verify_activity_steps(self, errors):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("Activity steps"))

        records = {
            step.name: step
            for step in ActivityStep.objects.prefetch_related("departments")
        }

        for name, expected in EXPECTED_ACTIVITY_STEPS.items():
            step = records.get(name)

            if step is None:
                errors.append(f"Missing activity step: {name}")
                self.stdout.write(self.style.ERROR(f"Missing: {name}"))
                continue

            actual_departments = set(
                step.departments.values_list("name", flat=True)
            )

            mismatches = []

            if step.code != expected["code"]:
                mismatches.append(
                    f"code={step.code!r}, "
                    f"expected {expected['code']!r}"
                )

            if actual_departments != expected["departments"]:
                mismatches.append(
                    "departments="
                    f"{sorted(actual_departments)!r}, "
                    "expected "
                    f"{sorted(expected['departments'])!r}"
                )

            if mismatches:
                errors.append(
                    f"Activity step {name}: " + "; ".join(mismatches)
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"{name}: " + "; ".join(mismatches)
                    )
                )
            else:
                department_text = ", ".join(
                    sorted(actual_departments)
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK: {name} [{step.code}] "
                        f"-> {department_text}"
                    )
                )

        self.stdout.write(
            f"Expected: {len(EXPECTED_ACTIVITY_STEPS)} | "
            f"Database total: {ActivityStep.objects.count()}"
        )