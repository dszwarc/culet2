from datetime import timedelta
from importlib import import_module
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import (
    Activity, ActivityStep, Customer, Department, Employee, Job, JobMovement,
    JobStatus, Location, MovementType, PieceworkMemo, PieceworkMemoLine, Style,
)
from .services import validate_batch_jobs


class CuletTestDataMixin:
    def setUp(self):
        self.manager_user = User.objects.create_user("manager", password="test")
        self.worker_user = User.objects.create_user("worker", password="test")
        self.other_user = User.objects.create_user("other", password="test")
        self.manager = Employee.objects.create(
            user=self.manager_user, must_change_password=False
        )
        self.worker = Employee.objects.create(
            user=self.worker_user, must_change_password=False
        )
        self.other = Employee.objects.create(
            user=self.other_user, must_change_password=False
        )
        self.customer = Customer.objects.create(
            name="Piecework Customer", address="1 Main", email="a@example.com",
            phone="555-0100",
        )
        self.style = Style.objects.create(name="PW-STYLE", customer=self.customer)
        self.office = Location.objects.create(name="Office")
        self.piecework = Location.objects.create(name="Piecework")
        for code, field in (
            ("assigned", "assigned_to"), ("received", "holder"),
            ("returned-to-manager", "assigned_to"), ("returned", "holder"),
        ):
            MovementType.objects.create(name=code, code=code, job_field=field)
        self.client.force_login(self.manager_user)

    def make_job(self, barcode, **kwargs):
        values = {
            "name": f"Job {barcode}", "barcode": barcode,
            "stock_num": f"STK-{barcode}", "style": self.style,
            "customer": self.customer,
            "due": timezone.localdate() + timedelta(days=7),
        }
        values.update(kwargs)
        return Job.objects.create(**values)

    def assign(self, scans, employee=None):
        return self.client.post(reverse("culet:piecework_create"), {
            "assigned_to": (employee or self.worker).pk,
            "due_back": "", "notes": "", "scans": scans,
        })


class AdminChangelistRegressionTests(CuletTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.manager_user.is_staff = True
        self.manager_user.is_superuser = True
        self.manager_user.save(update_fields=["is_staff", "is_superuser"])

    def test_employee_admin_search_and_boolean_filters_return_200(self):
        url = reverse("admin:culet_employee_changelist")
        self.assertEqual(self.client.get(url, {"q": "worker"}).status_code, 200)
        for field in (
            "can_qc", "can_inprocess_repair", "can_receive_returned_jobs",
            "can_start_batch", "clocked_in", "must_change_password",
        ):
            self.assertEqual(self.client.get(url, {field: "1"}).status_code, 200)

    def test_piecework_admin_search_and_filter_return_200(self):
        job = self.make_job(41001)
        self.assign(str(job.barcode))
        url = reverse("admin:culet_pieceworkmemo_changelist")
        self.assertEqual(self.client.get(url, {"q": str(job.barcode)}).status_code, 200)
        self.assertEqual(self.client.get(url, {"assigned_to__id__exact": self.worker.pk}).status_code, 200)

        job_url = reverse("admin:culet_job_changelist")
        self.assertEqual(self.client.get(job_url, {"q": "not-a-number"}).status_code, 200)
        self.assertContains(self.client.get(job_url, {"q": str(job.barcode)}), job.stock_num)

    def test_job_movement_admin_guards_barcode_and_preserves_text_searches(self):
        self.worker_user.first_name = "WorkerSearch"
        self.worker_user.save(update_fields=["first_name"])
        self.manager_user.first_name = "ManagerSearch"
        self.manager_user.save(update_fields=["first_name"])
        job = self.make_job(41002)
        self.assign(str(job.barcode))
        url = reverse("admin:culet_jobmovement_changelist")

        self.assertEqual(self.client.get(url, {"q": "not-a-number"}).status_code, 200)
        numeric_response = self.client.get(url, {"q": str(job.barcode)})
        self.assertTrue(
            all(movement.job_id == job.pk for movement in numeric_response.context["cl"].result_list)
        )
        self.assertEqual(len(numeric_response.context["cl"].result_list), 2)

        for search_term in (
            job.stock_num,
            "assigned",
            "WorkerSearch",
            "ManagerSearch",
        ):
            response = self.client.get(url, {"q": search_term})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["cl"].result_list)
            self.assertTrue(
                all(movement.job_id == job.pk for movement in response.context["cl"].result_list)
            )


