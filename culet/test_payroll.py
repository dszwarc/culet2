from datetime import datetime
from io import BytesIO
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import Employee, Role, TimeClock


class PayrollTestMixin:
    def setUp(self):
        self.viewer = User.objects.create_user(username="payroll-viewer", password="test")
        self.hourly_role = Role.objects.create(name="Hourly", requires_clock_in=True)
        self.excluded_role = Role.objects.create(name="Salary", requires_clock_in=False)
        self.john = self.make_employee("john", "John", "Smith", self.hourly_role)
        self.jane = self.make_employee("jane", "Jane", "Doe", self.hourly_role)
        self.excluded = self.make_employee("salary", "Sam", "Salary", self.excluded_role)
        self.client.force_login(self.viewer)

    @staticmethod
    def make_employee(username, first_name, last_name, role):
        user = User.objects.create_user(
            username=username, first_name=first_name, last_name=last_name
        )
        return Employee.objects.create(user=user, role=role)

    @staticmethod
    def aware(year, month, day, hour, minute=0):
        return timezone.make_aware(datetime(year, month, day, hour, minute))

    def make_entry(self, employee, start, end):
        return TimeClock.objects.create(employee=employee, clock_in=start, clock_out=end)


class TimeClockPayrollReturnTests(PayrollTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.entry = self.make_entry(
            self.john,
            self.aware(2026, 7, 27, 8),
            self.aware(2026, 7, 27, 16),
        )
        self.edit_url = reverse("culet:time_clock_edit", args=[self.entry.pk])
        self.payroll_url = reverse("culet:payroll_report")

    def valid_post(self, **extra):
        data = {
            "employee": self.john.pk,
            "clock_in": "2026-07-27T08:00",
            "clock_out": "2026-07-27T16:30",
        }
        data.update(extra)
        return data

    def test_safe_next_redirects_to_exact_filtered_payroll_url(self):
        query = urlencode(
            {"start_date": "2026-07-27", "end_date": "2026-08-09", "employee": self.john.pk}
        )
        next_url = f"{self.payroll_url}?{query}"
        response = self.client.post(
            f"{self.edit_url}?{urlencode({'next': next_url})}", self.valid_post(next=next_url)
        )
        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    def test_missing_next_falls_back_to_payroll(self):
        response = self.client.post(self.edit_url, self.valid_post())
        self.assertRedirects(response, self.payroll_url, fetch_redirect_response=False)

    def test_external_next_is_rejected(self):
        response = self.client.post(
            f"{self.edit_url}?next=https%3A%2F%2Fevil.example%2Fsteal",
            self.valid_post(next="https://evil.example/steal"),
        )
        self.assertRedirects(response, self.payroll_url, fetch_redirect_response=False)

    def test_validation_errors_render_without_redirecting(self):
        response = self.client.post(
            self.edit_url,
            {"employee": "", "clock_in": "not-a-date", "clock_out": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")


class PayrollExcelTests(PayrollTestMixin, TestCase):
    def export(self, start="2026-07-29", end="2026-08-10", employee=None):
        params = {"start_date": start, "end_date": end}
        if employee:
            params["employee"] = employee.pk
        response = self.client.get(reverse("culet:payroll_excel"), params)
        workbook = load_workbook(BytesIO(response.content), data_only=True)
        return response, workbook["Payroll"]

    def test_valid_xlsx_has_range_filename_repeating_weeks_and_numeric_totals(self):
        # Partial first/last weeks and 42.5 rounded hours in each of two weeks.
        for day in (29, 30, 31, 1, 2):
            month = 7 if day >= 29 else 8
            self.make_entry(
                self.john,
                self.aware(2026, month, day, 8),
                self.aware(2026, month, day, 16, 30),
            )
        for day in (3, 4, 5, 6, 7):
            self.make_entry(
                self.john,
                self.aware(2026, 8, day, 8),
                self.aware(2026, 8, day, 16, 30),
            )
        self.make_entry(
            self.jane, self.aware(2026, 7, 30, 8), self.aware(2026, 7, 30, 16)
        )
        self.make_entry(
            self.excluded, self.aware(2026, 7, 30, 8), self.aware(2026, 7, 30, 18)
        )

        response, sheet = self.export()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("payroll_2026-07-29_to_2026-08-10.xlsx", response["Content-Disposition"])
        self.assertEqual(
            [cell.value for cell in sheet[1]],
            [
                "Employee", "Week 1 Time", "Week 1 Overtime",
                "Week 2 Time", "Week 2 Overtime", "Week 3 Time", "Week 3 Overtime",
                "Total Time", "Total Overtime",
            ],
        )
        rows = {row[0]: row[1:] for row in sheet.iter_rows(min_row=2, values_only=True)}
        self.assertEqual(rows["John Smith"], (42.5, 2.5, 42.5, 2.5, 0, 0, 85, 5))
        self.assertEqual(rows["Jane Doe"], (8, 0, 0, 0, 0, 0, 8, 0))
        self.assertEqual(list(rows["John Smith"][-2:]), [85, 5])
        self.assertEqual(
            rows["John Smith"][-2],
            sum(rows["John Smith"][0:-2:2]),
        )
        self.assertEqual(
            rows["John Smith"][-1],
            sum(rows["John Smith"][1:-2:2]),
        )
        self.assertEqual([cell.value for cell in sheet[1]][-2:], ["Total Time", "Total Overtime"])
        self.assertNotIn("Sam Salary", rows)
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertEqual(sheet["B2"].number_format, "0.00")
        self.assertEqual(sheet.cell(row=2, column=sheet.max_column).number_format, "0.00")

    def test_totals_sum_weekly_values_without_offsetting_weekly_overtime(self):
        for day in range(27, 32):
            self.make_entry(
                self.john,
                self.aware(2026, 7, day, 8),
                self.aware(2026, 7, day, 17),
            )
        for day in range(3, 8):
            self.make_entry(
                self.john,
                self.aware(2026, 8, day, 8),
                self.aware(2026, 8, day, 15),
            )

        _response, sheet = self.export(start="2026-07-27", end="2026-08-09")

        self.assertEqual([cell.value for cell in sheet[1]][-2:], ["Total Time", "Total Overtime"])
        self.assertEqual(
            tuple(sheet.iter_rows(min_row=2, max_row=2, values_only=True))[0],
            ("John Smith", 45, 5, 35, 0, 80, 5),
        )

    def test_employee_filter_is_respected(self):
        self.make_entry(
            self.john, self.aware(2026, 8, 3, 8), self.aware(2026, 8, 3, 16)
        )
        self.make_entry(
            self.jane, self.aware(2026, 8, 3, 8), self.aware(2026, 8, 3, 16)
        )
        _response, sheet = self.export(start="2026-08-03", end="2026-08-09", employee=self.jane)
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual(sheet["A2"].value, "Jane Doe")

    def test_exactly_40_hours_has_no_overtime(self):
        for day in range(3, 8):
            self.make_entry(
                self.john, self.aware(2026, 8, day, 8), self.aware(2026, 8, day, 16)
            )
        _response, sheet = self.export(start="2026-08-03", end="2026-08-09")
        self.assertEqual(sheet["B2"].value, 40)
        self.assertEqual(sheet["C2"].value, 0)

    def test_export_uses_quarter_hour_payroll_rounding(self):
        self.make_entry(
            self.john,
            self.aware(2026, 8, 3, 8, 8),
            self.aware(2026, 8, 3, 16, 52),
        )
        _response, sheet = self.export(start="2026-08-03", end="2026-08-09")
        self.assertEqual(sheet["B2"].value, 8.5)

    def test_payroll_page_links_preserve_full_query_string(self):
        self.make_entry(
            self.john, self.aware(2026, 8, 3, 8), self.aware(2026, 8, 3, 16)
        )
        response = self.client.get(
            reverse("culet:payroll_report"),
            {"start_date": "2026-08-03", "end_date": "2026-08-09", "employee": self.john.pk},
        )
        self.assertContains(response, "Download Excel")
        self.assertContains(response, "next=")
