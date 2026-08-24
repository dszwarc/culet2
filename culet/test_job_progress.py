from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Activity,
    ActivityStep,
    Customer,
    Department,
    Employee,
    Job,
    JobStone,
    Style,
)
from .services import get_job_progress


class JobProgressTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="progress-user",
            password="test",
        )
        self.employee = Employee.objects.create(
            user=self.user,
            must_change_password=False,
        )
        self.customer = Customer.objects.create(
            name="Progress Customer",
            address="1 Main Street",
            email="progress@example.com",
            phone="555-0100",
        )
        self.style = Style.objects.create(
            name="PROGRESS-STYLE",
            customer=self.customer,
        )
        self.job = Job.objects.create(
            name="Progress Job",
            barcode=99001,
            stock_num="PROGRESS-1",
            customer=self.customer,
            style=self.style,
            due=timezone.localdate() + timedelta(days=7),
        )
        self.jewelry = Department.objects.create(name="Jewelry")
        self.polishing = Department.objects.create(name="Polishing")
        self.polishing_37 = Department.objects.create(name="Polishing 37")
        self.setting = Department.objects.create(name="Setting")

    def make_step(self, name, code, *departments):
        step = ActivityStep.objects.create(name=name, code=code)
        step.departments.set(departments)
        return step

    def complete(self, step):
        now = timezone.now()
        return Activity.objects.create(
            name=step.name,
            step=step,
            employee=self.employee,
            job=self.job,
            start=now - timedelta(minutes=10),
            end=now,
            active=False,
        )

    def group_values(self, progress):
        return {
            group["name"]: (
                group["completed"],
                group["total"],
            )
            for group in progress["groups"]
        }

    def test_counts_each_completed_step_once_and_excludes_exceptions(self):
        assembly = self.make_step("Assembly", "assembly", self.jewelry)
        polish = self.make_step("Polish", "polish", self.polishing)
        repair = self.make_step("Repair", "repair", self.jewelry)
        piecework = self.make_step("Piecework", "piecework", self.jewelry)

        self.complete(assembly)
        self.complete(assembly)
        self.complete(repair)
        self.complete(piecework)

        progress = get_job_progress(self.job)

        self.assertEqual(
            self.group_values(progress),
            {"Jewelry": (1, 1), "Polishing": (0, 1)},
        )
        self.assertEqual(progress["completed_steps"], 1)
        self.assertEqual(progress["total_steps"], 2)
        self.assertEqual(progress["percent"], 50)
        self.assertEqual(progress["groups"][0]["progress_grade"], "high")
        self.assertEqual(
            [segment["state"] for segment in progress["groups"][0]["segments"]],
            ["completed"],
        )

    def test_requires_a_closed_activity_to_count_as_completed(self):
        step = self.make_step("Assembly", "assembly", self.jewelry)
        Activity.objects.create(
            name=step.name,
            step=step,
            employee=self.employee,
            job=self.job,
            active=False,
            end=None,
        )

        progress = get_job_progress(self.job)

        self.assertEqual(self.group_values(progress), {"Jewelry": (0, 1)})

    def test_open_activity_marks_step_active_without_completing_it(self):
        active_step = self.make_step("Assembly", "assembly", self.jewelry)
        self.make_step("Cleaning", "cleaning", self.jewelry)
        Activity.objects.create(
            name=active_step.name,
            step=active_step,
            employee=self.employee,
            job=self.job,
            active=True,
            end=None,
        )

        progress = get_job_progress(self.job)
        jewelry = progress["groups"][0]

        self.assertEqual(jewelry["completed"], 0)
        self.assertEqual(jewelry["in_progress"], 1)
        self.assertEqual(
            [segment["state"] for segment in jewelry["segments"]],
            ["active", "pending"],
        )

    def test_setting_is_hidden_without_stones_and_included_with_stones(self):
        self.make_step("Assembly", "assembly", self.jewelry)
        self.make_step("Set center", "set-center", self.setting)

        self.assertNotIn("Setting", self.group_values(get_job_progress(self.job)))

        JobStone.objects.create(job=self.job, qty_req=1)

        self.assertEqual(
            self.group_values(get_job_progress(self.job))["Setting"],
            (0, 1),
        )

    def test_shared_step_is_assigned_to_one_stable_department(self):
        second_jewelry = Department.objects.create(name="Jewelry 37")
        shared = self.make_step(
            "Assembly",
            "assembly",
            self.jewelry,
            second_jewelry,
        )
        self.complete(shared)

        progress = get_job_progress(self.job)

        self.assertEqual(progress["completed_steps"], 1)
        self.assertEqual(progress["total_steps"], 1)
        self.assertEqual(len(progress["groups"]), 1)

    def test_polishing_locations_are_one_distinct_logical_group(self):
        shared = self.make_step(
            "Final Polish",
            "final-polish",
            self.polishing,
            self.polishing_37,
        )
        self.make_step(
            "Pre-polish",
            "pre-polish",
            self.polishing,
        )
        location_only = self.make_step(
            "Polish before stamp",
            "polish-stamp",
            self.polishing_37,
        )

        self.employee.department = self.polishing_37
        self.employee.save(update_fields=["department"])
        self.complete(shared)
        self.complete(shared)
        self.complete(location_only)

        progress = get_job_progress(self.job)

        self.assertEqual(
            self.group_values(progress),
            {"Polishing": (2, 3)},
        )
        self.assertEqual(
            [group["name"] for group in progress["groups"]],
            ["Polishing"],
        )

    def test_only_supported_groups_appear_in_fixed_order(self):
        quality_control = Department.objects.create(name="Quality Control")
        laser = Department.objects.create(name="Laser")
        self.make_step("Assembly", "assembly", self.jewelry)
        self.make_step("Inspection", "inspection", quality_control)
        self.make_step("Laser weld", "laser-weld", laser)
        self.make_step("Final Polish", "final-polish", self.polishing_37)
        self.make_step("Set center", "set-center", self.setting)
        JobStone.objects.create(job=self.job, qty_req=1)

        progress = get_job_progress(self.job)

        self.assertEqual(
            [group["name"] for group in progress["groups"]],
            ["Jewelry", "Polishing", "Setting"],
        )
        self.assertNotIn("Quality Control", self.group_values(progress))
        self.assertNotIn("Laser", self.group_values(progress))

    def test_jobs_index_renders_compact_progress_and_prefetches_per_page(self):
        assembly = self.make_step("Assembly", "assembly", self.jewelry)
        self.complete(assembly)
        self.client.force_login(self.user)

        # Includes authentication, pagination, and the filter form's choices;
        # activity and stone facts remain one bulk query each for the page.
        with self.assertNumQueries(17):
            response = self.client.get(reverse("culet:index_job"))

        self.assertContains(response, "Progress")
        self.assertContains(response, "Jewelry")
        self.assertContains(response, "1 of 1 steps completed")
        self.assertContains(response, "progress-grade-high")
        self.assertContains(response, "job-progress-bubble is-completed")