class PieceworkWorkflowTests(CuletTestDataMixin, TestCase):
    def test_assignment_is_consistent_and_visible_only_to_memo_employee(self):
        first = self.make_job(42001, assigned_to=self.other, holder=self.other)
        second = self.make_job(42002)
        response = self.assign(f"{first.barcode}\n{second.stock_num}\n{first.barcode}")
        self.assertEqual(response.status_code, 200)
        memo = PieceworkMemo.objects.get()
        self.assertEqual(memo.assigned_to, self.worker)
        self.assertEqual(memo.lines.count(), 2)
        for job in (first, second):
            job.refresh_from_db()
            self.assertTrue(job.is_piecework)
            self.assertEqual(job.assigned_to, self.worker)
            self.assertEqual(job.holder, self.worker)
        self.client.force_login(self.worker_user)
        visible = list(self.client.get(reverse("culet:my_piecework")).context["piecework_job_list"])
        self.assertCountEqual(visible, [first, second])
        self.client.force_login(self.other_user)
        self.assertNotContains(self.client.get(reverse("culet:my_piecework")), first.stock_num)

    def test_my_piecework_uses_open_memo_even_when_job_fields_are_stale(self):
        job = self.make_job(42003, assigned_to=self.other, holder=self.other)
        memo = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.worker,
            from_location=self.office, to_location=self.piecework,
        )
        PieceworkMemoLine.objects.create(memo=memo, job=job)
        self.client.force_login(self.worker_user)
        self.assertContains(self.client.get(reverse("culet:my_piecework")), job.stock_num)

    def test_invalid_job_or_existing_open_memo_rolls_back_everything(self):
        valid = self.make_job(42004)
        invalid = self.make_job(42005, shipped=True)
        self.assign(f"{valid.barcode}\n{invalid.barcode}")
        self.assertEqual(PieceworkMemo.objects.count(), 0)
        valid.refresh_from_db()
        self.assertFalse(valid.is_piecework)

        open_memo = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.other,
            from_location=self.office, to_location=self.piecework,
        )
        PieceworkMemoLine.objects.create(memo=open_memo, job=valid)
        self.assign(str(valid.barcode))
        self.assertEqual(PieceworkMemo.objects.count(), 1)

    def test_exception_rolls_back_memo_lines_jobs_and_movements(self):
        job = self.make_job(42006)
        with patch("culet.views.move_job", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.assign(str(job.barcode))
        self.assertFalse(PieceworkMemo.objects.exists())
        job.refresh_from_db()
        self.assertFalse(job.is_piecework)
        self.assertFalse(job.movements.exists())

    def test_same_job_cannot_have_duplicate_lines_in_one_memo(self):
        job = self.make_job(42007)
        memo = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.worker,
            from_location=self.office, to_location=self.piecework,
        )
        PieceworkMemoLine.objects.create(memo=memo, job=job)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PieceworkMemoLine.objects.create(memo=memo, job=job)

    def test_return_is_consistent_and_idempotent(self):
        ActivityStep.objects.create(name="Piecework", code="piecework")
        job = self.make_job(42008)
        self.assign(str(job.barcode))
        memo = PieceworkMemo.objects.get()
        line = memo.lines.get()
        url = reverse("culet:piecework_return", args=[memo.pk])
        self.client.post(url, {"line_ids": [line.pk]})
        self.client.post(url, {"line_ids": [line.pk]})
        memo.refresh_from_db()
        line.refresh_from_db()
        job.refresh_from_db()
        self.assertIsNotNone(memo.returned_at)
        self.assertEqual(memo.returned_by, self.manager)
        self.assertIsNotNone(line.returned_at)
        self.assertEqual(line.returned_by, self.manager)
        self.assertFalse(job.is_piecework)
        self.assertEqual(job.assigned_to, self.manager)
        self.assertEqual(job.holder, self.manager)
        self.assertEqual(Activity.objects.filter(job=job, is_piecework=True).count(), 1)


