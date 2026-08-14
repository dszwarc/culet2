from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.template.loader import render_to_string
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
    JobWeight,
    MovementType,
    Role,
    Step,
    Style,
    WorkBatch,
)
from .forms import BatchStartForm, JobWeightForm, JobWeightLookupForm
from .services import (
    clock_out_employee,
    start_work_batch,
    stop_work_batch,
    validate_batch_jobs,
    parse_barcode_input,
)
from .views import MyJobListView


class JobWeightLookupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="weight-user", password="test")
        self.customer = Customer.objects.create(
            name="Weight Customer",
            address="1 Scale Way",
            email="weights@example.com",
            phone="555-0199",
        )
        self.style = Style.objects.create(name="WEIGHT-STYLE", customer=self.customer)
        self.url = reverse("culet:job_weight_lookup")
        self.client.force_login(self.user)

    def make_job(self, barcode, stock_num):
        return Job.objects.create(
            name="Weight Job",
            barcode=barcode,
            stock_num=stock_num,
            style=self.style,
            due=timezone.localdate() + timedelta(days=7),
        )

    def test_form_accepts_either_identifier_and_normalizes_stock_number(self):
        barcode_form = JobWeightLookupForm({"barcode": " 123456 ", "stock_num": ""})
        self.assertTrue(barcode_form.is_valid())
        self.assertEqual(barcode_form.cleaned_data["barcode"], 123456)

        stock_form = JobWeightLookupForm({"barcode": "", "stock_num": "  JOB-42  "})
        self.assertTrue(stock_form.is_valid())
        self.assertEqual(stock_form.cleaned_data["stock_num"], "JOB-42")

    def test_form_fields_are_individually_optional_but_both_blank_is_invalid(self):
        form = JobWeightLookupForm({"barcode": "", "stock_num": "  "})
        self.assertFalse(form.fields["barcode"].required)
        self.assertFalse(form.fields["stock_num"].required)
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.non_field_errors(),
            ["Enter a barcode or stock number."],
        )

    def test_get_renders_optional_fields_with_barcode_autofocus(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("autofocus", form["barcode"].as_widget())
        self.assertNotIn("autofocus", form["stock_num"].as_widget())
        self.assertNotIn("required", form["barcode"].as_widget())
        self.assertNotIn("required", form["stock_num"].as_widget())
        self.assertContains(response, "Scan a barcode or enter a stock number")

    def test_barcode_only_finds_job(self):
        job = self.make_job(123456, "WEIGHT-1")
        response = self.client.post(self.url, {"barcode": job.barcode, "stock_num": ""})
        self.assertRedirects(
            response,
            reverse("culet:job_weight_create", args=[job.pk]),
        )

    def test_stock_number_only_finds_job(self):
        job = self.make_job(123457, "WEIGHT-2")
        response = self.client.post(self.url, {"barcode": "", "stock_num": job.stock_num})
        self.assertRedirects(
            response,
            reverse("culet:job_weight_create", args=[job.pk]),
        )

    def test_matching_identifiers_find_job(self):
        job = self.make_job(123458, "WEIGHT-3")
        response = self.client.post(
            self.url,
            {"barcode": job.barcode, "stock_num": job.stock_num},
        )
        self.assertRedirects(
            response,
            reverse("culet:job_weight_create", args=[job.pk]),
        )

    def test_mismatched_identifiers_are_rejected_and_values_preserved(self):
        first = self.make_job(123459, "WEIGHT-4")
        second = self.make_job(123460, "WEIGHT-5")
        response = self.client.post(
            self.url,
            {"barcode": first.barcode, "stock_num": second.stock_num},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "The barcode and stock number do not belong to the same job.",
        )
        self.assertContains(response, str(first.barcode))
        self.assertContains(response, second.stock_num)

    def test_unknown_and_empty_submissions_redisplay_errors(self):
        unknown = self.client.post(
            self.url,
            {"barcode": "999999", "stock_num": ""},
        )
        self.assertEqual(unknown.status_code, 200)
        self.assertContains(unknown, "No job found with the provided number.")
        self.assertContains(unknown, "999999")

        empty = self.client.post(self.url, {"barcode": "", "stock_num": ""})
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, "Enter a barcode or stock number.")

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)


class JobWeightCreateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="weight-entry", password="test")
        self.customer = Customer.objects.create(
            name="Weight Entry Customer",
            address="1 Scale Way",
            email="entry-weights@example.com",
            phone="555-0188",
        )
        self.style = Style.objects.create(
            name="WEIGHT-ENTRY-STYLE",
            customer=self.customer,
        )
        self.step = Step.objects.create(name="Casting", code="casting")
        self.job = Job.objects.create(
            name="Weight Entry Job",
            barcode=123461,
            stock_num="WEIGHT-ENTRY-1",
            style=self.style,
            due=timezone.localdate() + timedelta(days=7),
        )
        self.url = reverse("culet:job_weight_create", args=[self.job.pk])
        self.client.force_login(self.user)

    def make_weight(self, *, created_at, weight, sprue_weight, dust_weight):
        return JobWeight.objects.create(
            job=self.job,
            step=self.step,
            created_at=created_at,
            weight=weight,
            sprue_weight=sprue_weight,
            dust_weight=dust_weight,
            recorded_by=self.user,
        )

    def test_uses_most_recent_weight_across_all_steps(self):
        earlier_step = Step.objects.create(name="Wax", code="wax")
        earlier = self.make_weight(
            created_at=timezone.now() - timedelta(days=2),
            weight=Decimal("12.480"),
            sprue_weight=Decimal("1.320"),
            dust_weight=Decimal("0.110"),
        )
        earlier.step = earlier_step
        earlier.save(update_fields=["step"])
        latest = self.make_weight(
            created_at=timezone.now() - timedelta(hours=1),
            weight=Decimal("11.900"),
            sprue_weight=Decimal("1.100"),
            dust_weight=Decimal("0.090"),
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["previous_job_weight"], latest)
        self.assertContains(response, "11.900 g")
        self.assertContains(response, "1.100 g")
        self.assertContains(response, "0.090 g")
        self.assertContains(response, 'data-previous-weight="11.900"')
        self.assertNotEqual(response.context["previous_job_weight"], earlier)

    def test_no_previous_weight_displays_placeholders(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["previous_job_weight"])
        self.assertContains(response, "&ndash;", count=6)
        self.assertContains(response, 'data-previous-weight=""')

    def test_missing_previous_component_displays_placeholder(self):
        previous = JobWeight(
            job=self.job,
            step=self.step,
            weight=Decimal("12.480"),
            sprue_weight=None,
            dust_weight=Decimal("0.110"),
        )

        html = render_to_string(
            "jobs/weight_create.html",
            {
                "job": self.job,
                "form": JobWeightForm(),
                "previous_job_weight": previous,
            },
        )

        self.assertIn("12.480 g", html)
        self.assertIn("0.110 g", html)
        self.assertIn('data-previous-sprue-weight=""', html)

    def test_post_still_creates_weight_and_redirects_to_lookup(self):
        response = self.client.post(
            self.url,
            {
                "step": self.step.pk,
                "weight": "12.000",
                "sprue_weight": "1.250",
                "dust_weight": "0.075",
            },
        )

        self.assertRedirects(response, reverse("culet:job_weight_lookup"))
        created = JobWeight.objects.get(job=self.job)
        self.assertEqual(created.recorded_by, self.user)
        self.assertEqual(created.step, self.step)
        self.assertEqual(created.weight, Decimal("12.000"))
        self.assertEqual(created.sprue_weight, Decimal("1.250"))
        self.assertEqual(created.dust_weight, Decimal("0.075"))

    def test_percentage_loss_is_not_a_database_field(self):
        field_names = {field.name for field in JobWeight._meta.get_fields()}

        self.assertNotIn("loss_percent", field_names)
        self.assertNotIn("weight_loss_percent", field_names)
        self.assertNotIn("sprue_weight_loss_percent", field_names)
        self.assertNotIn("dust_weight_loss_percent", field_names)


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

    def make_job(self, suffix="1", **overrides):
        values = {
            "name": "Job",
            "stock_num": "JOB-" + suffix,
            "style": self.style,
            "due": timezone.localdate() + timedelta(days=7),
            "assigned_to": self.employee,
            "holder": self.employee,
        }
        values.update(overrides)
        return Job.objects.create(**values)

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

    def test_scan_to_start_uses_replace_mode_and_auto_submit(self):
        self.make_job()
        self.client.force_login(self.user)
        response = self.client.get(reverse("culet:my_jobs"))
        content = response.content.decode()

        self.assertContains(response, "Scan to Start")
        self.assertContains(response, 'name="barcode"', count=1)
        self.assertContains(response, 'id="scan-to-start-barcode"', count=1)
        self.assertContains(response, 'data-target="scan-to-start-barcode"')
        self.assertContains(response, 'data-scanner-mode="replace"')
        self.assertContains(response, 'data-append-mode="false"')
        self.assertContains(response, 'data-auto-submit="true"')
        self.assertEqual(content.count('id="culet-scanner-overlay"'), 1)

    def test_my_jobs_polls_shared_view_every_twenty_seconds(self):
        self.make_job()
        self.client.force_login(self.user)

        page = self.client.get(reverse("culet:my_jobs"))
        poll = self.client.get(reverse("culet:my_jobs_poll"))

        self.assertContains(page, 'hx-trigger="every 20s')
        self.assertContains(page, 'hx-select=".my-jobs-page"')
        self.assertEqual(
            list(page.context["latest_job_list"]),
            list(poll.context["latest_job_list"]),
        )
        self.assertEqual(
            page.context["my_jobs_total_count"],
            poll.context["my_jobs_total_count"],
        )

    def test_poll_requires_authentication(self):
        response = self.client.get(reverse("culet:my_jobs_poll"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_poll_reflects_jobs_entering_and_leaving_holder_state(self):
        leaving = self.make_job("LEAVING")
        entering = self.make_job(
            "ENTERING",
            assigned_to=self.other_employee,
            holder=self.other_employee,
        )
        self.client.force_login(self.user)

        initial = self.client.get(reverse("culet:my_jobs_poll"))
        self.assertContains(initial, leaving.stock_num)
        self.assertNotContains(initial, entering.stock_num)

        leaving.holder = self.other_employee
        leaving.save(update_fields=["holder"])
        entering.holder = self.employee
        entering.save(update_fields=["holder"])

        updated = self.client.get(reverse("culet:my_jobs_poll"))
        self.assertNotContains(updated, leaving.stock_num)
        self.assertContains(updated, entering.stock_num)
        self.assertEqual(updated.context["my_jobs_total_count"], 1)

    def test_poll_reflects_inprocess_repair_closing_other_employee_work(self):
        job = self.make_job("REPAIR", barcode=910001)
        activity = Activity.objects.create(
            job=job,
            employee=self.employee,
            step=self.normal_step,
            start=timezone.now() - timedelta(minutes=5),
        )
        self.client.force_login(self.user)
        before = self.client.get(reverse("culet:my_jobs_poll"))
        stop_url = reverse("culet:stop_work", args=[activity.pk, job.pk])
        self.assertContains(before, stop_url)

        self.other_employee.can_inprocess_repair = True
        self.other_employee.clocked_in = True
        self.other_employee.save(
            update_fields=["can_inprocess_repair", "clocked_in"],
        )
        self.client.force_login(self.other_user)
        response = self.client.post(
            reverse("culet:inprocess_repair"),
            {"action": "start", "barcode": str(job.barcode)},
        )
        self.assertRedirects(response, reverse("culet:my_jobs"))

        activity.refresh_from_db()
        self.assertFalse(activity.active)
        self.assertIsNotNone(activity.end)

        self.client.force_login(self.user)
        updated = self.client.get(reverse("culet:my_jobs_poll"))
        self.assertNotContains(updated, job.stock_num)
        self.assertNotContains(updated, stop_url)

        self.client.force_login(self.other_user)
        repair_view = self.client.get(reverse("culet:my_jobs_poll"))
        self.assertContains(repair_view, job.stock_num)
        self.assertContains(repair_view, "In-Process Repair")

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


class ScanToStartRegressionTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="Scan Production")
        self.role = Role.objects.create(name="Scan Worker")
        self.user = User.objects.create_user(username="scanner", password="test")
        self.employee = Employee.objects.create(
            user=self.user,
            department=self.department,
            role=self.role,
            clocked_in=True,
            can_start_batch=True,
            must_change_password=False,
        )
        self.customer = Customer.objects.create(
            name="Scan Customer",
            address="1 Scan Way",
            email="scan@example.com",
            phone="555-0166",
        )
        self.style = Style.objects.create(name="SCAN-STYLE", customer=self.customer)
        self.step = ActivityStep.objects.create(name="Scan Assembly", code="scan-assm")
        self.step.departments.add(self.department)
        self.scan_url = reverse("culet:scan_to_start")
        self.client.force_login(self.user)

    def make_job(self, suffix="1", **overrides):
        values = {
            "name": "Scan Job",
            "stock_num": f"SCAN-{suffix}",
            "style": self.style,
            "due": timezone.localdate() + timedelta(days=5),
            "assigned_to": self.employee,
            "holder": self.employee,
        }
        values.update(overrides)
        return Job.objects.create(**values)

    @staticmethod
    def response_messages(response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_scanned_whitespace_and_trailing_newline_redirect_to_step_selection(self):
        job = self.make_job()
        response = self.client.post(
            self.scan_url,
            {"barcode": f"  {job.barcode}\n"},
        )
        self.assertRedirects(
            response,
            reverse("culet:job_start", args=[job.pk]),
            fetch_redirect_response=False,
        )

        step_page = self.client.get(response.url)
        self.assertEqual(step_page.status_code, 200)
        self.assertContains(step_page, job.name)
        self.assertContains(step_page, self.step.name)

    def test_complete_scan_workflow_creates_activity_through_start_view(self):
        job = self.make_job()
        scan_response = self.client.post(self.scan_url, {"barcode": job.barcode})

        response = self.client.post(
            scan_response.url,
            {"step": self.step.pk},
        )

        self.assertRedirects(response, reverse("culet:my_jobs"))
        activity = Activity.objects.get(job=job)
        self.assertEqual(activity.employee, self.employee)
        self.assertEqual(activity.step, self.step)
        self.assertTrue(activity.active)
        self.assertIsNone(activity.end)

    def test_blank_and_unknown_scans_return_clear_errors(self):
        blank = self.client.post(self.scan_url, {"barcode": " \n "})
        self.assertRedirects(blank, reverse("culet:my_jobs"))
        self.assertIn(
            "Please scan or enter a job barcode.",
            self.response_messages(blank),
        )

        unknown = self.client.post(self.scan_url, {"barcode": "999999"})
        self.assertRedirects(unknown, reverse("culet:my_jobs"))
        self.assertIn(
            "Barcode 999999 was not found in your assigned jobs.",
            self.response_messages(unknown),
        )

    def test_scan_preserves_shipped_and_holder_validation(self):
        shipped = self.make_job("SHIPPED", shipped=True)
        shipped_scan = self.client.post(self.scan_url, {"barcode": shipped.barcode})
        shipped_result = self.client.get(shipped_scan.url)
        self.assertRedirects(shipped_result, reverse("culet:my_jobs"))
        self.assertIn("This job has already been shipped.", self.response_messages(shipped_result))

        other_user = User.objects.create_user(username="scan-holder")
        other_employee = Employee.objects.create(user=other_user)
        wrong_holder = self.make_job("HOLDER", holder=other_employee)
        holder_scan = self.client.post(self.scan_url, {"barcode": wrong_holder.barcode})
        holder_result = self.client.get(holder_scan.url)
        self.assertRedirects(holder_result, reverse("culet:my_jobs"))
        self.assertIn(
            "You must receive this job before starting work.",
            self.response_messages(holder_result),
        )

    def test_active_work_is_rejected_during_start_submission(self):
        job = self.make_job()
        Activity.objects.create(
            job=job,
            employee=self.employee,
            step=self.step,
        )
        scan_response = self.client.post(self.scan_url, {"barcode": job.barcode})
        response = self.client.post(scan_response.url, {"step": self.step.pk})
        self.assertRedirects(response, reverse("culet:my_jobs"))
        self.assertEqual(Activity.objects.filter(job=job).count(), 1)
        self.assertIn(
            f"Job {job.barcode} is already in work.",
            self.response_messages(response),
        )

    def test_active_batch_blocks_scanned_individual_start(self):
        jobs = [self.make_job("BATCH-1"), self.make_job("BATCH-2")]
        batch = start_work_batch(
            employee=self.employee,
            jobs=jobs,
            step=self.step,
        )
        extra_job = self.make_job("EXTRA")

        scan_response = self.client.post(self.scan_url, {"barcode": extra_job.barcode})
        response = self.client.get(scan_response.url)

        self.assertRedirects(response, reverse("culet:my_jobs"))
        self.assertTrue(WorkBatch.objects.get(pk=batch.pk).active)
        self.assertIn(
            "Stop your active batch before starting individual work.",
            self.response_messages(response),
        )


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

    def test_page_uses_shared_textarea_scanner_and_keeps_employee_picker(self):
        response = self.client.get(self.url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="jobs_text"', count=1)
        self.assertContains(response, 'data-target="id_jobs_text"')
        self.assertContains(response, 'data-append-mode="true"')
        self.assertContains(response, 'data-scanner-mode="append-lines"')
        self.assertContains(response, 'data-auto-submit="false"')
        self.assertContains(response, 'type="button"')
        self.assertContains(response, 'id="department-option-grid"')
        self.assertContains(response, 'id="employee-option-grid"')
        self.assertNotContains(response, "form-TOTAL_FORMS")
        self.assertNotContains(response, "add-assignment-line")
        self.assertEqual(content.count("barcode_scanner.js"), 1)
        self.assertLess(
            content.index('data-target="id_jobs_text"'),
            content.index('id="id_jobs_text"'),
        )

    def test_barcode_entered_twice_assigns_once_and_reports_one_repeat(self):
        job = self.make_job(12001)

        response = self.submit("12001\n12001")

        job.refresh_from_db()
        self.assertEqual(job.assigned_to, self.target)
        self.assertEqual(JobMovement.objects.filter(job=job).count(), 1)
        self.assertIn(
            "1 job assigned. 1 repeated barcode ignored.",
            self.response_messages(response),
        )

    def test_barcode_entered_three_times_reports_two_repeats(self):
        job = self.make_job(13001)

        response = self.submit("13001, 13001; 13001")

        self.assertEqual(JobMovement.objects.filter(job=job).count(), 1)
        self.assertIn(
            "1 job assigned. 2 repeated barcodes ignored.",
            self.response_messages(response),
        )

    def test_multiple_repeated_barcodes_count_every_additional_entry(self):
        first = self.make_job(14001)
        second = self.make_job(14002)

        response = self.submit("14001\t14001\n14002,14002")

        self.assertEqual(JobMovement.objects.filter(job__in=[first, second]).count(), 2)
        self.assertIn(
            "2 jobs assigned. 2 repeated barcodes ignored.",
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
            "1 job assigned. 1 repeated barcode ignored.",
            self.response_messages(response),
        )

    def test_assigned_count_only_includes_jobs_that_changed_assignment(self):
        already_assigned = self.make_job(17001, assigned_to=self.target)
        newly_assigned = self.make_job(17002)

        response = self.submit("17001\n17002\n17002")

        self.assertEqual(JobMovement.objects.filter(job=already_assigned).count(), 0)
        self.assertEqual(JobMovement.objects.filter(job=newly_assigned).count(), 1)
        self.assertIn(
            "1 job assigned. 1 repeated barcode ignored.",
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
        self.assertContains(response, "18001\n99999\n99999")
        self.assertEqual(response.context["selected_employee_id"], str(self.target.pk))


class WorkBatchTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="Batch Department")
        self.role = Role.objects.create(name="Batch Worker", level=10)
        self.user = User.objects.create_user(username="batch-worker", password="test")
        self.employee = Employee.objects.create(
            user=self.user,
            department=self.department,
            role=self.role,
            clocked_in=True,
            can_start_batch=True,
            must_change_password=False,
        )
        other_user = User.objects.create_user(username="batch-other", password="test")
        self.other_employee = Employee.objects.create(
            user=other_user,
            department=self.department,
            role=self.role,
            clocked_in=True,
            can_start_batch=True,
            must_change_password=False,
        )
        self.customer = Customer.objects.create(
            name="Batch Customer",
            address="1 Batch Way",
            email="batch@example.com",
            phone="555-0199",
        )
        self.style = Style.objects.create(name="BATCH-STYLE", customer=self.customer)
        self.step = ActivityStep.objects.create(name="Batch Setting", code="batch-setting")
        self.step.departments.add(self.department)
        self.client.force_login(self.user)

    def make_job(self, barcode, **overrides):
        values = {
            "name": "Batch Job",
            "barcode": barcode,
            "stock_num": f"BATCH-{barcode}",
            "style": self.style,
            "due": timezone.localdate() + timedelta(days=7),
            "assigned_to": self.employee,
            "holder": self.employee,
        }
        values.update(overrides)
        return Job.objects.create(**values)

    def start_batch(self, count=2, started_at=None):
        jobs = [self.make_job(20000 + index) for index in range(count)]
        batch = start_work_batch(
            employee=self.employee,
            jobs=jobs,
            step=self.step,
            started_at=started_at,
        )
        return batch, jobs

    def test_permission_defaults_false(self):
        user = User.objects.create_user(username="no-batch")
        employee = Employee.objects.create(user=user)
        self.assertFalse(employee.can_start_batch)

    def test_only_one_active_batch_per_employee(self):
        WorkBatch.objects.create(employee=self.employee, step=self.step)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkBatch.objects.create(employee=self.employee, step=self.step)

    def test_batch_form_parses_supported_delimiters_and_ignores_blanks(self):
        form = BatchStartForm(
            {"step": self.step.pk, "barcodes": " 1\n\n2, 3;\t4 "},
            employee=self.employee,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["barcodes"], ["1", "2", "3", "4"])

    def test_batch_form_ignores_duplicates_and_rejects_fewer_than_two_jobs(self):
        duplicate_form = BatchStartForm(
            {"step": self.step.pk, "barcodes": "1\n1\n2"},
            employee=self.employee,
        )
        single_form = BatchStartForm(
            {"step": self.step.pk, "barcodes": "1"},
            employee=self.employee,
        )
        self.assertTrue(duplicate_form.is_valid(), duplicate_form.errors)
        self.assertEqual(duplicate_form.cleaned_data["barcodes"], ["1", "2"])
        self.assertEqual(duplicate_form.parsed_barcode_input.duplicate_count, 1)
        self.assertFalse(single_form.is_valid())

    def test_start_creates_shared_batch_activities_without_moving_jobs(self):
        started_at = timezone.now() - timedelta(minutes=3)
        batch, jobs = self.start_batch(3, started_at)
        activities = list(batch.activities.order_by("pk"))

        self.assertEqual(len(activities), 3)
        self.assertEqual({activity.start for activity in activities}, {started_at})
        self.assertEqual({activity.employee_id for activity in activities}, {self.employee.pk})
        self.assertEqual({activity.step_id for activity in activities}, {self.step.pk})
        self.assertEqual({activity.batch_id for activity in activities}, {batch.pk})
        for job in jobs:
            job.refresh_from_db()
            self.assertEqual(job.assigned_to, self.employee)
            self.assertEqual(job.holder, self.employee)
            self.assertTrue(job.in_work)

    def test_validation_rejects_ineligible_job_and_is_all_or_nothing(self):
        valid = self.make_job(21001)
        shipped = self.make_job(21002, shipped=True)
        errors = validate_batch_jobs(
            employee=self.employee,
            jobs=[valid, shipped],
            step=self.step,
        )
        self.assertTrue(any("shipped" in error for error in errors))
        with self.assertRaises(ValidationError):
            start_work_batch(
                employee=self.employee,
                jobs=[valid, shipped],
                step=self.step,
            )
        self.assertFalse(WorkBatch.objects.exists())
        self.assertFalse(Activity.objects.exists())

    def test_validation_uses_open_lines_not_stale_piecework_flag(self):
        wrong_holder = self.make_job(22001, holder=self.other_employee)
        stale_piecework_flag = self.make_job(22002, is_piecework=True)
        active_job = self.make_job(22003)
        Activity.objects.create(
            job=active_job,
            employee=self.other_employee,
            step=self.step,
        )
        errors = validate_batch_jobs(
            employee=self.employee,
            jobs=[wrong_holder, stale_piecework_flag, active_job],
            step=self.step,
        )
        self.assertTrue(any("received" in error for error in errors))
        self.assertFalse(any("piecework" in error for error in errors))
        self.assertTrue(any("active work" in error for error in errors))

    def test_employee_with_individual_work_cannot_start_batch(self):
        jobs = [self.make_job(23001), self.make_job(23002)]
        individual_job = self.make_job(23003)
        Activity.objects.create(
            job=individual_job,
            employee=self.employee,
            step=self.step,
        )
        with self.assertRaises(ValidationError):
            start_work_batch(employee=self.employee, jobs=jobs, step=self.step)

    def test_stop_allocates_elapsed_microseconds_exactly_and_stably(self):
        started_at = timezone.now() - timedelta(seconds=10, microseconds=1)
        batch, jobs = self.start_batch(3, started_at)
        stopped_at = started_at + timedelta(seconds=10, microseconds=1)

        stop_work_batch(batch=batch, stopped_at=stopped_at)

        batch.refresh_from_db()
        activities = list(batch.activities.order_by("pk"))
        self.assertFalse(batch.active)
        self.assertEqual(batch.stopped_at, stopped_at)
        self.assertEqual({activity.end for activity in activities}, {stopped_at})
        self.assertFalse(any(activity.active for activity in activities))
        self.assertEqual(
            sum((activity.duration for activity in activities), timedelta()),
            stopped_at - started_at,
        )
        self.assertGreaterEqual(activities[0].duration, activities[-1].duration)
        for job in jobs:
            job.refresh_from_db()
            self.assertFalse(job.in_work)

    def test_ten_minutes_across_ten_jobs_allocates_one_minute_each(self):
        started_at = timezone.now() - timedelta(minutes=10)
        batch, _ = self.start_batch(10, started_at)
        stop_work_batch(batch=batch, stopped_at=started_at + timedelta(minutes=10))
        self.assertEqual(
            {activity.duration for activity in batch.activities.all()},
            {timedelta(minutes=1)},
        )

    def test_second_stop_is_idempotent_and_negative_stop_is_rejected(self):
        started_at = timezone.now()
        batch, _ = self.start_batch(2, started_at)
        with self.assertRaises(ValidationError):
            stop_work_batch(batch=batch, stopped_at=started_at - timedelta(seconds=1))
        batch.refresh_from_db()
        self.assertTrue(batch.active)

        stopped_at = started_at + timedelta(seconds=5)
        stop_work_batch(batch=batch, stopped_at=stopped_at)
        original = list(batch.activities.values_list("duration", flat=True))
        stop_work_batch(batch=batch, stopped_at=stopped_at + timedelta(seconds=5))
        self.assertEqual(original, list(batch.activities.values_list("duration", flat=True)))

    def test_access_and_home_tile_follow_permission(self):
        batch_page = self.client.get(reverse("culet:batch_start"))
        self.assertEqual(batch_page.status_code, 200)
        self.assertContains(batch_page, 'name="barcodes"', count=1)
        self.assertContains(batch_page, 'data-target="id_barcodes"')
        self.assertContains(batch_page, 'data-append-mode="true"')
        self.assertContains(batch_page, 'data-scanner-mode="append-lines"')
        self.assertContains(batch_page, 'data-auto-submit="false"')
        self.assertContains(self.client.get(reverse("culet:home")), "Batch Start")

        self.employee.can_start_batch = False
        self.employee.save(update_fields=["can_start_batch"])
        self.assertEqual(self.client.get(reverse("culet:batch_start")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("culet:home")), "Batch Start")

    def test_review_confirm_and_my_jobs_group(self):
        first = self.make_job(24001)
        second = self.make_job(24002)
        payload = {
            "step": self.step.pk,
            "barcodes": "24001\n24002\n24001",
        }
        review = self.client.post(reverse("culet:batch_start"), payload)
        self.assertContains(review, "Review 2 Jobs")
        self.assertFalse(WorkBatch.objects.exists())

        payload["action"] = "confirm"
        response = self.client.post(reverse("culet:batch_start"), payload)
        self.assertRedirects(response, reverse("culet:my_jobs"))
        self.assertIn(
            "2 jobs added to the batch. 1 repeated barcode was ignored.",
            [str(message) for message in get_messages(response.wsgi_request)],
        )
        page = self.client.get(reverse("culet:my_jobs"))
        self.assertContains(page, "Batch Work")
        self.assertContains(page, "2 jobs")
        self.assertContains(page, first.stock_num)
        self.assertContains(page, second.stock_num)
        self.assertContains(page, "Stop Batch", count=2)

        poll = self.client.get(reverse("culet:my_jobs_poll"))
        self.assertEqual(poll.context["my_jobs_total_count"], 2)
        self.assertEqual(len(poll.context["latest_job_list"]), 0)
        self.assertEqual(
            len(poll.context["active_work_batch"].open_activities),
            2,
        )
        self.assertContains(poll, first.stock_num)
        self.assertContains(poll, second.stock_num)

    def test_individual_start_and_stop_are_blocked_for_active_batch(self):
        batch, jobs = self.start_batch(2)
        extra_job = self.make_job(25003)
        response = self.client.get(reverse("culet:job_start", args=[extra_job.pk]))
        self.assertRedirects(response, reverse("culet:my_jobs"))

        activity = batch.activities.get(job=jobs[0])
        response = self.client.post(
            reverse("culet:stop_work", args=[activity.pk, jobs[0].pk]),
        )
        self.assertRedirects(response, reverse("culet:my_jobs"))
        batch.refresh_from_db()
        activity.refresh_from_db()
        self.assertTrue(batch.active)
        self.assertTrue(activity.active)

    def test_stop_view_requires_owner_and_post(self):
        batch, _ = self.start_batch(2)
        stop_url = reverse("culet:stop_work_batch", args=[batch.pk])
        self.assertEqual(self.client.get(stop_url).status_code, 405)

        self.client.force_login(self.other_employee.user)
        self.assertEqual(self.client.post(stop_url).status_code, 404)

        self.client.force_login(self.user)
        response = self.client.post(stop_url)
        self.assertRedirects(response, reverse("culet:my_jobs"))
        batch.refresh_from_db()
        self.assertFalse(batch.active)

    def test_clock_out_stops_batch_with_allocated_total(self):
        started_at = timezone.now() - timedelta(minutes=2)
        batch, _ = self.start_batch(2, started_at)
        result = clock_out_employee(self.employee)
        batch.refresh_from_db()
        self.assertFalse(batch.active)
        self.assertEqual(
            sum((duration for duration in batch.activities.values_list("duration", flat=True)), timedelta()),
            batch.stopped_at - batch.started_at,
        )
        self.assertEqual(result.stopped_job_count, 2)

    def test_logout_stops_batch_and_allocates_duration(self):
        started_at = timezone.now() - timedelta(minutes=1)
        batch, _ = self.start_batch(2, started_at)

        response = self.client.post(reverse("culet:culet_logout"))

        self.assertRedirects(response, reverse("login"))
        batch.refresh_from_db()
        self.assertFalse(batch.active)
        self.assertEqual(
            sum(
                batch.activities.values_list("duration", flat=True),
                timedelta(),
            ),
            batch.stopped_at - batch.started_at,
        )

    def test_clock_out_also_stops_remaining_non_batch_activity(self):
        batch, _ = self.start_batch(2, timezone.now() - timedelta(minutes=1))
        individual_job = self.make_job(26001)
        individual = Activity.objects.create(
            job=individual_job,
            employee=self.employee,
            step=self.step,
            start=timezone.now() - timedelta(seconds=30),
        )

        result = clock_out_employee(self.employee)

        batch.refresh_from_db()
        individual.refresh_from_db()
        self.assertFalse(batch.active)
        self.assertFalse(individual.active)
        self.assertIsNotNone(individual.end)
        self.assertEqual(result.stopped_job_count, 3)


class SharedBarcodeParserTests(TestCase):
    def test_supported_delimiters_whitespace_and_empty_values(self):
        parsed = parse_barcode_input(" 100\n\n200, 300\t400; 500 ")
        self.assertEqual(parsed.values, ["100", "200", "300", "400", "500"])
        self.assertEqual(parsed.duplicate_count, 0)
        self.assertEqual(parsed.duplicate_values, [])

    def test_duplicates_are_counted_and_first_seen_order_is_preserved(self):
        parsed = parse_barcode_input("200\n100\n200\n100\n200\n300")
        self.assertEqual(parsed.values, ["200", "100", "300"])
        self.assertEqual(parsed.duplicate_values, ["200", "100"])
        self.assertEqual(parsed.duplicate_count, 3)

    def test_single_and_empty_input(self):
        self.assertEqual(parse_barcode_input("123").values, ["123"])
        self.assertEqual(parse_barcode_input(" \n\t,;").values, [])


class ReturnJobsTextareaTests(TestCase):
    def setUp(self):
        department = Department.objects.create(name="Returns")
        role = Role.objects.create(name="Return Worker", level=10)
        self.user = User.objects.create_user(username="returner", password="test")
        self.employee = Employee.objects.create(
            user=self.user,
            department=department,
            role=role,
            must_change_password=False,
        )
        manager_user = User.objects.create_user(username="return-manager")
        self.manager = Employee.objects.create(
            user=manager_user,
            department=department,
            role=role,
            can_receive_returned_jobs=True,
        )
        customer = Customer.objects.create(
            name="Return Customer",
            address="1 Return Way",
            email="return@example.com",
            phone="555-0188",
        )
        self.style = Style.objects.create(name="RETURN-STYLE", customer=customer)
        self.movement_type = MovementType.objects.create(
            name="Returned to Manager",
            code="returned-to-manager",
            job_field=MovementType.JobField.HOLDER,
        )
        self.url = reverse("culet:return_job")
        self.client.force_login(self.user)

    def make_job(self, barcode):
        return Job.objects.create(
            name="Return Job",
            barcode=barcode,
            stock_num=f"RETURN-{barcode}",
            style=self.style,
            due=timezone.localdate() + timedelta(days=5),
            assigned_to=self.employee,
            holder=self.employee,
        )

    @staticmethod
    def response_messages(response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_page_has_one_textarea_and_shared_append_scanner(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="barcodes"', count=1)
        self.assertContains(response, 'type="button"', count=2)
        self.assertContains(response, 'data-target="id_return_barcodes"')
        self.assertContains(response, 'data-append-mode="true"')
        self.assertContains(response, 'data-scanner-mode="append-lines"')
        self.assertContains(response, 'data-auto-submit="false"')
        self.assertNotContains(response, "form-TOTAL_FORMS")
        self.assertNotContains(response, "add-return-job-line")

    def test_multiline_submission_returns_each_job_once(self):
        first = self.make_job(31001)
        second = self.make_job(31002)

        response = self.client.post(
            self.url,
            {"barcodes": "31001\n31002", "employee": self.manager.pk},
            follow=True,
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.holder, self.manager)
        self.assertEqual(second.holder, self.manager)
        self.assertEqual(JobMovement.objects.count(), 2)
        self.assertIn(
            f"2 jobs returned to {self.manager}.",
            self.response_messages(response),
        )

    def test_duplicate_submission_moves_once_and_reports_repeat(self):
        job = self.make_job(32001)

        response = self.client.post(
            self.url,
            {"barcodes": "32001\n32001\n32001", "employee": self.manager.pk},
            follow=True,
        )

        self.assertEqual(JobMovement.objects.filter(job=job).count(), 1)
        self.assertIn(
            f"1 job returned to {self.manager}. 2 repeated barcodes were ignored.",
            self.response_messages(response),
        )

    def test_unknown_barcode_preserves_text_and_employee_selection(self):
        response = self.client.post(
            self.url,
            {"barcodes": "99991\n99992", "employee": self.manager.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "99991\n99992")
        self.assertEqual(response.context["selected_employee_id"], str(self.manager.pk))
        self.assertIn(
            "No job was found for the following barcode(s): 99991, 99992",
            self.response_messages(response),
        )

    def test_receiving_employee_is_still_required_and_input_is_preserved(self):
        self.make_job(33001)
        response = self.client.post(self.url, {"barcodes": "33001", "employee": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "33001")
        self.assertEqual(JobMovement.objects.count(), 0)
        self.assertIn(
            "Please select the employee receiving these jobs.",
            self.response_messages(response),
        )
