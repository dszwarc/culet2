from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import QualityInspectionForm
from .models import (
    Activity,
    ActivityStep,
    Customer,
    Department,
    Employee,
    FailureType,
    Job,
    QualityInspection,
    QualityInspectionFailure,
    QualityInspectionStep,
    Role,
    Style,
)


class QualityInspectionTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="inspector", password="test")
        self.department = Department.objects.create(name="Quality Control")
        self.role = Role.objects.create(name="Inspector")
        self.employee = Employee.objects.create(
            user=self.user,
            department=self.department,
            role=self.role,
            can_qc=True,
            must_change_password=False,
        )
        self.customer = Customer.objects.create(
            name="QC Customer",
            address="1 Main St",
            email="qc@example.com",
            phone="555-0100",
        )
        self.style = Style.objects.create(name="QC-STYLE", customer=self.customer)
        self.job = Job.objects.create(
            name="QC Job",
            barcode=810001,
            stock_num="QC-1",
            style=self.style,
            due=timezone.localdate() + timedelta(days=7),
        )
        self.qc_activity_step = ActivityStep.objects.create(
            name="Inspection",
            code="qc",
        )
        self.inspection_step = QualityInspectionStep.objects.create(
            name="Final Inspection",
            code="final",
            sort_order=20,
        )
        self.failure_type = FailureType.objects.create(name="Porosity")
        self.url = reverse("culet:quality_inspection")
        self.client.force_login(self.user)

    def submission_data(self, **overrides):
        data = {
            "barcode": str(self.job.barcode),
            "step": str(self.inspection_step.pk),
            "result": QualityInspection.RESULT_PASS,
            "inspection_duration_minutes": "17",
            "notes": "Looks good",
        }
        data.update(overrides)
        return data


class QualityInspectionModelTests(QualityInspectionTestBase):
    def test_step_creation_ordering_and_active_status(self):
        inactive = QualityInspectionStep.objects.create(
            name="Pre-Polish Inspection",
            code="pre-polish",
            sort_order=10,
            active=False,
        )
        self.assertEqual(
            list(QualityInspectionStep.objects.all()),
            [inactive, self.inspection_step],
        )
        self.assertFalse(inactive.active)
        self.assertEqual(str(inactive), "Pre-Polish Inspection")

    def test_inspection_relationships_and_legacy_step_display(self):
        activity = Activity.objects.create(
            job=self.job,
            employee=self.employee,
            step=self.qc_activity_step,
            end=timezone.now(),
            active=False,
        )
        inspection = QualityInspection.objects.create(
            job=self.job,
            inspected_by=self.employee,
            step=self.inspection_step,
            activity=activity,
            result=QualityInspection.RESULT_PASS,
        )
        self.assertEqual(inspection.step_display, "Final Inspection")
        self.assertEqual(activity.quality_inspection, inspection)

        legacy = QualityInspection.objects.create(
            job=self.job,
            inspected_by=self.employee,
            step=None,
            result=QualityInspection.RESULT_FAIL,
        )
        self.assertEqual(legacy.step_display, "Legacy / Unknown")


class QualityInspectionFormTests(QualityInspectionTestBase):
    def test_only_active_steps_are_available_in_configured_order(self):
        earlier = QualityInspectionStep.objects.create(
            name="After Setting Inspection", code="after-setting", sort_order=10
        )
        inactive = QualityInspectionStep.objects.create(
            name="Inactive", code="inactive", sort_order=1, active=False
        )
        form = QualityInspectionForm()
        self.assertEqual(
            list(form.fields["step"].queryset),
            [earlier, self.inspection_step],
        )
        self.assertNotIn(inactive, form.fields["step"].queryset)

    def test_step_is_required_and_inactive_step_is_rejected(self):
        missing = QualityInspectionForm(self.submission_data(step=[]))
        self.assertFalse(missing.is_valid())
        self.assertIn("step", missing.errors)

        self.inspection_step.active = False
        self.inspection_step.save(update_fields=["active"])
        inactive = QualityInspectionForm(
            self.submission_data(step=[str(self.inspection_step.pk)])
        )
        self.assertFalse(inactive.is_valid())
        self.assertIn("step", inactive.errors)

    def test_exactly_one_step_must_be_selected(self):
        other = QualityInspectionStep.objects.create(
            name="Pre-Polish Inspection", code="pre-polish", sort_order=10
        )
        form = QualityInspectionForm(
            self.submission_data(
                step=[str(other.pk), str(self.inspection_step.pk)]
            )
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["step"], ["Choose exactly one inspection step."])


class QualityInspectionSubmissionTests(QualityInspectionTestBase):
    def test_valid_failure_creates_linked_completed_qc_activity_and_failures(self):
        before = timezone.now()
        response = self.client.post(
            self.url,
            self.submission_data(
                result=QualityInspection.RESULT_FAIL,
                failure_types=[str(self.failure_type.pk)],
            ),
        )
        after = timezone.now()
        self.assertRedirects(response, self.url)

        inspection = QualityInspection.objects.get()
        activity = Activity.objects.get()
        self.assertEqual(inspection.activity, activity)
        self.assertEqual(inspection.job, self.job)
        self.assertEqual(inspection.inspected_by, self.employee)
        self.assertEqual(inspection.step, self.inspection_step)
        self.assertEqual(inspection.result, QualityInspection.RESULT_FAIL)
        self.assertEqual(
            list(inspection.failures.values_list("failure_type", flat=True)),
            [self.failure_type.pk],
        )
        self.assertEqual(activity.step.code, "qc")
        self.assertFalse(activity.active)
        self.assertEqual(activity.end - activity.start, timedelta(minutes=17))
        self.assertGreaterEqual(activity.end, before)
        self.assertLessEqual(activity.end, after)
        self.assertEqual(activity.duration, timedelta(minutes=17))

    def test_inactive_step_submission_creates_nothing(self):
        self.inspection_step.active = False
        self.inspection_step.save(update_fields=["active"])
        response = self.client.post(self.url, self.submission_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(QualityInspection.objects.count(), 0)
        self.assertEqual(Activity.objects.count(), 0)

    def test_inspection_failure_rolls_back_activity(self):
        with patch(
            "culet.views.QualityInspection.objects.create",
            side_effect=RuntimeError("inspection write failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.submission_data())
        self.assertEqual(Activity.objects.count(), 0)
        self.assertEqual(QualityInspection.objects.count(), 0)

    def test_legacy_step_renders_safely_and_qc_activity_reporting_still_works(self):
        legacy = QualityInspection.objects.create(
            job=self.job,
            inspected_by=self.employee,
            result=QualityInspection.RESULT_FAIL,
        )
        QualityInspectionFailure.objects.create(
            inspection=legacy,
            failure_type=self.failure_type,
        )
        Activity.objects.create(
            job=self.job,
            employee=self.employee,
            step=self.qc_activity_step,
            end=timezone.now(),
            active=False,
        )
        response = self.client.get(reverse("culet:quality_failure_report"))
        self.assertContains(response, "Legacy / Unknown")
        self.assertEqual(Activity.objects.filter(step__code="qc").count(), 1)