class PieceworkLineReturnWorkflowTests(CuletTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.piecework_step = ActivityStep.objects.create(
            name="Piecework",
            code="piecework",
        )

    def make_memo_with_jobs(self, *barcodes):
        memo = PieceworkMemo.objects.create(
            created_by=self.manager,
            assigned_to=self.worker,
            from_location=self.office,
            to_location=self.piecework,
            due_back=timezone.localdate() + timedelta(days=3),
        )
        lines = []
        for barcode in barcodes:
            job = self.make_job(
                barcode,
                assigned_to=self.worker,
                holder=self.worker,
                is_piecework=True,
                piecework_assigned_at=memo.created_at,
            )
            lines.append(PieceworkMemoLine.objects.create(memo=memo, job=job))
        return memo, lines

    def post_lines(self, memo, *lines, follow=False):
        return self.client.post(
            reverse("culet:piecework_return", args=[memo.pk]),
            {"line_ids": [line.pk for line in lines]},
            follow=follow,
        )

    @staticmethod
    def response_messages(response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_partial_then_final_return_updates_lines_jobs_memo_and_activity(self):
        memo, (first, second) = self.make_memo_with_jobs(42601, 42602)

        partial_response = self.post_lines(memo, first, follow=True)
        memo.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        first.job.refresh_from_db()
        second.job.refresh_from_db()

        self.assertIsNotNone(first.returned_at)
        self.assertEqual(first.returned_by, self.manager)
        self.assertIsNone(second.returned_at)
        self.assertIsNone(memo.returned_at)
        self.assertIsNone(memo.returned_by)
        self.assertFalse(first.job.is_piecework)
        self.assertTrue(second.job.is_piecework)
        self.assertEqual(first.job.assigned_to, self.manager)
        self.assertEqual(first.job.holder, self.manager)
        self.assertEqual(second.job.assigned_to, self.worker)
        self.assertEqual(second.job.holder, self.worker)
        self.assertIn(
            f"1 job returned from {memo.memo_num}. 1 job remains open.",
            self.response_messages(partial_response),
        )

        activity = Activity.objects.get(job=first.job, is_piecework=True)
        self.assertEqual(activity.employee, self.worker)
        self.assertEqual(activity.step, self.piecework_step)
        self.assertEqual(activity.start, memo.created_at)
        self.assertEqual(activity.end, first.returned_at)
        self.assertFalse(activity.active)
        self.assertEqual(
            set(first.job.movements.values_list("movement_type__code", flat=True)),
            {"returned-to-manager", "returned"},
        )

        self.client.force_login(self.worker_user)
        my_piecework = self.client.get(reverse("culet:my_piecework"))
        self.assertNotContains(my_piecework, first.job.stock_num)
        self.assertContains(my_piecework, second.job.stock_num)

        self.client.force_login(self.manager_user)
        open_piecework = self.client.get(reverse("culet:piecework_open"))
        self.assertContains(open_piecework, memo.memo_num)
        self.assertContains(open_piecework, "1 of")
        self.assertContains(open_piecework, "2 returned")
        self.assertNotContains(open_piecework, first.job.stock_num)
        self.assertContains(open_piecework, second.job.stock_num)

        complete_response = self.post_lines(memo, second, follow=True)
        memo.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(memo.returned_at)
        self.assertEqual(memo.returned_by, self.manager)
        self.assertEqual(memo.returned_at, second.returned_at)
        self.assertIn(
            f"1 job returned. Piecework memo {memo.memo_num} is now complete.",
            self.response_messages(complete_response),
        )
        self.assertEqual(Activity.objects.filter(is_piecework=True).count(), 2)
        self.assertEqual(
            Activity.objects.get(job=second.job, is_piecework=True).end,
            second.returned_at,
        )

    def test_return_page_shows_open_checkboxes_and_returned_audit(self):
        memo, (returned_line, open_line) = self.make_memo_with_jobs(42603, 42604)
        returned_line.returned_at = timezone.now()
        returned_line.returned_by = self.manager
        returned_line.save(update_fields=["returned_at", "returned_by"])

        response = self.client.get(reverse("culet:piecework_return", args=[memo.pk]))
        self.assertContains(response, f'value="{open_line.pk}"')
        self.assertNotContains(response, f'value="{returned_line.pk}"')
        self.assertContains(response, "Out for piecework")
        self.assertContains(response, "Returned")
        self.assertContains(response, str(self.manager))
        self.assertContains(response, "Return Selected Jobs")
        self.assertContains(response, "select-all-open-lines")

    def test_empty_selection_is_rejected_without_changes(self):
        memo, (line,) = self.make_memo_with_jobs(42605)
        response = self.client.post(
            reverse("culet:piecework_return", args=[memo.pk]),
            {},
            follow=True,
        )
        line.refresh_from_db()
        self.assertIsNone(line.returned_at)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())
        self.assertIn("Select at least one job to return.", self.response_messages(response))

    def test_line_from_another_memo_is_rejected(self):
        memo, (valid_line,) = self.make_memo_with_jobs(42606)
        other_memo, (other_line,) = self.make_memo_with_jobs(42607)
        response = self.post_lines(memo, other_line, follow=True)
        valid_line.refresh_from_db()
        other_line.refresh_from_db()
        self.assertIsNone(valid_line.returned_at)
        self.assertIsNone(other_line.returned_at)
        self.assertIn("do not belong to this memo", " ".join(self.response_messages(response)))

    def test_mixed_open_and_stale_selection_rolls_back_and_retry_is_safe(self):
        memo, (stale_line, open_line) = self.make_memo_with_jobs(42608, 42609)
        self.post_lines(memo, stale_line)
        activity_count = Activity.objects.count()
        movement_count = JobMovement.objects.count()

        response = self.post_lines(memo, stale_line, open_line, follow=True)
        open_line.refresh_from_db()
        self.assertIsNone(open_line.returned_at)
        self.assertTrue(open_line.job.is_piecework)
        self.assertEqual(Activity.objects.count(), activity_count)
        self.assertEqual(JobMovement.objects.count(), movement_count)
        self.assertIn("already been returned", " ".join(self.response_messages(response)))

        self.post_lines(memo, stale_line)
        self.assertEqual(Activity.objects.count(), activity_count)
        self.assertEqual(JobMovement.objects.count(), movement_count)

    def test_completed_memo_cannot_process_more_returns(self):
        memo, (line,) = self.make_memo_with_jobs(42610)
        self.post_lines(memo, line)
        response = self.post_lines(memo, line, follow=True)
        self.assertIn("already complete", " ".join(self.response_messages(response)))
        self.assertEqual(Activity.objects.filter(job=line.job).count(), 1)
        self.assertEqual(JobMovement.objects.filter(job=line.job).count(), 2)

    def test_shipped_job_blocks_entire_submission(self):
        memo, (shipped_line, valid_line) = self.make_memo_with_jobs(42611, 42612)
        shipped_line.job.shipped = True
        shipped_line.job.save(update_fields=["shipped", "last_updated"])
        response = self.post_lines(memo, shipped_line, valid_line, follow=True)
        shipped_line.refresh_from_db()
        valid_line.refresh_from_db()
        self.assertIsNone(shipped_line.returned_at)
        self.assertIsNone(valid_line.returned_at)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())
        self.assertIn("has been shipped", " ".join(self.response_messages(response)))

    def test_active_unrelated_activity_blocks_entire_submission(self):
        memo, (busy_line, valid_line) = self.make_memo_with_jobs(42613, 42614)
        other_step = ActivityStep.objects.create(name="Polish", code="polish")
        Activity.objects.create(
            name="Polish",
            step=other_step,
            employee=self.other,
            job=busy_line.job,
            active=True,
        )
        response = self.post_lines(memo, busy_line, valid_line, follow=True)
        valid_line.refresh_from_db()
        self.assertIsNone(valid_line.returned_at)
        self.assertFalse(Activity.objects.filter(is_piecework=True).exists())
        self.assertFalse(JobMovement.objects.exists())
        self.assertIn("has active work", " ".join(self.response_messages(response)))

    def test_inactive_job_can_be_reconciled(self):
        memo, (line,) = self.make_memo_with_jobs(42615)
        line.job.active = False
        line.job.save(update_fields=["active", "last_updated"])
        self.post_lines(memo, line)
        line.refresh_from_db()
        self.assertIsNotNone(line.returned_at)

    def test_open_filters_and_my_piecework_sorting_use_open_lines(self):
        first_memo, (first,) = self.make_memo_with_jobs(42616)
        second_memo, (second,) = self.make_memo_with_jobs(42617)
        third_memo, (third,) = self.make_memo_with_jobs(42618)
        second_memo.due_back = timezone.localdate() + timedelta(days=1)
        second_memo.save(update_fields=["due_back"])
        third_memo.due_back = timezone.localdate() + timedelta(days=5)
        third_memo.save(update_fields=["due_back"])
        self.post_lines(first_memo, first)

        open_url = reverse("culet:piecework_open")
        returned_filter_response = self.client.get(
            open_url,
            {"stock_num": first.job.stock_num},
        )
        self.assertEqual(
            list(returned_filter_response.context["piecework_lines"]),
            [],
        )
        open_filter_response = self.client.get(
            open_url,
            {"stock_num": second.job.stock_num},
        )
        self.assertEqual(
            list(open_filter_response.context["piecework_lines"]),
            [second],
        )
        self.assertContains(
            self.client.get(open_url, {"memo_num": second_memo.memo_num}),
            second.job.stock_num,
        )
        self.assertContains(
            self.client.get(open_url, {"assigned_to": self.worker.pk}),
            second.job.stock_num,
        )
        self.assertContains(
            self.client.get(open_url, {"customer": self.customer.pk}),
            second.job.stock_num,
        )
        due_filter_response = self.client.get(
            open_url,
            {"due_back": (timezone.localdate() + timedelta(days=2)).isoformat()},
        )
        self.assertEqual(
            list(due_filter_response.context["piecework_lines"]),
            [second],
        )

        self.client.force_login(self.worker_user)
        jobs = list(
            self.client.get(reverse("culet:my_piecework")).context["piecework_job_list"]
        )
        self.assertEqual(jobs, [second.job, third.job])

    def test_open_piecework_query_count_does_not_scale_with_lines(self):
        self.make_memo_with_jobs(42620)
        open_url = reverse("culet:piecework_open")
        with CaptureQueriesContext(connection) as baseline_queries:
            self.client.get(open_url)

        for offset in range(1, 9):
            self.make_memo_with_jobs(42620 + offset)

        with CaptureQueriesContext(connection) as populated_queries:
            response = self.client.get(open_url)

        self.assertEqual(response.context["page_obj"].paginator.count, 9)
        self.assertLessEqual(len(populated_queries), len(baseline_queries) + 1)


