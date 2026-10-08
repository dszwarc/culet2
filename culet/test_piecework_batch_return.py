from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .models import Activity, ActivityStep, Employee, Job, JobMovement, JobShip, PieceworkMemo, PieceworkMemoLine, Role
from .piecework_batch_return import process_piecework_batch
from .services import return_piecework_lines
from .test_piecework_integrity import CuletTestDataMixin

FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class BatchReturnSetup(CuletTestDataMixin):
    def setUp(self):
        super().setUp()
        self.manager.role = Role.objects.create(name="Batch Manager", level=30)
        self.manager.save(update_fields=["role"])
        self.operation = ActivityStep.objects.create(name="Setting", code="batch-setting")
        self.legacy = ActivityStep.objects.create(name="Piecework", code="piecework")
        self.url = reverse("culet:piecework_batch_return")

    def memo(self, *barcodes, legacy=False, jobs=None):
        memo = PieceworkMemo.objects.create(
            created_by=self.manager, assigned_to=self.worker,
            from_location=self.office, to_location=self.piecework,
            activity_step=None if legacy else self.operation,
        )
        PieceworkMemo.objects.filter(pk=memo.pk).update(created_at=timezone.now() - timedelta(days=2))
        memo.refresh_from_db()
        jobs = jobs or [self.make_job(barcode) for barcode in barcodes]
        lines = []
        for job in jobs:
            job.is_piecework = True
            job.piecework_assigned_at = memo.created_at
            job.assigned_to = self.worker
            job.holder = self.worker
            job.save()
            lines.append(PieceworkMemoLine.objects.create(memo=memo, job=job))
        return memo, lines

    def preview(self, *lines, scans=None):
        return self.client.post(self.url, {
            "scans": scans if scans is not None else "\n".join(str(line.job.barcode) for line in lines),
            "action": "preview",
        })

    def confirm(self, preview, **overrides):
        return self.client.post(self.url, {
            "scans": preview.context["scans"], "confirmation": preview.context["confirmation"],
            "action": "return", **overrides,
        })

    def assert_open(self, *lines):
        for line in lines:
            line.refresh_from_db()
            line.job.refresh_from_db()
            self.assertIsNone(line.returned_at)
            self.assertIsNone(line.returned_by_id)
            self.assertIsNone(line.activity_id)
            self.assertTrue(line.job.is_piecework)
            self.assertEqual(line.job.holder, self.worker)
            self.assertEqual(line.job.assigned_to, self.worker)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class BatchReturnTests(BatchReturnSetup, TestCase):
    def test_preview_is_read_only_then_returns_same_memo_with_correct_history(self):
        memo, lines = self.memo(62001, 62002)
        preview = self.preview(*lines)
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Confirm 2 jobs")
        self.assertContains(preview, memo.memo_num)
        self.assertContains(preview, self.operation.name)
        self.assert_open(*lines)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())
        before = timezone.now()
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 302)
        memo.refresh_from_db()
        self.assertIsNotNone(memo.returned_at)
        self.assertEqual(memo.returned_by, self.manager)
        for line in lines:
            line.refresh_from_db()
            line.job.refresh_from_db()
            self.assertGreaterEqual(line.returned_at, before)
            self.assertEqual(line.returned_at, memo.returned_at)
            self.assertEqual(line.returned_by, self.manager)
            activity = line.activity
            self.assertEqual(activity.employee, self.worker)
            self.assertEqual(activity.step, self.operation)
            self.assertEqual(activity.start, memo.created_at)
            self.assertEqual(activity.end, line.returned_at)
            self.assertEqual(activity.duration, activity.end - activity.start)
            self.assertTrue(activity.is_piecework)
            self.assertFalse(activity.active)
            self.assertEqual(line.job.assigned_to, self.manager)
            self.assertEqual(line.job.holder, self.manager)
            self.assertFalse(line.job.is_piecework)
            self.assertFalse(line.job.in_work)
            self.assertIsNone(line.job.piecework_assigned_at)
            self.assertEqual(set(JobMovement.objects.filter(job=line.job).values_list("movement_type__code", flat=True)),
                             {"returned-to-manager", "returned"})
            self.assertEqual(set(JobMovement.objects.filter(job=line.job).values_list("performed_by_id", flat=True)), {self.manager.pk})
        results = self.client.get(self.url)
        self.assertContains(results, "Returned 2 jobs")
        self.assertContains(results, "Scan another batch")
        self.assertContains(results, memo.memo_num, count=2)
        self.assertContains(results, lines[0].job.stock_num)
        self.assertEqual(len(results.context["returned_rows"]), 2)

    def test_multiple_memos_partial_return_and_legacy_fallback(self):
        first, (a, b) = self.memo(62010, 62011)
        second, (c,) = self.memo(62012, legacy=True)
        self.assertEqual(self.confirm(self.preview(a, c)).status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        a.refresh_from_db()
        c.refresh_from_db()
        self.assertIsNone(first.returned_at)
        self.assertIsNotNone(second.returned_at)
        self.assertEqual(a.activity.step, self.operation)
        self.assertEqual(c.activity.step, self.legacy)
        self.assertEqual(a.returned_at, c.returned_at)
        self.assert_open(b)
        # Existing individual return still completes the remainder.
        response = self.client.post(reverse("culet:piecework_return", args=[first.pk]), {"line_ids": [b.pk]})
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertIsNotNone(first.returned_at)
        self.assertEqual(Activity.objects.filter(is_piecework=True).count(), 3)

    def test_duplicate_and_zero_padded_scans_are_removed_and_blank_lines_ignored(self):
        memo, (line,) = self.memo(62020)
        preview = self.preview(scans="\n62020\n62020\n0062020\n\n")
        self.assertEqual(preview.context["scans"], "62020")
        self.assertEqual(preview.context["duplicate_count"], 2)
        self.assertEqual(len(preview.context["preview_rows"]), 1)
        self.assertEqual(self.confirm(preview).status_code, 302)
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(JobMovement.objects.count(), 2)

    def test_invalid_unknown_and_non_piecework_barcodes_reject_entire_batch(self):
        _, (line,) = self.memo(62030)
        plain = self.make_job(62031)
        for scans, error in (("62030\nSTK-62031", "numeric job barcodes"),
                             ("62030\n999999", "Barcode 999999: no job exists"),
                             ("62030\n62031", "not on a piecework memo")):
            response = self.preview(scans=scans)
            self.assertContains(response, error)
            self.assert_open(line)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())

    def test_inactive_shipped_and_existing_shipment_rechecked_after_preview(self):
        for index, state in enumerate(("inactive", "shipped", "shipment")):
            _, (line,) = self.memo(62040 + index)
            preview = self.preview(line)
            if state == "inactive":
                line.job.active = False
                line.job.save(update_fields=["active"])
            elif state == "shipped":
                line.job.shipped = True
                line.job.save(update_fields=["shipped"])
            else:
                JobShip.objects.create(job=line.job, shipped_by=self.manager)
            response = self.confirm(preview)
            self.assertContains(response, "inactive" if state == "inactive" else "shipped")
            self.assertContains(response, str(line.job.barcode))
            self.assertContains(response, line.job.stock_num)
            self.assert_open(line)
        self.assertFalse(Activity.objects.exists())

    def test_active_work_and_open_line_activity_conflicts_reject_without_writes(self):
        _, (line,) = self.memo(62050)
        preview = self.preview(line)
        activity = Activity.objects.create(job=line.job, employee=self.worker, active=True)
        response = self.confirm(preview)
        self.assertContains(response, "active work")
        self.assert_open(line)
        activity.end = timezone.now()
        activity.active = False
        activity.save()
        PieceworkMemoLine.objects.filter(pk=line.pk).update(activity=activity)
        response = self.preview(line)
        self.assertContains(response, "already has an Activity")
        self.assertEqual(Activity.objects.count(), 1)
        self.assertFalse(JobMovement.objects.exists())

    def test_already_returned_and_duplicate_submission_do_not_duplicate_history(self):
        _, (line,) = self.memo(62060)
        preview = self.preview(line)
        self.assertEqual(self.confirm(preview).status_code, 302)
        response = self.confirm(preview)
        self.assertContains(response, "already returned")
        self.assertContains(self.preview(line), "already returned")
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(JobMovement.objects.count(), 2)

    def test_stale_confirmation_cannot_return_a_new_memo_for_the_same_job(self):
        old_memo, (line,) = self.memo(62070)
        preview = self.preview(line)
        return_piecework_lines(memo=old_memo, line_ids=[line.pk], returned_by=self.manager)
        _, (new_line,) = self.memo(jobs=[line.job])
        response = self.confirm(preview)
        self.assertContains(response, "changed after confirmation")
        self.assert_open(new_line)
        self.assertEqual(Activity.objects.count(), 1)

    def test_changed_operation_or_tampered_confirmation_requires_review(self):
        memo, (line,) = self.memo(62080)
        preview = self.preview(line)
        PieceworkMemo.objects.filter(pk=memo.pk).update(activity_step=None)
        self.assertContains(self.confirm(preview), "changed after confirmation")
        self.assertContains(self.confirm(preview, confirmation="bad"), "expired or is invalid")
        self.assertContains(self.confirm(preview, scans="999999"), "scan list changed")
        self.assertContains(self.client.post(self.url, {"scans": "62080", "action": "return"}), "expired or is invalid")
        self.assert_open(line)

    def test_every_memo_is_validated_before_any_return_and_missing_reference_is_readable(self):
        _, (first,) = self.memo(62090)
        _, (second,) = self.memo(62091, legacy=True)
        self.legacy.delete()
        with patch("culet.piecework_batch_return.return_piecework_lines") as returning:
            response = self.preview(first, second)
        self.assertContains(response, "reference data is incomplete")
        returning.assert_not_called()
        self.assert_open(first, second)
        self.assertFalse(Activity.objects.exists())

    def test_failure_in_second_memo_rolls_back_first_memo_and_all_history(self):
        memo, (first,) = self.memo(62100)
        second_memo, (second,) = self.memo(62101)
        preview = self.preview(first, second)
        original = return_piecework_lines
        def failing_return(**kwargs):
            if kwargs["memo"] == second_memo.pk:
                self.assertTrue(Activity.objects.filter(job=first.job).exists())
                raise ValidationError("Return failed for second memo")
            return original(**kwargs)
        with patch("culet.piecework_batch_return.return_piecework_lines", side_effect=failing_return):
            self.assertContains(self.confirm(preview), "Return failed for second memo")
        self.assert_open(first, second)
        memo.refresh_from_db()
        self.assertIsNone(memo.returned_at)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())

    def test_real_activity_link_uniqueness_failure_is_readable_and_atomic(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL diagnostics required")
        _, (first, second) = self.memo(62110, 62111)
        preview = self.preview(first, second)
        original = PieceworkMemoLine.save
        def collide(line, *args, **kwargs):
            if line.pk == second.pk:
                line.activity_id = PieceworkMemoLine.objects.get(pk=first.pk).activity_id
            return original(line, *args, **kwargs)
        with patch.object(PieceworkMemoLine, "save", collide):
            self.assertContains(self.confirm(preview), "Nothing was returned")
        self.assert_open(first, second)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())

    def test_unrelated_database_errors_are_not_swallowed(self):
        _, (line,) = self.memo(62120)
        preview = self.preview(line)
        with patch("culet.piecework_batch_return.return_piecework_lines", side_effect=IntegrityError("unrelated")):
            with self.assertRaises(IntegrityError):
                self.confirm(preview)
        self.assert_open(line)

    def test_barcode_is_revalidated_after_locking(self):
        _, (line,) = self.memo(62125)
        preview = self.preview(line)
        original_lock = Job.objects.select_for_update
        def change_barcode(*args, **kwargs):
            Job.objects.filter(pk=line.job_id).update(barcode=62126)
            return original_lock(*args, **kwargs)
        with patch("culet.piecework_batch_return.Job.objects.select_for_update", side_effect=change_barcode):
            self.assertContains(self.confirm(preview), "barcode changed during lookup")
        self.assert_open(line)
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(JobMovement.objects.exists())

    def test_null_stock_number_and_stale_piecework_flag_still_use_open_line(self):
        _, (line,) = self.memo(62130)
        line.job.stock_num = None
        line.job.is_piecework = False
        line.job.save(update_fields=["stock_num", "is_piecework"])
        preview = self.preview(line)
        self.assertContains(preview, "Not recorded")
        self.assertEqual(self.confirm(preview).status_code, 302)
        self.assertEqual(Activity.objects.count(), 1)

    def test_permissions_home_tile_and_edit_without_mutating(self):
        _, (line,) = self.memo(62140)
        self.assertContains(self.client.get(reverse("culet:home")), "Batch Return Piecework")
        preview = self.preview(line)
        edit = self.confirm(preview, action="edit")
        self.assertContains(edit, "Review Batch")
        self.assert_open(line)
        self.client.force_login(self.worker_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.preview(line).status_code, 403)
        self.assertNotContains(self.client.get(reverse("culet:home")), "Batch Return Piecework")
        self.client.force_login(User.objects.create_user(username="no-employee"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        head = Employee.objects.create(user=User.objects.create_user(username="head"),
                                       role=Role.objects.create(name="Head", level=10), must_change_password=False)
        self.client.force_login(head.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertContains(self.confirm(preview), "scan list changed")
        self.assert_open(line)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class ConcurrentBatchReturnTests(BatchReturnSetup, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_multi_memo_batches_in_opposite_scan_order_return_once(self):
        _, (first,) = self.memo(62200)
        _, (second,) = self.memo(62201)
        barcodes = [str(first.job.barcode), str(second.job.barcode)]
        preview = process_piecework_batch(barcodes=barcodes, returned_by=self.manager)
        barrier = Barrier(2)
        def attempt(scans):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    process_piecework_batch(barcodes=scans, returned_by=self.manager, confirmed=preview["snapshot"])
                    return "returned"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt, scans) for scans in (barcodes, list(reversed(barcodes)))]
            self.assertCountEqual([future.result(timeout=20) for future in futures], ["returned", "rejected"])
        self.assertEqual(Activity.objects.count(), 2)
        self.assertEqual(JobMovement.objects.count(), 4)
        self.assertEqual(PieceworkMemoLine.objects.filter(returned_at__isnull=False).count(), 2)

    @skipUnlessDBFeature("has_select_for_update")
    def test_batch_and_individual_return_race_does_not_duplicate_activity(self):
        memo, (line,) = self.memo(62210)
        scans = [str(line.job.barcode)]
        preview = process_piecework_batch(barcodes=scans, returned_by=self.manager)
        barrier = Barrier(2)
        def attempt(batch):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    if batch:
                        process_piecework_batch(barcodes=scans, returned_by=self.manager, confirmed=preview["snapshot"])
                    else:
                        return_piecework_lines(memo=memo.pk, line_ids=[line.pk], returned_by=self.manager)
                    return "returned"
                except ValidationError:
                    return "rejected"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt, batch) for batch in (True, False)]
            self.assertCountEqual([future.result(timeout=20) for future in futures], ["returned", "rejected"])
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(JobMovement.objects.count(), 2)
