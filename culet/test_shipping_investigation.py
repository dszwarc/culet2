from datetime import date
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .models import JobShip
from .shipping import ship_jobs
from .test_shipping import ShippingSetup


class ShippingAttributionInvestigationTests(ShippingSetup, TestCase):
    def test_status_change_can_mark_shipped_without_shipment(self):
        job = self.make_job(71000)
        response = self.client.post(reverse("culet:change_status"), {
            "barcodes": [str(job.barcode)], "status": self.shipped_status.pk,
        })
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status_id, self.shipped_status.pk)
        self.assertFalse(job.shipped)
        self.assertFalse(JobShip.objects.filter(job=job).exists())

    def test_consecutive_batches_use_each_authenticated_employee(self):
        first = self.make_job(71001, assigned_to=self.worker)
        second = self.make_job(71002, assigned_to=self.other)
        third = self.make_job(71003, holder=self.other)
        self.assertEqual(self.post_ship(first).status_code, 302)
        self.client.force_login(self.worker_user)
        response = self.post_ship(second, barcodes=f"{second.barcode}\n{third.barcode}",
                                  shipped_by=self.other.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(JobShip.objects.get(job=first).shipped_by_id, self.manager.pk)
        for job in (second, third):
            self.assertEqual(JobShip.objects.get(job=job).shipped_by_id, self.worker.pk)

    def test_numeric_collision_resolves_only_selected_identifier_namespace(self):
        barcode_job = self.make_job(1161318, stock_num="DIFFERENT")
        stock_job = self.make_job(71004, stock_num="1161318")
        self.assertEqual(self.post_ship(barcode_job).status_code, 302)
        self.assertTrue(JobShip.objects.filter(job=barcode_job).exists())
        self.assertFalse(JobShip.objects.filter(job=stock_job).exists())
        response = self.post_ship(stock_job, barcodes="", stock_numbers="1161318")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(JobShip.objects.filter(job=stock_job).exists())

    def test_failed_form_preserves_all_identifiers_for_next_submission(self):
        first = self.make_job(71005)
        second = self.make_job(71006)
        scans = f"{first.barcode}\n{second.barcode}\n99999999"
        response = self.post_ship(first, barcodes=scans)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["barcodes"].value(), scans)
        self.assertFalse(JobShip.objects.exists())
        self.assertEqual(self.post_ship(first, barcodes=f"{first.barcode}\n{second.barcode}").status_code, 302)
        self.assertEqual(JobShip.objects.count(), 2)

    def test_employee_without_shipping_role_can_post_directly(self):
        from .permissions import can_ship_jobs

        self.assertFalse(can_ship_jobs(self.worker_user))
        self.client.force_login(self.worker_user)
        job = self.make_job(71007)
        self.assertEqual(self.post_ship(job).status_code, 302)
        self.assertEqual(JobShip.objects.get(job=job).shipped_by_id, self.worker.pk)

    def test_backfill_overwrites_existing_actor_timestamp_and_notes(self):
        job = self.make_job(71008, due=date(2025, 1, 1), shipped=True)
        original_timestamp = timezone.now()
        shipment = JobShip.objects.create(job=job, shipped_by=self.manager,
                                          shipped_at=original_timestamp, notes="Original evidence")
        call_command("backfill_2025_shipments", shipped_by=self.worker_user.username,
                     cutoff="2025-12-31", shipped_date="2025-12-31",
                     close_open_activities=False, dry_run=False, stdout=StringIO())
        shipment.refresh_from_db()
        self.assertEqual(shipment.shipped_by_id, self.worker.pk)
        self.assertNotEqual(shipment.shipped_at, original_timestamp)
        self.assertIn("Historical shipment backfill", shipment.notes)
        self.assertEqual(JobShip.objects.filter(job=job).count(), 1)

    def test_backfill_creates_retrospective_record_without_movement(self):
        job = self.make_job(71009, due=date(2025, 1, 1))
        call_command("backfill_2025_shipments", shipped_by=self.other_user.username,
                     cutoff="2025-12-31", shipped_date="2025-12-31",
                     close_open_activities=False, dry_run=False, stdout=StringIO())
        shipment = JobShip.objects.get(job=job)
        self.assertEqual(shipment.shipped_by_id, self.other.pk)
        self.assertEqual(timezone.localtime(shipment.shipped_at).date(), date(2025, 12, 31))
        self.assertFalse(job.movements.exists())


class ConcurrentAttributionInvestigationTests(ShippingSetup, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_employees_competing_preserve_winning_actor(self):
        job = self.make_job(71010)
        barrier = Barrier(2)

        def attempt(employee):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    ship_jobs(job_ids=[job.pk], employee=employee)
                    return employee.pk
                except ValidationError:
                    return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt, employee) for employee in (self.manager, self.worker)]
            results = [future.result(timeout=20) for future in futures]
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(JobShip.objects.get(job=job).shipped_by_id, winners[0])