class PieceworkLifecycleHardeningTests(CuletTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.department = Department.objects.create(name="Hardening")
        self.worker.department = self.department
        self.worker.clocked_in = True
        self.worker.save(update_fields=["department", "clocked_in"])
        self.work_step = ActivityStep.objects.create(
            name="Hardening Work",
            code="hardening-work",
        )
        self.work_step.departments.add(self.department)

    def make_open_line(self, barcode, *, is_piecework=False):
        memo = PieceworkMemo.objects.create(
            created_by=self.manager,
            assigned_to=self.worker,
            from_location=self.office,
            to_location=self.piecework,
        )
        job = self.make_job(
            barcode,
            assigned_to=self.worker,
            holder=self.worker,
            is_piecework=is_piecework,
        )
        line = PieceworkMemoLine.objects.create(memo=memo, job=job)
        return memo, job, line

    @staticmethod
    def response_messages(response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_shipping_rejects_open_line_when_job_flag_is_false(self):
        _, job, line = self.make_open_line(42701, is_piecework=False)
        JobStatus.objects.create(name="Shipped")
        MovementType.objects.create(
            name="Shipped Unassigned",
            code="shipped-unassigned",
            job_field="assigned_to",
        )
        MovementType.objects.create(
            name="Shipped Released",
            code="shipped-released",
            job_field="holder",
        )

        response = self.client.post(
            reverse("culet:job_ship_bulk"),
            {"barcodes": str(job.barcode), "stock_numbers": "", "notes": ""},
        )

        job.refresh_from_db()
        line.refresh_from_db()
        self.assertFalse(job.shipped)
        self.assertIsNone(line.returned_at)
        self.assertFalse(JobMovement.objects.filter(job=job).exists())
        self.assertIn("still out for piecework", " ".join(self.response_messages(response)))

    def test_normal_start_uses_open_line_not_stale_false_flag(self):
        _, job, line = self.make_open_line(42702, is_piecework=False)
        self.client.force_login(self.worker_user)

        blocked = self.client.get(reverse("culet:job_start", args=[job.pk]), follow=True)
        self.assertIn("assigned as piecework", " ".join(self.response_messages(blocked)))

        line.returned_at = timezone.now()
        line.returned_by = self.manager
        line.save(update_fields=["returned_at", "returned_by"])
        allowed = self.client.get(reverse("culet:job_start", args=[job.pk]))
        self.assertEqual(allowed.status_code, 200)
        self.assertContains(allowed, self.work_step.name)

    def test_batch_start_validation_uses_open_line_not_job_flag(self):
        _, piecework_job, _ = self.make_open_line(42711, is_piecework=False)
        normal_job = self.make_job(
            42712,
            assigned_to=self.worker,
            holder=self.worker,
        )

        errors = validate_batch_jobs(
            employee=self.worker,
            jobs=[piecework_job, normal_job],
            step=self.work_step,
        )

        self.assertTrue(any("piecework" in error for error in errors))

    def test_normal_return_workflow_cannot_reassign_open_piecework_job(self):
        _, job, line = self.make_open_line(42713, is_piecework=False)
        self.manager.can_receive_returned_jobs = True
        self.manager.save(update_fields=["can_receive_returned_jobs"])

        response = self.client.post(
            reverse("culet:return_job"),
            {"barcodes": str(job.barcode), "employee": self.manager.pk},
        )

        job.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(job.assigned_to, self.worker)
        self.assertIsNone(line.returned_at)
        self.assertFalse(JobMovement.objects.filter(job=job).exists())
        self.assertIn("still out for piecework", " ".join(self.response_messages(response)))

    def test_my_jobs_uses_open_line_and_allows_returned_stale_true_job(self):
        _, job, line = self.make_open_line(42703, is_piecework=False)
        self.client.force_login(self.worker_user)
        open_jobs = list(
            self.client.get(reverse("culet:my_jobs")).context["latest_job_list"]
        )
        self.assertNotIn(job, open_jobs)

        line.returned_at = timezone.now()
        line.returned_by = self.manager
        line.save(update_fields=["returned_at", "returned_by"])
        job.is_piecework = True
        job.save(update_fields=["is_piecework", "last_updated"])
        returned_jobs = list(
            self.client.get(reverse("culet:my_jobs")).context["latest_job_list"]
        )
        self.assertIn(job, returned_jobs)

    def test_integrity_command_detects_stale_true_and_false_flags(self):
        _, false_job, _ = self.make_open_line(42704, is_piecework=False)
        true_job = self.make_job(42705, is_piecework=True)
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_piecework_integrity", stdout=output)

        report = output.getvalue()
        self.assertIn(f"job_id={false_job.pk}", report)
        self.assertIn(f"job_id={true_job.pk}", report)
        self.assertIn("open_line_stale_false: 1", report)
        self.assertIn("stale_true_without_line: 1", report)
        self.assertIn("Mode: READ ONLY", report)

    def test_integrity_command_accepts_partial_return(self):
        memo, open_job, open_line = self.make_open_line(42706, is_piecework=True)
        returned_job = self.make_job(42707, is_piecework=False)
        PieceworkMemoLine.objects.create(
            memo=memo,
            job=returned_job,
            returned_at=timezone.now(),
            returned_by=self.manager,
        )
        output = StringIO()

        call_command("audit_piecework_integrity", stdout=output)

        self.assertIn("serious_violations: 0", output.getvalue())
        self.assertTrue(open_job.is_piecework)
        self.assertIsNone(open_line.returned_at)

    def test_integrity_command_detects_fully_returned_unclosed_memo(self):
        memo, job, line = self.make_open_line(42708, is_piecework=False)
        line.returned_at = timezone.now()
        line.returned_by = self.manager
        line.save(update_fields=["returned_at", "returned_by"])
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_piecework_integrity", stdout=output)

        self.assertIn(f"memo_id={memo.pk}", output.getvalue())
        self.assertIn("fully_returned_memo_not_closed: 1", output.getvalue())

    def test_open_piecework_count_is_jobs_and_progress_is_memo_level(self):
        memo, first_job, first_line = self.make_open_line(42709, is_piecework=True)
        second_job = self.make_job(42710, is_piecework=False)
        PieceworkMemoLine.objects.create(
            memo=memo,
            job=second_job,
            returned_at=timezone.now(),
            returned_by=self.manager,
        )

        response = self.client.get(reverse("culet:piecework_open"))

        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        visible_line = list(response.context["piecework_lines"])[0]
        self.assertEqual(visible_line.pk, first_line.pk)
        self.assertEqual(visible_line.memo_total_lines, 2)
        self.assertEqual(visible_line.memo_returned_lines, 1)
        self.assertContains(response, "open piecework")

    def test_open_piecework_uses_shared_filter_panel_contract(self):
        self.make_open_line(42714, is_piecework=True)

        response = self.client.get(
            reverse("culet:piecework_open"),
            {"sort": "stock_num", "direction": "desc"},
        )

        self.assertContains(response, 'data-filter-panel')
        self.assertContains(response, 'class="filter-panel-details"')
        self.assertContains(response, "Filter Piecework Jobs")
        self.assertContains(
            response,
            "Search by memo, employee, stock number, customer, or due back date.",
        )
        for field_name in (
            "memo_num",
            "assigned_to",
            "stock_num",
            "customer",
            "due_back",
        ):
            self.assertContains(response, f'name="{field_name}"')
        self.assertContains(response, 'name="sort" value="stock_num"')
        self.assertContains(response, 'name="direction" value="desc"')
        self.assertContains(
            response,
            f'href="{reverse("culet:piecework_open")}"',
        )
        self.assertContains(response, "Clear Filters")


class PieceworkLineReturnModelTests(CuletTestDataMixin, TestCase):
    def make_memo(self, **kwargs):
        values = {
            "created_by": self.manager,
            "assigned_to": self.worker,
            "from_location": self.office,
            "to_location": self.piecework,
        }
        values.update(kwargs)
        return PieceworkMemo.objects.create(**values)

    def test_backfill_copies_completed_memo_audit_fields_to_lines(self):
        returned_at = timezone.now()
        memo = self.make_memo(
            returned_at=returned_at,
            returned_by=self.manager,
        )
        line = PieceworkMemoLine.objects.create(
            memo=memo,
            job=self.make_job(42501),
        )

        migration = import_module("culet.migrations.0090_piecework_line_returns")
        migration.backfill_and_validate_piecework_lines(apps, None)

        line.refresh_from_db()
        self.assertEqual(line.returned_at, returned_at)
        self.assertEqual(line.returned_by, self.manager)

    def test_backfill_leaves_open_memo_lines_open(self):
        memo = self.make_memo()
        line = PieceworkMemoLine.objects.create(
            memo=memo,
            job=self.make_job(42502),
        )

        migration = import_module("culet.migrations.0090_piecework_line_returns")
        migration.backfill_and_validate_piecework_lines(apps, None)

        line.refresh_from_db()
        self.assertIsNone(line.returned_at)
        self.assertIsNone(line.returned_by)

    def test_job_cannot_have_two_open_lines_on_separate_memos(self):
        job = self.make_job(42503)
        PieceworkMemoLine.objects.create(memo=self.make_memo(), job=job)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PieceworkMemoLine.objects.create(memo=self.make_memo(), job=job)

    def test_returned_job_can_be_placed_on_a_new_memo(self):
        job = self.make_job(42504)
        PieceworkMemoLine.objects.create(
            memo=self.make_memo(),
            job=job,
            returned_at=timezone.now(),
            returned_by=self.manager,
        )

        new_line = PieceworkMemoLine.objects.create(
            memo=self.make_memo(),
            job=job,
        )

        self.assertTrue(new_line.is_open)
        self.assertFalse(new_line.is_returned)
        self.assertEqual(job.current_piecework_memo, new_line.memo)

    def test_memo_and_line_status_helpers(self):
        memo = self.make_memo()
        lines = [
            PieceworkMemoLine.objects.create(
                memo=memo,
                job=self.make_job(42510 + offset),
            )
            for offset in range(3)
        ]

        self.assertEqual(memo.total_line_count, 3)
        self.assertEqual(memo.open_line_count, 3)
        self.assertEqual(memo.returned_line_count, 0)
        self.assertTrue(memo.is_open)
        self.assertFalse(memo.is_partially_returned)
        self.assertFalse(memo.is_returned)
        self.assertTrue(lines[0].is_open)

        lines[0].returned_at = timezone.now()
        lines[0].returned_by = self.manager
        lines[0].save(update_fields=["returned_at", "returned_by"])
        self.assertEqual(memo.open_line_count, 2)
        self.assertEqual(memo.returned_line_count, 1)
        self.assertTrue(memo.is_partially_returned)
        self.assertTrue(lines[0].is_returned)

        PieceworkMemoLine.objects.filter(
            pk__in=[lines[1].pk, lines[2].pk]
        ).update(returned_at=timezone.now(), returned_by=self.manager)
        memo.returned_at = timezone.now()
        memo.returned_by = self.manager
        memo.save(update_fields=["returned_at", "returned_by"])

        self.assertEqual(memo.open_line_count, 0)
        self.assertEqual(memo.returned_line_count, 3)
        self.assertFalse(memo.is_open)
        self.assertFalse(memo.is_partially_returned)
        self.assertTrue(memo.is_returned)


class PieceworkAuditCommandTests(CuletTestDataMixin, TestCase):
    def test_report_and_fix_unambiguous_records(self):
        repairable = self.make_job(43001, assigned_to=self.other, holder=self.other)
        first = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.worker,
            from_location=self.office, to_location=self.piecework,
        )
        PieceworkMemoLine.objects.create(memo=first, job=repairable)

        report = StringIO()
        call_command("audit_piecework_consistency", stdout=report)
        self.assertIn("EMPLOYEE CONFLICT", report.getvalue())
        repairable.refresh_from_db()
        self.assertFalse(repairable.is_piecework)

        fixed = StringIO()
        call_command("audit_piecework_consistency", "--fix", stdout=fixed)
        repairable.refresh_from_db()
        self.assertTrue(repairable.is_piecework)
        self.assertEqual(repairable.assigned_to, self.worker)
        self.assertEqual(repairable.holder, self.worker)
        self.assertIn("multiple_open_memos: 0", fixed.getvalue())
