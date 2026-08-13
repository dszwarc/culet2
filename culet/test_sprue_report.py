from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from .models import Customer, Employee, Job, JobWeight, Role, Style
from .views import SprueReportView


class SprueReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sprue-manager",
            password="test",
            first_name="Morgan",
            last_name="Manager",
        )
        manager_role = Role.objects.create(name="Manager", level=30)
        Employee.objects.create(
            user=self.user,
            role=manager_role,
            must_change_password=False,
        )
        self.client.force_login(self.user)

        customer = Customer.objects.create(
            name="Sprue Customer",
            address="1 Foundry Way",
            email="sprue@example.com",
            phone="555-0100",
        )
        self.alpha = Style.objects.create(name="ALPHA", customer=customer)
        self.beta = Style.objects.create(name="BETA", customer=customer)
        self.alpha_job = Job.objects.create(
            name="Alpha Job",
            stock_num="ALPHA-1",
            style=self.alpha,
            due=timezone.localdate() + timedelta(days=7),
        )
        self.beta_job = Job.objects.create(
            name="Beta Job",
            stock_num="BETA-1",
            style=self.beta,
            due=timezone.localdate() + timedelta(days=7),
        )
        self.url = reverse("culet:sprue_report")

    def local_datetime(self, day, at_time=time(12, 0)):
        return timezone.make_aware(
            datetime.combine(day, at_time),
            timezone.get_current_timezone(),
        )

    def make_weight(self, job, created_at, value="10.000", **kwargs):
        return JobWeight.objects.create(
            job=job,
            created_at=created_at,
            weight=Decimal(value),
            sprue_weight=Decimal(kwargs.get("sprue_weight", "2.000")),
            dust_weight=Decimal(kwargs.get("dust_weight", "0.500")),
            recorded_by=kwargs.get("recorded_by", self.user),
            step=kwargs.get("step"),
        )

    def report(self, start_date, end_date, style=None):
        params = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        if style is not None:
            params["style"] = style.pk
        return self.client.get(self.url, params)

    def test_in_range_records_appear_and_out_of_range_records_do_not(self):
        selected_day = timezone.localdate()
        included = self.make_weight(
            self.alpha_job,
            self.local_datetime(selected_day),
            "11.111",
        )
        excluded = self.make_weight(
            self.alpha_job,
            self.local_datetime(selected_day - timedelta(days=1)),
            "22.222",
        )

        response = self.report(selected_day, selected_day)

        self.assertIn(included, response.context["weights"])
        self.assertNotIn(excluded, response.context["weights"])
        self.assertContains(response, "11.111")
        self.assertNotContains(response, "22.222")

    def test_date_boundaries_include_entire_start_and_end_dates(self):
        start_date = timezone.localdate() - timedelta(days=1)
        end_date = timezone.localdate()
        at_start = self.make_weight(
            self.alpha_job,
            self.local_datetime(start_date, time.min),
        )
        at_end = self.make_weight(
            self.alpha_job,
            self.local_datetime(end_date, time(23, 59, 59, 999999)),
        )
        before = self.make_weight(
            self.alpha_job,
            self.local_datetime(start_date - timedelta(days=1), time(23, 59, 59)),
        )
        after = self.make_weight(
            self.alpha_job,
            self.local_datetime(end_date + timedelta(days=1), time.min),
        )

        weights = list(self.report(start_date, end_date).context["weights"])

        self.assertIn(at_start, weights)
        self.assertIn(at_end, weights)
        self.assertNotIn(before, weights)
        self.assertNotIn(after, weights)

    def test_style_filter_returns_only_selected_style(self):
        day = timezone.localdate()
        alpha_weight = self.make_weight(self.alpha_job, self.local_datetime(day))
        beta_weight = self.make_weight(self.beta_job, self.local_datetime(day))

        weights = list(self.report(day, day, self.alpha).context["weights"])

        self.assertEqual(weights, [alpha_weight])
        self.assertNotIn(beta_weight, weights)

    def test_all_styles_returns_multiple_styles(self):
        day = timezone.localdate()
        self.make_weight(self.alpha_job, self.local_datetime(day))
        self.make_weight(self.beta_job, self.local_datetime(day))

        weights = list(self.report(day, day).context["weights"])

        self.assertEqual({weight.job.style for weight in weights}, {self.alpha, self.beta})

    def test_results_are_ordered_by_style_then_created_at(self):
        day = timezone.localdate()
        beta = self.make_weight(self.beta_job, self.local_datetime(day, time(8)))
        alpha_later = self.make_weight(self.alpha_job, self.local_datetime(day, time(16)))
        alpha_earlier = self.make_weight(self.alpha_job, self.local_datetime(day, time(9)))

        weights = list(self.report(day, day).context["weights"])

        self.assertEqual(weights, [alpha_earlier, alpha_later, beta])

    def test_totals_sum_only_filtered_weight_records(self):
        day = timezone.localdate()
        self.make_weight(
            self.alpha_job,
            self.local_datetime(day),
            "10.125",
            sprue_weight="2.250",
            dust_weight="0.375",
        )
        self.make_weight(
            self.alpha_job,
            self.local_datetime(day, time(14)),
            "5.500",
            sprue_weight="1.125",
            dust_weight="0.625",
        )
        self.make_weight(
            self.beta_job,
            self.local_datetime(day),
            "99.000",
            sprue_weight="99.000",
            dust_weight="99.000",
        )

        response = self.report(day, day, self.alpha)

        self.assertEqual(response.context["totals"]["piece_weight"], Decimal("15.625"))
        self.assertEqual(response.context["totals"]["sprue_weight"], Decimal("3.375"))
        self.assertEqual(response.context["totals"]["dust_weight"], Decimal("1.000"))
        self.assertContains(response, "15.625")
        self.assertContains(response, "3.375")
        self.assertContains(response, "1.000")

    def test_blank_related_weight_information_does_not_break_report(self):
        day = timezone.localdate()
        self.make_weight(
            self.alpha_job,
            self.local_datetime(day),
            step=None,
            recorded_by=None,
        )

        response = self.report(day, day)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "—")

    def test_url_resolves_and_page_renders(self):
        match = resolve(self.url)

        self.assertIs(match.func.view_class, SprueReportView)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sprue Report")
        self.assertContains(response, "All Styles")

    def test_homepage_contains_sprue_report_link(self):
        response = self.client.get(reverse("culet:home"))

        self.assertContains(response, "Sprue Report")
        self.assertContains(response, self.url)
