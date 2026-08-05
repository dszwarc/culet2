from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Activity, ActivityStep, Customer, Employee, Job, Location,
    MovementType, PieceworkMemo, PieceworkMemoLine, Style,
)


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
        url = reverse("culet:piecework_return", args=[memo.pk])
        self.client.post(url)
        self.client.post(url)
        memo.refresh_from_db()
        job.refresh_from_db()
        self.assertIsNotNone(memo.returned_at)
        self.assertEqual(memo.returned_by, self.manager)
        self.assertFalse(job.is_piecework)
        self.assertEqual(job.assigned_to, self.manager)
        self.assertEqual(job.holder, self.manager)
        self.assertEqual(Activity.objects.filter(job=job, is_piecework=True).count(), 1)


class PieceworkAuditCommandTests(CuletTestDataMixin, TestCase):
    def test_report_and_fix_only_unambiguous_records(self):
        repairable = self.make_job(43001, assigned_to=self.other, holder=self.other)
        ambiguous = self.make_job(43002, is_piecework=True)
        first = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.worker,
            from_location=self.office, to_location=self.piecework,
        )
        second = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.other,
            from_location=self.office, to_location=self.piecework,
        )
        PieceworkMemoLine.objects.create(memo=first, job=repairable)
        PieceworkMemoLine.objects.create(memo=first, job=ambiguous)
        PieceworkMemoLine.objects.create(memo=second, job=ambiguous)

        report = StringIO()
        call_command("audit_piecework_consistency", stdout=report)
        self.assertIn("EMPLOYEE CONFLICT", report.getvalue())
        self.assertIn("MANUAL REVIEW", report.getvalue())
        repairable.refresh_from_db()
        self.assertFalse(repairable.is_piecework)

        fixed = StringIO()
        call_command("audit_piecework_consistency", "--fix", stdout=fixed)
        repairable.refresh_from_db()
        ambiguous.refresh_from_db()
        self.assertTrue(repairable.is_piecework)
        self.assertEqual(repairable.assigned_to, self.worker)
        self.assertEqual(repairable.holder, self.worker)
        self.assertNotEqual(ambiguous.assigned_to, self.worker)
        self.assertIn("multiple_open_memos: 1", fixed.getvalue())
