from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import (
    Customer, FindingStock, FindingType, Job, JobFinding, JobMetal, JobStone,
    JobStatus, Location, MetalPart, Style, StyleFinding, StyleMetal, StyleStone,
)


class JobCreationTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user(username="job-creator"))
        customer = Customer.objects.create(name="Creation customer")
        self.style = Style.objects.create(
            name="WITH-FINDING", customer=customer,
            stamp="14K", description="Style notes",
        )
        Location.objects.get_or_create(name="Office")
        JobStatus.objects.get_or_create(name="Waiting on Metal")
        part = MetalPart.objects.create(sku="Test part")
        StyleMetal.objects.create(style=self.style, part=part, qty_req=2, weight="3.25")
        StyleStone.objects.create(style=self.style, qty_req=3, stone_size="2mm")
        finding = FindingStock.objects.create(
            name="Test clasp", finding_type=FindingType.objects.create(name="Clasp"),
        )
        StyleFinding.objects.create(style=self.style, finding=finding, qty_req="1.500")
        self.url = reverse("culet:job_create")

    def style_payload(self):
        response = self.client.get(
            reverse("culet:job_style_defaults_htmx"), {"style_id": self.style.pk},
        )
        self.assertEqual(response.status_code, 200)
        data = {
            "style": self.style.pk, "customer": self.style.customer_id,
            "stock_num": "CREATION-1", "quantity": "1", "due": "2026-12-01",
            "stamp": "", "notes": "",
        }
        for prefix, context_key in (
            ("metals", "metal_formset"), ("stones", "stone_formset"),
            ("findings", "finding_formset"),
        ):
            formset = response.context[context_key]
            for name, value in formset.management_form.initial.items():
                data[f"{prefix}-{name}"] = value
            for form in formset:
                for field in form:
                    value = field.value()
                    data[field.html_name] = "" if value is None else value
        return data

    def assert_no_creation(self):
        for model in (Job, JobMetal, JobStone, JobFinding):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_create_from_style_with_finding_and_other_requirements(self):
        response = self.client.post(self.url, self.style_payload())
        job = Job.objects.get()
        self.assertRedirects(response, job.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual((job.stamp, job.notes), ("14K", "Style notes"))
        self.assertEqual(job.location.name, "Office")
        self.assertEqual(job.status.name, "Waiting on Metal")
        self.assertEqual(job.job_metals.get().weight_req, Decimal("3.25"))
        self.assertEqual(job.job_stones.get().qty_req, 3)
        finding = job.job_findings.get()
        self.assertEqual(finding.finding_id, self.style.style_findings.get().finding_id)
        self.assertEqual(finding.qty_req, Decimal("1.500"))
        self.assertEqual(finding.qty_used, 0)

    def test_style_without_findings_still_creates(self):
        self.style.style_findings.all().delete()
        response = self.client.post(self.url, self.style_payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(JobMetal.objects.count(), 1)
        self.assertEqual(JobStone.objects.count(), 1)
        self.assertFalse(JobFinding.objects.exists())

    def test_invalid_child_forms_redisplay_without_creating_job(self):
        for prefix, field in (("metals", "part"), ("stones", "qty_req"), ("findings", "finding")):
            with self.subTest(prefix=prefix):
                data = self.style_payload()
                data[f"{prefix}-0-{field}"] = ""
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                key = {"metals": "metal", "stones": "stone", "findings": "finding"}[prefix]
                self.assertIn(field, response.context[f"{key}_formset"].forms[0].errors)
                self.assertContains(response, "CREATION-1")
                self.assert_no_creation()

    def test_child_save_exception_rolls_back_parent_and_prior_children(self):
        data = self.style_payload()

        def fail_save(finding, *args, **kwargs):
            self.assertIsNotNone(finding.job.pk)
            self.assertTrue(Job.objects.filter(pk=finding.job.pk).exists())
            self.assertEqual(JobMetal.objects.count(), 1)
            self.assertEqual(JobStone.objects.count(), 1)
            raise RuntimeError("Simulated child save failure")

        with patch.object(JobFinding, "save", fail_save):
            with self.assertRaisesMessage(RuntimeError, "Simulated child save failure"):
                self.client.post(self.url, data)
        self.assert_no_creation()
