import csv
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .management.commands.reset_legacy_job_assignments import Command
from .models import (
    Activity,
    ActivityStep,
    Customer,
    Department,
    Employee,
    Job,
    JobMovement,
    MovementType,
    Role,
    Style,
)


class ResetLegacyJobAssignmentsTests(TestCase):
    def setUp(self):
        self.manager = self.make_employee("manager", "Target", "Manager")
        self.worker = self.make_employee("worker", "Legacy", "Worker")
        self.other_worker = self.make_employee("other", "Other", "Holder")
        self.assigned_type = MovementType.objects.create(
            name="Assigned",
            code="assigned",
            job_field=MovementType.JobField.ASSIGNED_TO,
        )
        self.received_type = MovementType.objects.create(
            name="Received",
            code="received",
            job_field=MovementType.JobField.HOLDER,
        )
        self.customer = Customer.objects.create(
            name="Legacy Customer",
            address="1 Main Street",
            email="legacy@example.com",
            phone="555-0100",
        )
        self.style = Style.objects.create(
            name="LEGACY-STYLE",
            customer=self.customer,
        )
        self.launch_date = timezone.localdate() - timedelta(days=30)
        self.launch_arg = self.launch_date.isoformat()
        self.old_time = timezone.now() - timedelta(days=60)
        self.next_barcode = 88000

    def make_employee(self, username, first_name, last_name):
        user = User.objects.create_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        return Employee.objects.create(user=user)

    def make_job(self, **overrides):
        self.next_barcode += 1
        values = {
            "name": "Legacy Job",
            "barcode": self.next_barcode,
            "stock_num": f"LEGACY-{self.next_barcode}",
            "customer": self.customer,
            "style": self.style,
            "due": timezone.localdate() + timedelta(days=7),
            "assigned_to": self.worker,
            "holder": self.other_worker,
            "active": True,
            "shipped": False,
        }
        values.update(overrides)
        job = Job.objects.create(**values)
        Job.objects.filter(pk=job.pk).update(created=self.old_time)
        job.refresh_from_db()
        return job

    def run_command(self, *, dry_run=False, output=None):
        stdout = StringIO()
        stderr = StringIO()
        kwargs = {
            "manager_id": self.manager.pk,
            "launch_date": self.launch_arg,
            "stdout": stdout,
            "stderr": stderr,
        }
        if dry_run:
            kwargs["dry_run"] = True
        if output:
            kwargs["output"] = output
        call_command("reset_legacy_job_assignments", **kwargs)
        return stdout.getvalue(), stderr.getvalue()

    def create_movement(self, job, *, created_at=None):
        movement = JobMovement.objects.create(
            job=job,
            movement_type=self.assigned_type,
            from_employee=self.worker,
            to_employee=self.other_worker,
            performed_by=self.worker,
        )
        if created_at:
            JobMovement.objects.filter(pk=movement.pk).update(
                created_at=created_at
            )
            movement.refresh_from_db()
        return movement

    def set_job_created(self, job, created_at):
        Job.objects.filter(pk=job.pk).update(created=created_at)
        job.refresh_from_db()
        return job

    def test_eligible_job_resets_both_fields_and_creates_audit_movements(self):
        job = self.make_job()

        self.run_command()

        job.refresh_from_db()
        self.assertEqual(job.assigned_to, self.manager)
        self.assertEqual(job.holder, self.manager)
        movements = list(job.movements.order_by("created_at", "pk"))
        self.assertEqual(len(movements), 2)
        self.assertEqual(movements[0].movement_type, self.assigned_type)
        self.assertEqual(movements[0].from_employee, self.worker)
        self.assertEqual(movements[1].movement_type, self.received_type)
        self.assertEqual(movements[1].from_employee, self.other_worker)

    def test_recent_activity_or_movement_excludes_job(self):
        activity_job = self.make_job()
        movement_job = self.make_job()
        step = ActivityStep.objects.create(name="Assembly", code="assembly")
        Activity.objects.create(
            name=step.name,
            step=step,
            employee=self.worker,
            job=activity_job,
            start=timezone.now(),
            active=True,
        )
        self.create_movement(movement_job)

        self.run_command()

        activity_job.refresh_from_db()
        movement_job.refresh_from_db()
        self.assertEqual(activity_job.assigned_to, self.worker)
        self.assertEqual(movement_job.holder, self.other_worker)

    def test_jobs_created_at_or_after_launch_are_excluded(self):
        launch_cutoff = Command()._parse_launch_cutoff(self.launch_arg)
        before_launch = self.make_job()
        at_launch = self.set_job_created(self.make_job(), launch_cutoff)
        after_launch = self.set_job_created(
            self.make_job(),
            launch_cutoff + timedelta(seconds=1),
        )
        recent = self.set_job_created(self.make_job(), timezone.now())

        stdout, _ = self.run_command(dry_run=True)

        self.assertIn(
            "Created before launch with no movement AND no activity "
            "since launch: 1",
            stdout,
        )
        for excluded_job in (at_launch, after_launch, recent):
            excluded_job.refresh_from_db()
            self.assertEqual(excluded_job.assigned_to, self.worker)
        before_launch.refresh_from_db()
        self.assertEqual(before_launch.assigned_to, self.worker)

    def test_inactive_and_shipped_jobs_are_not_reset(self):
        inactive_job = self.make_job(active=False)
        shipped_job = self.make_job(shipped=True)

        self.run_command()

        inactive_job.refresh_from_db()
        shipped_job.refresh_from_db()
        self.assertEqual(inactive_job.assigned_to, self.worker)
        self.assertEqual(shipped_job.assigned_to, self.worker)

    def test_current_employee_role_and_department_do_not_affect_reset(self):
        department = Department.objects.create(name="Office")
        role = Role.objects.create(name="Office Role", requires_clock_in=False)
        self.worker.department = department
        self.worker.role = role
        self.worker.save(update_fields=["department", "role"])
        job = self.make_job()

        self.run_command()

        job.refresh_from_db()
        self.assertEqual(job.assigned_to, self.manager)

    def test_dry_run_changes_nothing_and_projects_inactive_overlap(self):
        eligible_inactive = self.make_job()
        stalled_post_launch = self.make_job()
        self.create_movement(
            stalled_post_launch,
            created_at=timezone.now() - timedelta(days=10),
        )

        stdout, _ = self.run_command(dry_run=True)

        eligible_inactive.refresh_from_db()
        self.assertEqual(eligible_inactive.assigned_to, self.worker)
        self.assertEqual(eligible_inactive.movements.count(), 0)
        self.assertIn("No movement in last 7 days: 2", stdout)
        self.assertIn("Inactive jobs cleared by reset: 1", stdout)
        self.assertIn("Inactive jobs remaining afterward: 1", stdout)
        self.assertIn("No database changes made.", stdout)

    def test_csv_preserves_previous_assignment_and_holder(self):
        job = self.make_job()
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "legacy-reset.csv"

            self.run_command(dry_run=True, output=str(output_path))

            with output_path.open(newline="", encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], str(job.pk))
        self.assertEqual(rows[0]["previous_assigned_to_id"], str(self.worker.pk))
        self.assertEqual(rows[0]["previous_holder_id"], str(self.other_worker.pk))
        self.assertEqual(rows[0]["result"], "WOULD_RESET")

    def test_final_recheck_skips_job_moved_after_initial_scan(self):
        job = self.make_job()
        original_recheck = Command._is_still_eligible
        inserted = False

        def move_before_recheck(command, locked_job, cutoff):
            nonlocal inserted
            if not inserted:
                inserted = True
                self.create_movement(locked_job)
            return original_recheck(command, locked_job, cutoff)

        with patch.object(
            Command,
            "_is_still_eligible",
            autospec=True,
            side_effect=move_before_recheck,
        ):
            stdout, _ = self.run_command()

        job.refresh_from_db()
        self.assertEqual(job.assigned_to, self.worker)
        self.assertIn("Skipped - no longer eligible: 1", stdout)

    def test_final_locked_recheck_rejects_job_not_created_before_launch(self):
        job = self.make_job()
        launch_cutoff = Command()._parse_launch_cutoff(self.launch_arg)
        self.set_job_created(job, launch_cutoff)

        result = Command()._reset_job(
            job_id=job.pk,
            manager=self.manager,
            launch_cutoff=launch_cutoff,
            assignment_type=self.assigned_type,
            holder_type=self.received_type,
        )

        job.refresh_from_db()
        self.assertEqual(result, "SKIPPED_NO_LONGER_ELIGIBLE")
        self.assertEqual(job.assigned_to, self.worker)
        self.assertEqual(job.holder, self.other_worker)
        self.assertEqual(job.movements.count(), 0)

    def test_one_job_failure_does_not_prevent_another_reset(self):
        failed_job = self.make_job()
        successful_job = self.make_job()
        real_move_job = __import__(
            "culet.management.commands.reset_legacy_job_assignments",
            fromlist=["move_job"],
        ).move_job

        def selective_failure(**kwargs):
            if kwargs["job"].pk == failed_job.pk:
                raise RuntimeError("simulated movement failure")
            return real_move_job(**kwargs)

        with patch(
            "culet.management.commands.reset_legacy_job_assignments.move_job",
            side_effect=selective_failure,
        ):
            stdout, stderr = self.run_command()

        failed_job.refresh_from_db()
        successful_job.refresh_from_db()
        self.assertEqual(failed_job.assigned_to, self.worker)
        self.assertEqual(failed_job.holder, self.other_worker)
        self.assertEqual(successful_job.assigned_to, self.manager)
        self.assertEqual(successful_job.holder, self.manager)
        self.assertIn("Skipped - errors: 1", stdout)
        self.assertIn("simulated movement failure", stderr)
