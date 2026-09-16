from datetime import datetime
from io import BytesIO
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import Client, TestCase
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
            "valid": "on",
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


class PayrollInlineTimeClockTests(PayrollTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.entry = self.make_entry(
            self.john,
            self.aware(2026, 8, 3, 8),
            self.aware(2026, 8, 3, 17),
        )
        self.query = urlencode(
            {
                "start_date": "2026-08-03",
                "end_date": "2026-08-09",
                "employee": self.john.pk,
            }
        )
        self.inline_url = (
            reverse("culet:payroll_timeclock_inline_edit", args=[self.entry.pk])
            + "?"
            + self.query
        )
        self.row_url = (
            reverse("culet:payroll_timeclock_row", args=[self.entry.pk])
            + "?"
            + self.query
        )
        self.htmx = {"HTTP_HX_REQUEST": "true"}

    def valid_post(self, clock_out="2026-08-03T16:00"):
        return {
            "employee": self.john.pk,
            "valid": "on",
            "clock_in": "2026-08-03T08:00",
            "clock_out": clock_out,
        }

    def test_edit_returns_populated_compact_edit_row(self):
        response = self.client.get(self.inline_url, **self.htmx)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/partials/payroll_timeclock_edit_row.html")
        self.assertContains(response, f'id="timeclock-editor-{self.entry.pk}"')
        self.assertContains(response, 'value="2026-08-03T08:00"')
        self.assertContains(response, 'value="2026-08-03T17:00"')
        self.assertContains(response, "Save")
        self.assertContains(response, "Cancel")
        self.assertContains(response, 'hx-target="#payroll-results"')
        self.assertContains(response, 'hx-swap="outerHTML"', count=2)
        self.assertContains(response, 'hx-sync="this:drop"')
        self.assertContains(response, 'hx-select="#payroll-results"')
        self.assertNotContains(response, "hx-disabled-elt")
        self.assertContains(response, self.query.replace("&", "&amp;"))

    def test_valid_post_updates_entry_and_refreshes_report(self):
        response = self.client.post(self.inline_url, self.valid_post(), **self.htmx)
        self.entry.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(timezone.localtime(self.entry.effective_clock_out).hour, 16)
        self.assertContains(response, f'id="timeclock-row-{self.entry.pk}"')
        self.assertTemplateUsed(response, "reports/partials/payroll_results.html")
        self.assertContains(response, "4:00 PM")
        self.assertContains(response, f'hx-target="#timeclock-editor-{self.entry.pk}"')
        self.assertContains(response, 'hx-swap="outerHTML"')
        self.assertContains(response, 'id="payroll-report-totals"')
        self.assertContains(response, 'id="payroll-results"')

    def test_invalid_post_returns_edit_row_and_errors(self):
        response = self.client.post(
            self.inline_url,
            {"employee": self.john.pk, "clock_in": "invalid", "clock_out": ""},
            **self.htmx,
        )
        self.assertEqual(response.status_code, 422)
        self.assertTemplateUsed(response, "reports/partials/payroll_timeclock_edit_row.html")
        self.assertContains(response, "Enter a valid date/time", status_code=422)
        self.entry.refresh_from_db()
        self.assertEqual(timezone.localtime(self.entry.clock_out).hour, 17)

    def test_cancel_closes_editor_without_changing_punch(self):
        response = self.client.get(self.row_url, **self.htmx)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/partials/payroll_timeclock_editor_placeholder.html")
        self.assertContains(response, f'id="timeclock-editor-{self.entry.pk}"')
        self.assertNotContains(response, "Save")
        self.entry.refresh_from_db()
        self.assertEqual(timezone.localtime(self.entry.clock_out).hour, 17)

    def test_login_is_required_for_inline_editing(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.inline_url, **self.htmx).status_code, 302)
        self.assertEqual(
            self.client.post(self.inline_url, self.valid_post(), **self.htmx).status_code,
            302,
        )

    def test_edit_from_41_to_40_updates_rounding_overtime_and_all_summaries(self):
        for day in range(4, 8):
            self.make_entry(
                self.john,
                self.aware(2026, 8, day, 8),
                self.aware(2026, 8, day, 16),
            )

        before = self.client.get(
            reverse("culet:payroll_report") + "?" + self.query
        )
        self.assertContains(before, "OT 1.00")

        response = self.client.post(self.inline_url, self.valid_post(), **self.htmx)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OT 0.00")
        self.assertContains(response, "Regular 40.00")
        self.assertContains(response, "Total Paid 40.00")
        self.assertContains(response, "40.00")
        self.assertNotContains(response, "41.00")

    def test_non_htmx_request_preserves_standalone_editor(self):
        response = self.client.get(self.inline_url)
        self.assertRedirects(
            response,
            reverse("culet:time_clock_edit", args=[self.entry.pk]),
            fetch_redirect_response=False,
        )


