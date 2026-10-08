from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .forms import JobForm
from .models import Activity, FailureType, Job, JobMovement, JobShip, JobStatus, MovementType, PieceworkMemo, PieceworkMemoLine
from .shipping import ship_jobs
from .test_piecework_integrity import CuletTestDataMixin
from .views import JobUpdateView


class ShippingSetup(CuletTestDataMixin):
    def setUp(self):
        super().setUp()
        self.shipped_status = JobStatus.objects.create(name="Shipped")
        for code, field in (("shipped-unassigned", "assigned_to"), ("shipped-released", "holder")):
            MovementType.objects.create(name=code, code=code, job_field=field)

    def post_ship(self, job, **data):
        return self.client.post(reverse("culet:job_ship_bulk"), {"barcodes": str(job.barcode), **data})

    def assert_rejected(self, response, text):
        self.assertEqual(response.status_code, 200)
        self.assertIn(text, " ".join(str(m) for m in get_messages(response.wsgi_request)))


class ShippingTests(ShippingSetup, TestCase):
    def test_normal_barcode_and_stock_shipping_and_movements(self):
        for barcode, use_stock in ((60001, False), (60002, True)):
            job = self.make_job(barcode, assigned_to=self.worker, holder=self.worker)
            data = {"barcodes": "", "stock_numbers": job.stock_num} if use_stock else {}
            response = self.post_ship(job, notes="Shipping note", **data)
            self.assertEqual(response.status_code, 302)
            job.refresh_from_db()
            self.assertTrue(job.shipped)
            self.assertFalse(job.active)
            self.assertFalse(job.in_work)
            self.assertIsNone(job.assigned_to_id)
            self.assertIsNone(job.holder_id)
            self.assertEqual(job.status, self.shipped_status)
            shipment = JobShip.objects.get(job=job)
            self.assertEqual(shipment.shipped_by, self.manager)
            self.assertEqual(shipment.notes, "Shipping note")
            self.assertEqual(JobMovement.objects.filter(job=job).count(), 2)

    def test_four_states_and_preservation(self):
        for index, (flag, existing) in enumerate(((True, True), (False, True), (True, False))):
            job = self.make_job(60010 + index, shipped=flag)
            if existing:
                shipment = JobShip.objects.create(job=job, shipped_by=self.worker)
                original = (shipment.shipped_at, shipment.shipped_by_id)
            self.assert_rejected(self.post_ship(job), "conflict" if not flag else "Already shipped")
            job.refresh_from_db()
            self.assertEqual(job.shipped, flag)
            self.assertTrue(job.active)
            self.assertEqual(JobShip.objects.filter(job=job).count(), int(existing))
            if existing:
                shipment.refresh_from_db()
                self.assertEqual((shipment.shipped_at, shipment.shipped_by_id), original)

    def test_duplicate_scans_cross_field_and_repeat_submission(self):
        job = self.make_job(60020)
        for data in (
            {"barcodes": f"{job.barcode} {job.barcode}"},
            {"barcodes": "", "stock_numbers": f"{job.stock_num} {job.stock_num}"},
            {"stock_numbers": job.stock_num},
        ):
            self.assertEqual(self.post_ship(job, **data).status_code, 200)
            self.assertFalse(JobShip.objects.filter(job=job).exists())
        self.assertEqual(self.post_ship(job).status_code, 302)
        shipment = JobShip.objects.get(job=job)
        self.assert_rejected(self.post_ship(job), "Already shipped")
        self.assertEqual(JobShip.objects.get(job=job).shipped_at, shipment.shipped_at)

    def test_null_stock_and_active_work(self):
        job = self.make_job(60030, stock_num=None, shipped=True)
        self.assert_rejected(self.post_ship(job), str(job.barcode))
        job.shipped = False
        job.save(update_fields=["shipped"])
        Activity.objects.create(job=job, employee=self.worker, active=True, start=timezone.now())
        self.assert_rejected(self.post_ship(job), "currently being worked on")
        self.assertFalse(JobShip.objects.filter(job=job).exists())

    def test_eligibility_is_rechecked_after_scan_resolution(self):
        for index, condition in enumerate(("shipped", "shipment", "work", "piecework")):
            job = self.make_job(60120 + index)
            def change_after_resolution(**kwargs):
                if condition == "shipped":
                    Job.objects.filter(pk=job.pk).update(shipped=True)
                elif condition == "shipment":
                    JobShip.objects.create(job=job, shipped_by=self.worker)
                elif condition == "work":
                    Activity.objects.create(job=job, employee=self.worker, active=True,
                                            start=timezone.now())
                else:
                    memo = PieceworkMemo.objects.create(
                        created_by=self.manager, assigned_to=self.worker,
                        from_location=self.office, to_location=self.piecework,
                    )
                    PieceworkMemoLine.objects.create(memo=memo, job=job)
                return ship_jobs(**kwargs)
            with patch("culet.views.ship_jobs", side_effect=change_after_resolution):
                response = self.post_ship(job)
            expected = {"shipped": "Already shipped", "shipment": "conflict",
                        "work": "currently being worked on", "piecework": "still out for piecework"}
            self.assert_rejected(response, expected[condition])
            self.assertEqual(JobShip.objects.filter(job=job).count(), int(condition == "shipment"))
            self.assertFalse(JobMovement.objects.filter(job=job).exists())

    def test_invalid_child_job_edit_never_saves_parent(self):
        job = self.make_job(60130)
        form = JobForm(data={"customer": self.customer.pk, "style": self.style.pk,
                            "stock_num": job.stock_num, "quantity": 1, "due": job.due,
                            "notes": "Must not save"}, instance=job)
        self.assertTrue(form.is_valid(), form.errors)
        from django.test import RequestFactory
        from django.contrib.messages.storage.fallback import FallbackStorage
        request = RequestFactory().post("/")
        request.user = self.manager_user
        request.session = {}
        request._messages = FallbackStorage(request)
        view = JobUpdateView()
        view.request = request
        view.object = job
        formsets = {key: Mock(errors=[], non_form_errors=lambda: [])
                    for key in ("metal_formset", "stone_formset", "finding_formset")}
        for formset in formsets.values():
            formset.is_valid.return_value = False
        with patch.object(view, "get_context_data", return_value=dict(formsets)), \
             patch("culet.views.log_validation_failure"), \
             patch.object(view, "render_to_response"):
            view.form_valid(form)
        job.refresh_from_db()
        self.assertNotEqual(job.notes, "Must not save")
        for formset in formsets.values():
            formset.save.assert_not_called()

    def test_batch_validation_and_failure_roll_back_everything(self):
        first = self.make_job(60040, assigned_to=self.worker, holder=self.worker)
        second = self.make_job(60041, shipped=True)
        scans = f"{second.barcode} {first.barcode}"
        self.assert_rejected(self.post_ship(first, barcodes=scans), "Already shipped")
        self.assertFalse(JobShip.objects.exists())
        second.shipped = False
        second.save(update_fields=["shipped"])
        original_create = JobShip.objects.create
        def fail_second(**kwargs):
            if kwargs["job"].pk == second.pk:
                raise ValidationError("Expected shipping failure")
            return original_create(**kwargs)
        with patch("culet.shipping.JobShip.objects.create", side_effect=fail_second):
            self.assert_rejected(self.post_ship(first, barcodes=scans), "Expected shipping failure")
        first.refresh_from_db()
        self.assertFalse(first.shipped)
        self.assertTrue(first.active)
        self.assertEqual(first.assigned_to, self.worker)
        self.assertEqual(first.holder, self.worker)
        self.assertFalse(JobShip.objects.exists())
        self.assertFalse(JobMovement.objects.exists())

    def test_unrelated_integrity_errors_are_not_swallowed(self):
        job = self.make_job(60050)
        with patch("culet.shipping.JobShip.objects.create", side_effect=IntegrityError("unrelated")):
            with self.assertRaises(IntegrityError):
                self.post_ship(job)
        job.refresh_from_db()
        self.assertFalse(job.shipped)

    def test_actual_shipment_unique_violation_is_classified_and_handled(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL diagnostic fields required")
        job = self.make_job(60051)
        shipment = JobShip.objects.create(job=job, shipped_by=self.worker)
        # Simulate a writer not using the Job lock inserting after the lookup.
        with patch("culet.shipping.JobShip.objects.filter") as lookup:
            lookup.return_value.values_list.return_value = []
            self.assert_rejected(self.post_ship(job), "shipment already exists")
        job.refresh_from_db()
        self.assertFalse(job.shipped)
        self.assertEqual(JobShip.objects.get(job=job).shipped_at, shipment.shipped_at)

    def test_stale_job_edit_preserves_shipping_and_saves_m2m(self):
        job = self.make_job(60060)
        reason = FailureType.objects.create(name="Shipping edit regression")
        data = {"customer": self.customer.pk, "style": self.style.pk,
                "stock_num": job.stock_num, "quantity": 1, "due": job.due,
                "notes": "Edited after shipping", "repair_reasons": [reason.pk]}
        form = JobForm(data=data, instance=job)
        self.assertTrue(form.is_valid(), form.errors)
        ship_jobs(job_ids=[job.pk], employee=self.manager)
        view = JobUpdateView()
        view.object = job
        formsets = {}
        for key in ("metal_formset", "stone_formset", "finding_formset"):
            formsets[key] = Mock()
            formsets[key].is_valid.return_value = True
        with patch.object(view, "get_context_data", return_value=dict(formsets)):
            response = view.form_valid(form)
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertTrue(job.shipped)
        self.assertFalse(job.active)
        self.assertEqual(job.status, self.shipped_status)
        self.assertEqual(job.notes, "Edited after shipping")
        self.assertEqual(list(job.repair_reasons.all()), [reason])
        for formset in formsets.values():
            formset.save.assert_called_once()

    def test_admin_guards_and_stale_admin_save(self):
        from django.test import RequestFactory
        request = RequestFactory().get("/")
        self.manager_user.is_superuser = True
        self.manager_user.is_staff = True
        self.manager_user.save()
        request.user = self.manager_user
        job = self.make_job(60070)
        job_admin = admin.site._registry[Job]
        form_class = job_admin.get_form(request, job)
        for field in ("shipped", "active", "status"):
            self.assertNotIn(field, form_class.base_fields)
        shipment_admin = admin.site._registry[JobShip]
        self.assertFalse(shipment_admin.has_add_permission(request))
        self.assertFalse(shipment_admin.has_change_permission(request))
        self.assertFalse(shipment_admin.has_delete_permission(request))
        self.assertTrue(shipment_admin.has_view_permission(request))
        self.assertEqual(self.client.post(reverse("admin:culet_jobship_add"), {}).status_code, 403)
        # Same restricted-save path used by Admin; its instance predates shipping.
        form = JobForm(data={"customer": self.customer.pk, "style": self.style.pk,
                            "stock_num": job.stock_num, "quantity": 1, "due": job.due,
                            "notes": "Admin edit"}, instance=job)
        self.assertTrue(form.is_valid(), form.errors)
        ship_jobs(job_ids=[job.pk], employee=self.manager)
        job_admin.save_model(request, job, form, change=True)
        job.refresh_from_db()
        self.assertTrue(job.shipped)
        self.assertFalse(job.active)
        self.assertEqual(job.status, self.shipped_status)

    def test_import_rerun_preserves_existing_shipping_state(self):
        from .management.commands.import_jobs import Command
        from .management.commands.import_jobs_2025 import Command as Command2025
        for index, (flag, existing) in enumerate(((True, True), (True, False), (False, True), (False, False))):
            job = self.make_job(60080 + index, shipped=flag, active=False, status=self.shipped_status,
                                assigned_to=self.worker, holder=self.worker)
            if existing:
                JobShip.objects.create(job=job, shipped_by=self.worker)
            command = (Command if index % 2 else Command2025)()
            command.stats = SimpleNamespace(created=0, updated=0, unchanged=0)
            command.shipped_status = self.shipped_status
            command.open_status = None
            with patch.object(command, "resolve_customer", return_value=(self.customer, "")), \
                 patch.object(command, "resolve_style", return_value=(self.style, "")), \
                 patch.object(command, "prepare_barcode", return_value=(job.barcode, "")), \
                 patch.object(command, "prepare_stock_num", return_value=(job.stock_num, "")), \
                 patch.object(command, "get_mapped_object", return_value=job), \
                 patch.object(command, "record_mapping"), patch.object(command, "row_message"):
                command.import_job({"id": job.pk, "is_shipped": not flag, "notes": "Imported update"})
            job.refresh_from_db()
            self.assertEqual(job.shipped, flag)
            self.assertFalse(job.active)
            self.assertEqual(job.status, self.shipped_status)
            self.assertEqual(job.assigned_to, self.worker)
            self.assertEqual(job.holder, self.worker)
            self.assertEqual(job.notes, "Imported update")
            self.assertEqual(JobShip.objects.filter(job=job).count(), int(existing))


class ConcurrentShippingTests(ShippingSetup, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_shipping_creates_exactly_one_shipment(self):
        job = self.make_job(60100, assigned_to=self.worker, holder=self.worker)
        barrier = Barrier(2)
        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return ship_jobs(job_ids=[job.pk], employee=self.manager)
                except ValidationError as exc:
                    return exc.messages[0]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results.count(1), 1)
        self.assertTrue(any("Already shipped" in str(result) for result in results))
        self.assertEqual(JobShip.objects.filter(job=job).count(), 1)
        self.assertEqual(JobMovement.objects.filter(job=job).count(), 2)
