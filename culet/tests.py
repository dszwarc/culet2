from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

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
from .views import MyJobListView


class MyJobsRunningTimerTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="Production")
        self.role = Role.objects.create(name="Jeweler")
        self.user = User.objects.create_user(username="worker", password="test")
        self.employee = Employee.objects.create(
            user=self.user,
            department=self.department,
            role=self.role,
            must_change_password=False,
        )
        self.other_user = User.objects.create_user(username="other", password="test")
        self.other_employee = Employee.objects.create(
            user=self.other_user,
            department=self.department,
            role=self.role,
        )
        self.customer = Customer.objects.create(
            name="Customer",
            address="1 Main St",
            email="customer@example.com",
            phone="555-0100",
        )
        self.style = Style.objects.create(name="STYLE", customer=self.customer)
        self.normal_step = ActivityStep.objects.create(name="Setting", code="setting")
        self.repair_step = ActivityStep.objects.create(name="Repair", code="repair")
        self.factory = RequestFactory()

    def make_job(self, suffix="1"):
        return Job.objects.create(
            name="Job",
            stock_num="JOB-" + suffix,
            style=self.style,
            due=timezone.localdate() + timedelta(days=7),
            assigned_to=self.employee,
            holder=self.employee,
        )

    def queryset(self):
        request = self.factory.get("/jobs/my_jobs/")
        request.user = self.user
        view = MyJobListView()
        view.setup(request)
        return view.get_queryset()

    def test_normal_open_activity_exposes_start_and_type(self):
        job = self.make_job()
        started_at = timezone.now() - timedelta(minutes=12)
        activity = Activity.objects.create(
            job=job,
            employee=self.employee,
            step=self.normal_step,
            start=started_at,
        )

        result = self.queryset().get(pk=job.pk)

        self.assertEqual(result.running_start, started_at)
        self.assertEqual(result.running_activity_id, activity.pk)
        self.assertEqual(result.running_timer_type, "normal")

    def test_repair_activity_exposes_repair_start(self):
        job = self.make_job()
        started_at = timezone.now() - timedelta(minutes=4)
        activity = Activity.objects.create(
            job=job,
            employee=self.employee,
            step=self.repair_step,
            start=started_at,
        )

        result = self.queryset().get(pk=job.pk)

        self.assertEqual(result.running_start, started_at)
        self.assertEqual(result.running_activity_id, activity.pk)
        self.assertEqual(result.running_timer_type, "repair")

    def test_stopped_activity_does_not_expose_timer(self):
        job = self.make_job()
        started_at = timezone.now() - timedelta(minutes=4)
        Activity.objects.create(
            job=job,
            employee=self.employee,
            step=self.normal_step,
            start=started_at,
            end=timezone.now(),
            active=False,
        )

        result = self.queryset().get(pk=job.pk)

        self.assertIsNone(result.running_start)
        self.assertIsNone(result.running_activity_id)
        self.assertEqual(result.running_timer_type, "")

    def test_other_employees_activity_is_not_used(self):
        job = self.make_job()
        Activity.objects.create(
            job=job,
            employee=self.other_employee,
            step=self.normal_step,
            start=timezone.now() - timedelta(minutes=4),
        )

        result = self.queryset().get(pk=job.pk)

        self.assertIsNone(result.running_start)
        self.assertIsNone(result.running_activity_id)

    def test_job_without_activity_does_not_render_timer(self):
        job = self.make_job()

        response = self.client.get("/jobs/my_jobs/")
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.user)
        response = self.client.get("/jobs/my_jobs/")

        self.assertContains(response, job.stock_num)
        self.assertNotContains(response, 'class="activity-timer"')

    def test_repair_precedes_normal_if_conflicting_activities_exist(self):
        job = self.make_job()
        normal = Activity.objects.create(
            job=job,
            employee=self.employee,
            step=self.normal_step,
            start=timezone.now() - timedelta(minutes=10),
        )
        repair = Activity.objects.create(
            job=job,
            employee=self.employee,
            step=self.repair_step,
            start=timezone.now() - timedelta(minutes=2),
        )

        result = self.queryset().get(pk=job.pk)

        self.assertNotEqual(result.running_activity_id, normal.pk)
        self.assertEqual(result.running_activity_id, repair.pk)
        self.assertEqual(result.running_start, repair.start)
        self.assertEqual(result.running_timer_type, "repair")

    def test_query_count_is_constant_as_job_count_grows(self):
        for suffix in range(1, 7):
            job = self.make_job(str(suffix))
            Activity.objects.create(
                job=job,
                employee=self.employee,
                step=self.normal_step,
                start=timezone.now(),
            )

        request = self.factory.get("/jobs/my_jobs/")
        request.user = self.user
        view = MyJobListView()
        view.setup(request)
        view.get_employee()

        with self.assertNumQueries(1):
            results = list(view.get_queryset())
            self.assertEqual(len(results), 6)


class AssignJobDuplicateBarcodeTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="Assignment")
        self.role = Role.objects.create(name="Department Head", level=20)
        self.user = User.objects.create_user(username="assigner", password="test")
        self.assigner = Employee.objects.create(
            user=self.user,
            department=self.department,
            role=self.role,
            must_change_password=False,
        )
        target_user = User.objects.create_user(username="target", password="test")
        self.target = Employee.objects.create(
            user=target_user,
            department=self.department,
            role=self.role,
            must_change_password=False,
        )
        self.customer = Customer.objects.create(
            name="Assignment Customer",
            address="1 Main St",
            email="assignment@example.com",
            phone="555-0101",
        )
        self.style = Style.objects.create(
            name="ASSIGN-STYLE",
            customer=self.customer,
        )
        self.assignment_type = MovementType.objects.create(
            name="Assigned",
            code="assigned",
            job_field=MovementType.JobField.ASSIGNED_TO,
        )
        self.url = reverse("culet:assign_job")
        self.client.force_login(self.user)

    def make_job(self, barcode, assigned_to=None):
        return Job.objects.create(
            name="Assignment Job",
            barcode=barcode,
            stock_num=f"ASSIGN-{barcode}",
            style=self.style,
            due=timezone.localdate() + timedelta(days=7),
            assigned_to=assigned_to or self.assigner,
            holder=self.assigner,
        )

    def submit(self, jobs_text):
        return self.client.post(
            self.url,
            {
                "employee": str(self.target.pk),
                "jobs_text": jobs_text,
            },
            follow=True,
        )

    @staticmethod
    def response_messages(response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_unique_barcodes_assign_all_jobs(self):
        first = self.make_job(11001)
        second = self.make_job(11002)

        response = self.submit("11001\n11002")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.assigned_to, self.target)
        self.assertEqual(second.assigned_to, self.target)
        self.assertEqual(JobMovement.objects.count(), 2)
        self.assertIn("2 jobs assigned.", self.response_messages(response))

    def test_barcode_entered_twice_assigns_once_and_reports_one_repeat(self):
        job = self.make_job(12001)

        response = self.submit("12001\n12001")

        job.refresh_from_db()
        self.assertEqual(job.assigned_to, self.target)
        self.assertEqual(JobMovement.objects.filter(job=job).count(), 1)
        self.assertIn(
            "1 job assigned, 1 repeated barcode ignored.",
            self.response_messages(response),
        )

    def test_barcode_entered_three_times_reports_two_repeats(self):
        job = self.make_job(13001)

        response = self.submit("13001, 13001; 13001")

        self.assertEqual(JobMovement.objects.filter(job=job).count(), 1)
        self.assertIn(
            "1 job assigned, 2 repeated barcodes ignored.",
            self.response_messages(response),
        )

    def test_multiple_repeated_barcodes_count_every_additional_entry(self):
        first = self.make_job(14001)
        second = self.make_job(14002)

        response = self.submit("14001\t14001\n14002,14002")

        self.assertEqual(JobMovement.objects.filter(job__in=[first, second]).count(), 2)
        self.assertIn(
            "2 jobs assigned, 2 repeated barcodes ignored.",
            self.response_messages(response),
        )

    def test_blank_lines_do_not_count_as_repeats(self):
        self.make_job(15001)

        response = self.submit("\n15001\n\n")

        self.assertIn("1 job assigned.", self.response_messages(response))

    def test_whitespace_is_normalized_before_duplicate_detection(self):
        job = self.make_job(16001)

        response = self.submit(" 16001 \n\t16001  ")

        self.assertEqual(JobMovement.objects.filter(job=job).count(), 1)
        self.assertIn(
            "1 job assigned, 1 repeated barcode ignored.",
            self.response_messages(response),
        )

    def test_assigned_count_only_includes_jobs_that_changed_assignment(self):
        already_assigned = self.make_job(17001, assigned_to=self.target)
        newly_assigned = self.make_job(17002)

        response = self.submit("17001\n17002\n17002")

        self.assertEqual(JobMovement.objects.filter(job=already_assigned).count(), 0)
        self.assertEqual(JobMovement.objects.filter(job=newly_assigned).count(), 1)
        self.assertIn(
            "1 job assigned, 1 repeated barcode ignored.",
            self.response_messages(response),
        )

    def test_invalid_unique_barcode_preserves_all_or_nothing_behavior(self):
        job = self.make_job(18001)

        response = self.submit("18001\n99999\n99999")

        job.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(job.assigned_to, self.assigner)
        self.assertEqual(JobMovement.objects.count(), 0)
        self.assertIn(
            "No job was found for the following barcode(s): 99999",
            self.response_messages(response),
        )