class PayrollInlineTimeClockDeleteTests(PayrollTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.entry = self.make_entry(
            self.john,
            self.aware(2026, 8, 3, 8),
            self.aware(2026, 8, 3, 10),
        )
        self.query = urlencode(
            {
                "start_date": "2026-08-03",
                "end_date": "2026-08-09",
                "employee": self.john.pk,
            }
        )
        self.delete_url = (
            reverse("culet:payroll_timeclock_inline_delete", args=[self.entry.pk])
            + "?"
            + self.query
        )
        self.htmx = {"HTTP_HX_REQUEST": "true"}

    def test_payroll_ui_has_no_delete_action(self):
        response = self.client.get(reverse("culet:payroll_report") + "?" + self.query)
        self.assertContains(response, "Edit")
        self.assertNotContains(response, "Delete")
        self.assertNotContains(response, "payroll-delete")
        self.assertNotContains(response, "hx-confirm")

    def test_authorized_post_deletes_record_and_returns_oob_totals(self):
        response = self.client.post(self.delete_url, **self.htmx)
        self.assertEqual(response.status_code, 200)
        self.entry.refresh_from_db()
        self.assertFalse(self.entry.valid)
        self.assertTemplateUsed(
            response,
            "reports/partials/payroll_inline_delete_response.html",
        )
        self.assertNotContains(response, f'id="timeclock-row-{self.entry.pk}"')
        self.assertContains(response, 'id="payroll-report-totals"')
        self.assertContains(response, 'hx-swap-oob="true"', count=5)
        self.assertContains(response, "0.00")

    def test_delete_requires_post(self):
        response = self.client.get(self.delete_url, **self.htmx)
        self.assertEqual(response.status_code, 405)
        self.assertTrue(TimeClock.objects.filter(pk=self.entry.pk).exists())

    def test_delete_requires_login(self):
        self.client.logout()
        response = self.client.post(self.delete_url, **self.htmx)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TimeClock.objects.filter(pk=self.entry.pk).exists())

    def test_delete_is_csrf_protected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.viewer)
        response = csrf_client.post(self.delete_url, **self.htmx)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(TimeClock.objects.filter(pk=self.entry.pk).exists())

    def test_delete_recalculates_weekly_overtime_from_41_to_39(self):
        for day in range(4, 8):
            self.make_entry(
                self.john,
                self.aware(2026, 8, day, 8),
                self.aware(2026, 8, day, 16),
            )
        self.make_entry(
            self.john,
            self.aware(2026, 8, 3, 10),
            self.aware(2026, 8, 3, 17),
        )

        before = self.client.get(
            reverse("culet:payroll_report") + "?" + self.query
        )
        self.assertContains(before, "OT 1.00")

        response = self.client.post(self.delete_url, **self.htmx)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OT 0.00")
        self.assertContains(response, "Rounded: 39.00")
        self.assertContains(response, "Overtime: 0.00")
        self.assertNotContains(response, "41.00")

    def test_open_timeclock_cannot_be_deleted(self):
        self.entry.clock_out = None
        self.entry.save(update_fields=["clock_out"])

        response = self.client.post(self.delete_url, **self.htmx)

        self.assertEqual(response.status_code, 409)
        self.assertContains(
            response,
            "Open TimeClock entries cannot be deleted",
            status_code=409,
        )
        self.assertTrue(TimeClock.objects.filter(pk=self.entry.pk).exists())

        payroll = self.client.get(
            reverse("culet:payroll_report") + "?" + self.query
        )
        self.assertContains(payroll, "Missing")
        self.assertNotContains(payroll, "payroll-delete")

    def test_already_deleted_entry_returns_404(self):
        self.entry.delete()
        response = self.client.post(self.delete_url, **self.htmx)
        self.assertEqual(response.status_code, 404)


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


