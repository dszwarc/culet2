from datetime import timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .models import (
    Activity,
    ActivityStep,
    Customer,
    Department,
    Employee,
    Job,
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
