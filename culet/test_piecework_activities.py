import csv
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .models import Activity, ActivityStep, JobMovement, PieceworkMemo, PieceworkMemoLine
from .services import return_piecework_lines
from .test_piecework_integrity import CuletTestDataMixin


class PieceworkActivityTests(CuletTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.step = ActivityStep.objects.create(name="Piecework", code="piecework")
        self.start = timezone.now() - timedelta(days=3)

    def line(self, barcode=49001, job=None, start=None):
        memo = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.worker, from_location=self.office,
            to_location=self.piecework, created_at=start or self.start,
        )
        return PieceworkMemoLine.objects.create(memo=memo, job=job or self.make_job(barcode))

    def finish(self, line, end=None):
        return_piecework_lines(memo=line.memo, line_ids=[line.pk], returned_by=self.manager,
                               returned_at=end or timezone.now())
        line.refresh_from_db()

    def historical(self, **kwargs):
        line = self.line(**kwargs)
        line.returned_at = line.memo.created_at + timedelta(days=1)
        line.returned_by = self.manager
        line.save()
        return line

    def activity(self, line, **overrides):
        values = dict(job=line.job, employee=self.worker, step=self.step,
                      start=line.memo.created_at, end=line.returned_at, active=False, is_piecework=True)
        values.update(overrides)
        return Activity.objects.create(**values)

    def command(self, *args):
        out = StringIO()
        call_command("backfill_piecework_activities", *args, stdout=out)
        return out.getvalue()

    def test_single_return_links_correct_completed_activity_and_duplicate_is_rejected(self):
        line = self.line()
        self.finish(line)
        activity = line.activity
        self.assertEqual(activity.job, line.job)
        self.assertEqual(activity.employee, self.worker)
        self.assertEqual(activity.step, self.step)
        self.assertEqual(activity.start, self.start)
        self.assertEqual(activity.end, line.returned_at)
        self.assertEqual(activity.duration, activity.end - activity.start)
        self.assertTrue(activity.is_piecework)
        self.assertFalse(activity.active)
        with self.assertRaises(ValidationError):
            self.finish(line)
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(JobMovement.objects.count(), 2)

    def test_partial_separate_and_bulk_returns(self):
        first = self.line()
        second = PieceworkMemoLine.objects.create(memo=first.memo, job=self.make_job(49002))
        third = PieceworkMemoLine.objects.create(memo=first.memo, job=self.make_job(49003))
        time_a = self.start + timedelta(days=1)
        time_b = time_a + timedelta(hours=5)
        self.finish(first, time_a)
        second.refresh_from_db()
        self.assertIsNone(second.activity_id)
        return_piecework_lines(memo=first.memo, line_ids=[second.pk, third.pk],
                               returned_by=self.manager, returned_at=time_b)
        for line in (second, third):
            line.refresh_from_db()
            self.assertEqual(line.activity.end, time_b)
        self.assertEqual(first.activity.end, time_a)
        self.assertEqual(Activity.objects.count(), 3)

    def test_repeated_job_periods(self):
        first = self.line()
        self.finish(first, self.start + timedelta(days=1))
        second = self.line(job=first.job, start=self.start + timedelta(days=2))
        self.finish(second)
        self.assertNotEqual(first.activity_id, second.activity_id)
        self.assertEqual(Activity.objects.filter(job=first.job).count(), 2)

    def test_second_activity_failure_rolls_back_entire_selection(self):
        first = self.line()
        second = PieceworkMemoLine.objects.create(memo=first.memo, job=self.make_job(49002))
        create = Activity.objects.create
        calls = []
        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("activity failure")
            return create(**kwargs)
        old_holder = first.job.holder_id
        with patch("culet.services.Activity.objects.create", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                return_piecework_lines(memo=first.memo, line_ids=[first.pk, second.pk], returned_by=self.manager)
        first.refresh_from_db()
        first.job.refresh_from_db()
        self.assertIsNone(first.returned_at)
        self.assertIsNone(first.activity_id)
        self.assertEqual(first.job.holder_id, old_holder)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())

    def test_dry_run_csv_and_real_run_are_idempotent(self):
        line = self.historical()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"
            self.assertIn("would_create: 1", self.command("--dry-run", "--output", str(path)))
            self.assertFalse(Activity.objects.exists())
            line.refresh_from_db()
            self.assertIsNone(line.activity_id)
            with path.open() as stream:
                self.assertEqual(list(csv.DictReader(stream))[0]["action"], "would_create")
            with self.assertRaises(CommandError):
                self.command("--output", str(path))
            self.assertFalse(Activity.objects.exists())
        self.assertIn("created: 1", self.command())
        self.assertIn("created: 0", self.command())
        line.refresh_from_db()
        self.assertEqual(line.activity.start, self.start)
        self.assertEqual(line.activity.employee, self.worker)
        self.assertEqual(Activity.objects.count(), 1)

    def test_existing_activity_linked_without_duplication(self):
        line = self.historical()
        activity = self.activity(line)
        self.assertIn("existing: 1", self.command("--dry-run"))
        line.refresh_from_db()
        self.assertIsNone(line.activity_id)
        self.command()
        line.refresh_from_db()
        self.assertEqual(line.activity_id, activity.pk)
        self.assertEqual(Activity.objects.count(), 1)

    def test_fuzzy_and_duplicate_matches_are_never_repaired(self):
        line = self.historical()
        self.activity(line, employee=self.other)
        self.assertIn("ambiguous: 1", self.command())
        self.activity(line)
        self.assertIn("duplicate_suspected: 1", self.command())
        line.refresh_from_db()
        self.assertIsNone(line.activity_id)
        self.assertEqual(Activity.objects.count(), 2)

    def test_missing_step_and_negative_period_skipped(self):
        line = self.historical()
        self.step.delete()
        self.assertIn("skipped: 1", self.command())
        self.step = ActivityStep.objects.create(name="Piecework", code="piecework")
        line.returned_at = self.start - timedelta(seconds=1)
        line.save()
        self.assertIn("skipped: 1", self.command())
        self.assertFalse(Activity.objects.exists())

    def test_backfill_allows_distinct_periods_but_skips_overlapping_lines(self):
        first = self.historical()
        second = self.historical(job=first.job, start=self.start + timedelta(days=2))
        self.assertIn("created: 2", self.command())
        self.assertIn("existing: 2", self.command())
        self.historical(job=first.job, start=self.start + timedelta(hours=1))
        self.assertIn("ambiguous: 2", self.command())
        self.assertEqual(Activity.objects.count(), 2)

    def test_backfill_link_failure_rolls_back_activity(self):
        line = self.historical()
        with patch.object(PieceworkMemoLine, "save", side_effect=RuntimeError("link failure")):
            with self.assertRaises(RuntimeError):
                self.command()
        self.assertFalse(Activity.objects.exists())
        line.refresh_from_db()
        self.assertIsNone(line.activity_id)

    def test_negative_future_return_rejected(self):
        line = self.line()
        with self.assertRaises(ValidationError):
            self.finish(line, self.start - timedelta(seconds=1))
        line.refresh_from_db()
        self.assertIsNone(line.returned_at)
        self.assertFalse(Activity.objects.exists())

    def test_returned_activity_reaches_duration_report_and_combined_events(self):
        from django.test import RequestFactory
        from .services import get_job_history
        from .views import StyleStepTimeReportView
        line = self.line()
        self.finish(line)
        view = StyleStepTimeReportView()
        view.setup(RequestFactory().get("/"))
        rows = list(view.get_context_data()["rows"])
        self.assertEqual(rows[0]["activity_count"], 1)
        self.assertEqual(rows[0]["total_duration"], line.activity.duration)
        events = get_job_history(line.job)
        self.assertEqual([event.event_id for event in events if event.event_type == "activity"], [line.activity_id])

    def test_unexplained_disjoint_activity_requires_manual_review(self):
        line = self.historical()
        self.activity(line, start=self.start - timedelta(days=5), end=self.start - timedelta(days=4))
        self.assertIn("ambiguous: 1", self.command())
        self.assertEqual(Activity.objects.count(), 1)
        line.refresh_from_db()
        self.assertIsNone(line.activity_id)