class TimeClockAdjustmentTests(PayrollTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start = self.aware(2026, 8, 3, 8, 8)
        self.end = self.aware(2026, 8, 3, 16, 52)
        self.entry = self.make_entry(self.john, self.start, self.end)

    def edit(self, start="2026-08-03T08:08", end="2026-08-03T16:52", valid=True):
        from .forms import TimeClockEditForm
        form = TimeClockEditForm({"employee": self.john.pk, "clock_in": start,
                                  "clock_out": end, "valid": valid}, instance=self.entry)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.entry.refresh_from_db()

    def report(self):
        from .payroll import build_payroll_report
        return build_payroll_report(start_date=self.start.date(), end_date=self.start.date())

    def test_untouched_rounding_and_unrounded_totals_are_compatible(self):
        self.assertIsNone(self.entry.adjusted_clock_in)
        self.assertIsNone(self.entry.adjusted_clock_out)
        self.assertTrue(self.entry.valid)
        self.assertEqual(self.entry.rounded_clock_in, self.aware(2026, 8, 3, 8, 15))
        self.assertEqual(self.entry.rounded_clock_out, self.aware(2026, 8, 3, 16, 45))
        self.assertEqual(self.entry.rounded_hours, 8.5)
        self.assertEqual(self.report()["report_totals"]["raw_hours"], self.entry.raw_hours)
        self.edit()
        self.assertIsNone(self.entry.adjusted_clock_in)
        self.assertIsNone(self.entry.adjusted_clock_out)

    def test_edit_and_clear_preserve_raw_punches(self):
        self.edit(start="2026-08-03T09:00", end="2026-08-03T17:00")
        self.assertEqual((self.entry.clock_in, self.entry.clock_out), (self.start, self.end))
        self.assertEqual(self.entry.rounded_hours, 8)
        self.edit(start="", end="")
        self.assertIsNone(self.entry.adjusted_clock_in)
        self.assertIsNone(self.entry.adjusted_clock_out)
        self.assertEqual(self.entry.effective_clock_in, self.start)
        self.assertEqual(self.entry.effective_clock_out, self.end)

    def test_missing_raw_clock_out_can_be_adjusted(self):
        self.entry.clock_out = None
        self.entry.save(update_fields=["clock_out"])
        self.edit(end="2026-08-03T17:00")
        self.assertIsNone(self.entry.clock_out)
        self.assertEqual(self.entry.rounded_hours, 8.75)

    def test_effective_order_validation_including_cleared_adjustment(self):
        from .forms import TimeClockEditForm
        from django.core.exceptions import ValidationError
        self.entry.adjusted_clock_out = self.aware(2026, 8, 3, 7)
        with self.assertRaises(ValidationError):
            self.entry.full_clean()
        self.entry.adjusted_clock_out = None
        form = TimeClockEditForm({"employee": self.john.pk, "clock_in": "",
                                  "clock_out": "2026-08-03T07:00", "valid": True}, instance=self.entry)
        self.assertFalse(form.is_valid())
        self.assertIn("cannot precede", str(form.errors))

    def test_invalid_is_visible_but_zero_in_totals_and_export(self):
        self.edit(valid=False)
        report = self.report()
        self.assertEqual(report["report_totals"], {"raw_hours": 0, "rounded_hours": 0, "overtime_hours": 0})
        self.assertEqual(len(report["employee_rows"][0]["weeks"][0]["days"][self.start.date()]["entries"]), 1)
        response = self.client.get(reverse("culet:payroll_excel"),
                                   {"start_date": "2026-08-03", "end_date": "2026-08-03"})
        sheet = load_workbook(BytesIO(response.content), data_only=True).active
        self.assertEqual(tuple(sheet.values)[1], ("John Smith", 0, 0, 0, 0))
        self.edit(valid=True)
        self.assertEqual(self.entry.rounded_hours, 8.5)

    def test_adjustment_changes_date_range_and_week_membership(self):
        self.edit(start="2026-08-10T08:00", end="2026-08-10T16:00")
        self.assertEqual(self.report()["employee_rows"], [])
        from .payroll import build_payroll_report
        day = self.aware(2026, 8, 10, 0).date()
        report = build_payroll_report(start_date=day, end_date=day)
        self.assertEqual(report["report_totals"]["rounded_hours"], 8)
        self.assertIn(day, report["employee_rows"][0]["weeks_by_start"])

    def test_operational_punches_ignore_adjustments_and_validity(self):
        from .services import clock_in_employee, clock_out_employee
        self.entry.clock_out = None
        self.entry.adjusted_clock_out = self.end
        self.entry.valid = False
        self.entry.save()
        result = clock_in_employee(self.john)
        self.assertFalse(result.created_clock)
        clock_out_employee(self.john)
        self.entry.refresh_from_db()
        self.assertIsNotNone(self.entry.clock_out)
        self.assertEqual(self.entry.adjusted_clock_out, self.end)
        self.assertEqual(self.entry.clock_in, self.start)
        self.assertFalse(self.entry.valid)

    def test_adjusted_and_invalid_hours_change_weekly_overtime_and_export(self):
        for day in range(4, 8):
            self.make_entry(self.john, self.aware(2026, 8, day, 8), self.aware(2026, 8, day, 17))
        self.edit(start="2026-08-03T08:00", end="2026-08-03T18:00")
        params = {"start_date": "2026-08-03", "end_date": "2026-08-09"}
        def exported_values():
            response = self.client.get(reverse("culet:payroll_excel"), params)
            return tuple(load_workbook(BytesIO(response.content), data_only=True).active.values)[1]
        self.assertEqual(exported_values(), ("John Smith", 46, 6, 46, 6))
        self.edit(start="2026-08-03T08:00", end="2026-08-03T18:00", valid=False)
        self.assertEqual(exported_values(), ("John Smith", 36, 0, 36, 0))

    def test_new_employee_clock_in_writes_only_raw_fields(self):
        from .services import clock_in_employee
        result = clock_in_employee(self.jane)
        self.assertTrue(result.created_clock)
        entry = TimeClock.objects.get(employee=self.jane)
        self.assertIsNotNone(entry.clock_in)
        self.assertIsNone(entry.clock_out)
        self.assertIsNone(entry.adjusted_clock_in)
        self.assertIsNone(entry.adjusted_clock_out)
        self.assertTrue(entry.valid)

    def test_stale_manual_edit_cannot_overwrite_new_raw_punch(self):
        new_punch = self.aware(2026, 8, 3, 18)
        TimeClock.objects.filter(pk=self.entry.pk).update(clock_out=new_punch)
        self.edit(end="2026-08-03T17:00")
        self.assertEqual(self.entry.clock_out, new_punch)
        self.assertEqual(self.entry.effective_clock_out, self.aware(2026, 8, 3, 17))


class PayrollDisplayTests(PayrollTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.day = self.aware(2026, 8, 3, 0).date()
        self.params = {"start_date": "2026-08-03", "end_date": "2026-08-09"}

    def entry(self, start_hour, start_min, end_hour, end_min):
        return self.make_entry(self.john, self.aware(2026, 8, 3, start_hour, start_min),
                               self.aware(2026, 8, 3, end_hour, end_min))

    def display(self):
        from .payroll import build_payroll_display
        return build_payroll_display(start_date=self.day, end_date=self.aware(2026, 8, 9, 0).date())

    def entries(self):
        return [entry for row in self.display()["employee_rows"] for day in row["days"] for entry in day["entries"]]

    def page(self):
        return self.client.get(reverse("culet:payroll_report"), self.params)

    def test_compact_table_daily_and_employee_totals(self):
        self.entry(8, 0, 12, 0)
        self.entry(12, 30, 14, 0)
        self.entry(14, 30, 16, 30)
        self.make_entry(self.john, self.aware(2026, 8, 4, 8), self.aware(2026, 8, 4, 16))
        row = self.display()["employee_rows"][0]
        self.assertEqual([day["rounded_hours"] for day in row["days"]], [7.5, 8])
        self.assertEqual((row["regular_hours"], row["overtime_hours"], row["rounded_hours"]), (15.5, 0, 15.5))
        response = self.page()
        self.assertContains(response, '<table ', count=1)
        self.assertContains(response, "Daily Total", count=2)
        self.assertContains(response, "Total Paid 15.50", count=2)
        self.assertNotContains(response, "Raw Hours")
        self.assertNotContains(response, "Delete")

    def test_warning_thresholds_use_rounded_effective_times(self):
        normal = self.entry(7, 53, 16, 37)  # Rounds to exactly 8:00 and 16:30.
        early_late = self.entry(7, 52, 16, 38)
        values = {e["timeclock"].pk: e for e in self.entries()}
        self.assertEqual(values[normal.pk]["in_warning"], "")
        self.assertEqual(values[normal.pk]["out_warning"], "")
        self.assertEqual(values[early_late.pk]["in_warning"], "Paid time before 8:00 AM")
        self.assertEqual(values[early_late.pk]["out_warning"], "Paid time after 4:30 PM")
        early_late.adjusted_clock_in = self.aware(2026, 8, 3, 7, 30)
        early_late.adjusted_clock_out = self.aware(2026, 8, 3, 16, 30)
        early_late.save()
        response = self.page()
        self.assertContains(response, 'title="Paid time before 8:00 AM">7:30 AM')
        self.assertNotContains(response, 'title="Paid time after 4:30 PM"')
        self.assertContains(response, 'title="Clock-in manually adjusted"', count=1)
        self.assertContains(response, 'title="Clock-out manually adjusted"', count=1)

    def test_short_lunch_uses_adjacent_valid_rounded_sessions(self):
        first = self.entry(8, 0, 12, 7)  # Rounded 12:00.
        ignored = self.entry(12, 8, 12, 14)
        ignored.valid = False
        ignored.save()
        second = self.entry(12, 22, 14, 0)  # Rounded 12:15 -> short lunch.
        third = self.entry(14, 30, 15, 0)  # Exactly 30 -> no warning.
        fourth = self.entry(15, 0, 16, 0)  # Zero -> no warning.
        entries = {e["timeclock"].pk: e for e in self.entries()}
        self.assertEqual(entries[second.pk]["in_warning"], "Lunch break under 30 minutes")
        for clock in (first, ignored, third, fourth):
            self.assertEqual(entries[clock.pk]["in_warning"], "")
        response = self.page()
        self.assertContains(response, 'title="Lunch break under 30 minutes">12:15 PM', count=1)

    def test_invalid_rows_muted_zero_and_without_warnings(self):
        invalid = self.entry(7, 0, 18, 0)
        invalid.valid = False
        invalid.save()
        response = self.page()
        self.assertContains(response, 'class="payroll-invalid text-muted"')
        self.assertContains(response, ">Invalid</span>")
        self.assertNotContains(response, "text-danger")
        self.assertNotContains(response, "Paid time before")
        self.assertNotContains(response, "Paid time after")
        self.assertContains(response, "Total Paid 0.00", count=2)

    def test_missing_punches_and_undated_entries_remain_editable_with_zero_paid(self):
        no_out = self.make_entry(self.john, self.aware(2026, 8, 3, 8), None)
        no_in = self.make_entry(self.john, None, self.aware(2026, 8, 3, 16))
        undated = self.make_entry(self.john, None, None)
        outside = self.make_entry(self.john, None, self.aware(2026, 7, 1, 16))
        response = self.page()
        for clock in (no_out, no_in, undated):
            self.assertContains(response, f'id="timeclock-row-{clock.pk}"')
            self.assertContains(response, f'hx-target="#timeclock-editor-{clock.pk}"')
        self.assertNotContains(response, f'id="timeclock-row-{outside.pk}"')
        self.assertContains(response, 'class="payroll-punch text-danger">Missing</span>', count=4)
        self.assertContains(response, "Date unknown")
        self.assertContains(response, "Total Paid 0.00", count=2)
        self.assertEqual(self.display()["report_totals"]["rounded_hours"], 0)

    def test_editor_shows_raw_readonly_and_effective_inputs_below_row(self):
        clock = self.entry(8, 0, 16, 30)
        clock.adjusted_clock_in = self.aware(2026, 8, 3, 9)
        clock.save()
        page = self.page().content.decode()
        self.assertLess(page.index(f'id="timeclock-row-{clock.pk}"'), page.index(f'id="timeclock-editor-{clock.pk}"'))
        response = self.client.get(reverse("culet:payroll_timeclock_inline_edit", args=[clock.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Raw In")
        self.assertContains(response, "Raw Out")
        self.assertContains(response, "Adjusted In")
        self.assertContains(response, "Adjusted Out")
        self.assertContains(response, 'value="2026-08-03T09:00"')
        self.assertContains(response, f'name="clock-{clock.pk}-clock_in"')
        self.assertContains(response, 'colspan="5"')
        self.assertNotContains(response, 'name="raw_clock_in"')

    def test_prefixed_save_normalizes_raw_and_refreshes_neighbor_lunch_warning(self):
        first = self.entry(8, 0, 12, 0)
        self.entry(12, 15, 16, 30)
        first.adjusted_clock_in = self.aware(2026, 8, 3, 9)
        first.save()
        url = reverse("culet:payroll_timeclock_inline_edit", args=[first.pk]) + "?" + urlencode(self.params)
        response = self.client.post(url, {
            f"clock-{first.pk}-clock_in": "2026-08-03T08:00",
            f"clock-{first.pk}-clock_out": "2026-08-03T11:45",
            f"clock-{first.pk}-valid": "on",
        }, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        self.assertIsNone(first.adjusted_clock_in)
        self.assertEqual(first.clock_out, self.aware(2026, 8, 3, 12))
        self.assertEqual(first.clock_in, self.aware(2026, 8, 3, 8))
        self.assertNotContains(response, "Lunch break under 30 minutes")
        self.assertContains(response, 'id="payroll-results"')
        self.assertContains(response, "Total Paid 8.00", count=2)

    def test_correct_missing_clock_in_through_inline_editor(self):
        clock = self.make_entry(self.john, None, self.aware(2026, 8, 3, 16))
        url = reverse("culet:payroll_timeclock_inline_edit", args=[clock.pk]) + "?" + urlencode(self.params)
        response = self.client.post(url, {"clock_in": "2026-08-03T08:00", "clock_out": "2026-08-03T16:00", "valid": "on"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        clock.refresh_from_db()
        self.assertIsNone(clock.clock_in)
        self.assertEqual(clock.rounded_hours, 8)
        self.assertContains(response, "Total Paid 8.00", count=2)

    def test_invalid_input_targets_editor_and_preserves_display(self):
        clock = self.entry(8, 0, 16, 30)
        url = reverse("culet:payroll_timeclock_inline_edit", args=[clock.pk]) + "?" + urlencode(self.params)
        response = self.client.post(url, {"clock_in": "bad", "valid": "on"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response["HX-Retarget"], f"#timeclock-editor-{clock.pk}")
        self.assertEqual(response["HX-Reselect"], f"#timeclock-editor-{clock.pk}")

    def test_standalone_return_link_keeps_filters_after_inline_save(self):
        from html import unescape
        import re
        from urllib.parse import parse_qs, urlsplit
        clock = self.entry(8, 0, 16, 30)
        params = {**self.params, "employee": self.john.pk}
        url = reverse("culet:payroll_timeclock_inline_edit", args=[clock.pk]) + "?" + urlencode(params)
        response = self.client.post(url, {"clock_in": "2026-08-03T08:00", "clock_out": "2026-08-03T16:00", "valid": "on"}, HTTP_HX_REQUEST="true")
        edit_url = reverse("culet:time_clock_edit", args=[clock.pk])
        href = re.search(r'href="(' + re.escape(edit_url) + r'[^\"]+)"', response.content.decode()).group(1)
        next_url = parse_qs(urlsplit(unescape(href)).query)["next"][0]
        self.assertEqual(next_url, reverse("culet:payroll_report") + "?" + urlencode(params))
