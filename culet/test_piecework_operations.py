from datetime import timedelta
from io import StringIO

from django.contrib import admin
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import PieceworkMemoCreateForm
from .models import Activity, ActivityStep, Department, Job, JobMovement, PieceworkMemo, PieceworkMemoLine
from .services import get_job_progress, return_piecework_lines, with_job_progress_data
from .test_piecework_integrity import CuletTestDataMixin


class PieceworkOperationTests(CuletTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.cleaning = ActivityStep.objects.create(name="Cleaning", code="clean")
        self.legacy = ActivityStep.objects.create(name="Piecework", code="piecework")
        self.repair = ActivityStep.objects.create(name="Repair", code="repair")
        self.cleaning.departments.add(Department.objects.create(name="Jewelry"))

    def payload(self, operation):
        return {"assigned_to": self.worker.pk, "activity_step": operation,
                "scans": "51001\n51002\n51001", "notes": "", "due_back": ""}

    def test_creation_rejects_missing_excluded_and_unknown_operation_without_side_effects(self):
        self.make_job(51001)
        self.make_job(51002)
        for operation in ("", self.legacy.pk, self.repair.pk, 999999):
            with self.subTest(operation=operation):
                response = self.client.post(reverse("culet:piecework_create"), self.payload(operation))
                self.assertIn("activity_step", response.context["memo_form"].errors)
                self.assertFalse(PieceworkMemo.objects.exists())
                self.assertFalse(JobMovement.objects.exists())
        data = self.payload("")
        del data["activity_step"]
        self.assertFalse(PieceworkMemoCreateForm(data).is_valid())

    def test_selector_and_multiple_job_creation_preserve_scan_deduplication(self):
        form = PieceworkMemoCreateForm()
        self.assertTrue(form.fields["activity_step"].required)
        self.assertEqual(list(form.fields["activity_step"].queryset), [self.cleaning])
        first, second = self.make_job(51001), self.make_job(51002)
        self.client.post(reverse("culet:piecework_create"), self.payload(self.cleaning.pk))
        memo = PieceworkMemo.objects.get()
        self.assertEqual(memo.activity_step, self.cleaning)
        self.assertCountEqual(list(memo.lines.values_list("job_id", flat=True)), [first.pk, second.pk])
        self.assertEqual(JobMovement.objects.count(), 4)
        self.client.post(reverse("culet:piecework_create"), self.payload(self.cleaning.pk))
        self.assertEqual(PieceworkMemo.objects.count(), 1)
        self.assertEqual(JobMovement.objects.count(), 4)
        for route, kwargs in (("piecework_open", {}), ("piecework_return", {"pk": memo.pk}),
                              ("piecework_print", {"pk": memo.pk})):
            self.assertContains(self.client.get(reverse("culet:" + route, kwargs=kwargs)), "Cleaning")

    def memo(self, operation):
        return PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.worker,
            from_location=self.office, to_location=self.piecework,
            activity_step=operation, created_at=timezone.now() - timedelta(days=3),
        )

    def test_partial_and_bulk_returns_use_operation_and_preserve_historical_activity(self):
        old_memo = self.memo(None)
        old_line = PieceworkMemoLine.objects.create(memo=old_memo, job=self.make_job(51000))
        return_piecework_lines(memo=old_memo, line_ids=[old_line.pk], returned_by=self.manager)
        historical = list(Activity.objects.values())
        memo = self.memo(self.cleaning)
        lines = [PieceworkMemoLine.objects.create(memo=memo, job=self.make_job(code))
                 for code in (51001, 51002, 51003)]
        for selection in (lines[:1], lines[1:]):
            end = timezone.now()
            response = self.client.post(reverse("culet:piecework_return", kwargs={"pk": memo.pk}),
                                        {"line_ids": [line.pk for line in selection]})
            self.assertEqual(response.status_code, 302)
            for line in selection:
                line.refresh_from_db()
                activity = line.activity
                self.assertEqual(activity.step, self.cleaning)
                self.assertTrue(activity.is_piecework)
                self.assertFalse(activity.active)
                self.assertGreaterEqual(activity.end, end)
                self.assertEqual(activity.start, memo.created_at)
                self.assertEqual(activity.duration, activity.end - activity.start)
                self.assertEqual(activity.employee, self.worker)
                self.assertEqual(line.returned_by, self.manager)
                self.assertEqual(line.returned_at, activity.end)
                line.job.refresh_from_db()
                self.assertEqual(line.job.holder, self.manager)
                self.assertEqual(line.job.assigned_to, self.manager)
        memo.refresh_from_db()
        self.assertIsNotNone(memo.returned_at)
        self.assertEqual(list(Activity.objects.filter(pk=historical[0]["id"]).values()), historical)
        self.assertEqual(get_job_progress(lines[0].job)["completed_steps"], 1)
        prefetched = with_job_progress_data(Job.objects.all()).get(pk=lines[0].job_id)
        self.assertEqual(get_job_progress(prefetched)["completed_steps"], 1)
        self.assertEqual(get_job_progress(old_line.job)["completed_steps"], 0)

    def test_operation_return_does_not_require_legacy_reference_step(self):
        self.legacy.delete()
        memo = self.memo(self.cleaning)
        line = PieceworkMemoLine.objects.create(memo=memo, job=self.make_job(51001))
        return_piecework_lines(memo=memo, line_ids=[line.pk], returned_by=self.manager)
        line.refresh_from_db()
        self.assertEqual(line.activity.step, self.cleaning)

    def test_legacy_return_and_operation_display(self):
        memo = self.memo(None)
        line = PieceworkMemoLine.objects.create(memo=memo, job=self.make_job(51001))
        self.assertContains(self.client.get(reverse("culet:piecework_return", kwargs={"pk": memo.pk})),
                            "Operation not recorded")
        return_piecework_lines(memo=memo, line_ids=[line.pk], returned_by=self.manager)
        line.refresh_from_db()
        self.assertEqual(line.activity.step, self.legacy)
        self.assertTrue(line.activity.is_piecework)
        self.assertFalse(line.activity.active)

    def test_admin_requires_operation_on_add_and_preserves_existing_memo(self):
        request = RequestFactory().get("/")
        request.user = self.manager_user
        model_admin = admin.site._registry[PieceworkMemo]
        form_class = model_admin.get_form(request)
        for operation in ("", self.legacy.pk, self.repair.pk):
            form = form_class(self.payload(operation))
            self.assertIn("activity_step", form.errors)
        self.assertNotIn("activity_step", form_class(self.payload(self.cleaning.pk)).errors)
        legacy = self.memo(None)
        self.assertIn("activity_step", model_admin.get_readonly_fields(request, legacy))

    def test_reconciliation_recognizes_operation_return(self):
        memo = self.memo(self.cleaning)
        line = PieceworkMemoLine.objects.create(memo=memo, job=self.make_job(51001))
        return_piecework_lines(memo=memo, line_ids=[line.pk], returned_by=self.manager)
        before = list(Activity.objects.values())
        out = StringIO()
        call_command("backfill_piecework_activities", "--dry-run", stdout=out)
        self.assertIn("existing: 1", out.getvalue())
        self.assertEqual(list(Activity.objects.values()), before)

    def test_reconciliation_handles_distinct_operations_for_same_job(self):
        first = self.memo(None)
        job = self.make_job(51001)
        line = PieceworkMemoLine.objects.create(memo=first, job=job)
        return_piecework_lines(memo=first, line_ids=[line.pk], returned_by=self.manager,
                               returned_at=first.created_at + timedelta(hours=1))
        second = self.memo(self.cleaning)
        second.created_at = first.created_at + timedelta(days=1)
        second.save(update_fields=["created_at"])
        other = PieceworkMemoLine.objects.create(
            memo=second, job=job, returned_at=second.created_at + timedelta(hours=1),
            returned_by=self.manager,
        )
        out = StringIO()
        call_command("backfill_piecework_activities", stdout=out)
        self.assertIn("existing: 1", out.getvalue())
        self.assertIn("created: 1", out.getvalue())
        other.refresh_from_db()
        self.assertEqual(other.activity.step, self.cleaning)
        out = StringIO()
        call_command("backfill_piecework_activities", "--dry-run", stdout=out)
        self.assertIn("existing: 2", out.getvalue())
