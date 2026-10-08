from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import PayrollTimeClockCreateForm
from .models import Employee, Role, TimeClock
from .services import clock_in_employee, clock_out_employee
from .timeclock_creation import save_manual_timeclock


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class TimeClockCreationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("viewer", password="test")
        self.admin_user = User.objects.create_superuser("admin", password="test")
        role = Role.objects.create(name="Hourly", requires_clock_in=True)
        self.employee = Employee.objects.create(user=self.user, role=role, must_change_password=False)
        self.other = Employee.objects.create(user=User.objects.create_user("other"), role=role)
        self.client.force_login(self.user)
        self.query = urlencode({"start_date": "2026-08-03", "end_date": "2026-08-09", "employee": self.employee.pk})
        self.url = reverse("culet:payroll_timeclock_create", args=[self.employee.pk]) + "?" + self.query
        self.admin_url = reverse("admin:culet_timeclock_add")
        self.data = {"date": "2026-08-03", "clock_in": "08:08", "clock_out": "16:52"}
        self.htmx = {"HTTP_HX_REQUEST": "true"}

    def instant(self, day=3, hour=8, minute=8):
        return timezone.make_aware(datetime(2026, 8, day, hour, minute))

    def event(self, start=None, end=None, **kwargs):
        return TimeClock(employee=self.employee, clock_in=start or self.instant(),
                         clock_out=end or self.instant(hour=16, minute=52), **kwargs)

    def admin_data(self, day="2026-08-03", start="08:08:00", end="16:52:00"):
        return {"employee": self.employee.pk, "clock_in_0": day, "clock_in_1": start,
                "clock_out_0": day, "clock_out_1": end, "_save": "Save"}

    def assert_initialized(self, event):
        self.assertEqual(event.clock_in, self.instant())
        self.assertEqual(event.clock_out, self.instant(hour=16, minute=52))
        self.assertIsNone(event.adjusted_clock_in)
        self.assertIsNone(event.adjusted_clock_out)
        self.assertTrue(event.valid)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.clocked_in)

    def test_null_safe_string(self):
        self.assertIn("Missing clock-in", str(TimeClock(employee=self.employee)))

    def test_admin_creation_initializes_raw_fields_and_logs_success(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.admin_url)
        form = response.context["adminform"].form
        self.assertEqual(set(form.fields), {"employee", "clock_in", "clock_out"})
        self.assertTrue(all(field.required for field in form.fields.values()))
        response = self.client.post(self.admin_url, {
            **self.admin_data(), "adjusted_clock_in": "2026-08-03T01:00", "valid": "",
        })
        self.assertEqual(response.status_code, 302)
        event = TimeClock.objects.get()
        self.assert_initialized(event)
        self.assertTrue(self.admin_user.logentry_set.filter(object_id=str(event.pk), action_flag=1).exists())

    def test_admin_requires_timestamps_employee_and_strict_order(self):
        self.client.force_login(self.admin_user)
        for changes in ({"employee": ""}, {"clock_in_1": ""}, {"clock_out_0": ""},
                        {"clock_in_1": "bad"}, {"clock_out_1": "08:08:00"}, {"clock_out_1": "07:00:00"}):
            with self.subTest(changes=changes):
                response = self.client.post(self.admin_url, {**self.admin_data(), **changes})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
                self.assertFalse(TimeClock.objects.exists())

    def test_admin_overlap_and_dst_errors_are_form_errors(self):
        self.client.force_login(self.admin_user)
        save_manual_timeclock(self.event())
        for data in (self.admin_data(), self.admin_data(start="09:00:00", end="18:00:00"),
                     self.admin_data(day="2026-03-08", start="02:30:00", end="04:00:00"),
                     self.admin_data(day="2026-11-01", start="01:30:00", end="03:00:00")):
            with self.subTest(data=data):
                response = self.client.post(self.admin_url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
        self.assertEqual(TimeClock.objects.count(), 1)

    def test_admin_edit_keeps_raw_readonly_and_changes_only_adjustments(self):
        event = save_manual_timeclock(self.event())
        self.client.force_login(self.admin_user)
        url = reverse("admin:culet_timeclock_change", args=[event.pk])
        response = self.client.get(url)
        self.assertNotIn("clock_in", response.context["adminform"].form.fields)
        response = self.client.post(url, {
            "employee": self.employee.pk, "valid": "on",
            "adjusted_clock_in_0": "2026-08-03", "adjusted_clock_in_1": "09:00:00",
            "adjusted_clock_out_0": "", "adjusted_clock_out_1": "",
            "clock_in_0": "2020-01-01", "clock_in_1": "00:00:00", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.clock_in, self.instant())
        self.assertEqual(event.adjusted_clock_in, self.instant(hour=9, minute=0))

    def test_payroll_add_button_including_employee_without_events(self):
        response = self.client.get(reverse("culet:payroll_report") + "?" + self.query)
        self.assertContains(response, "+ Add Event")
        response = self.client.get(self.url, **self.htmx)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertNotIn("employee", form.fields)
        self.assertTrue(date(2026, 8, 3) <= form.initial["date"] <= date(2026, 8, 9))
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_payroll_creation_uses_url_employee_and_recalculates_rounding(self):
        response = self.client.post(self.url, {**self.data, "employee": self.other.pk,
                                              "valid": "", "adjusted_clock_in": "bad"}, **self.htmx)
        self.assertEqual(response.status_code, 200)
        event = TimeClock.objects.get()
        self.assertEqual(event.employee, self.employee)
        self.assert_initialized(event)
        self.assertEqual(event.rounded_hours, 8.5)
        self.assertAlmostEqual(event.effective_hours, 8 + 44 / 60)
        self.assertContains(response, "Total Paid 8.50")
        self.assertContains(response, "TimeClock event added")
        self.assertContains(response, "start_date=2026-08-03")
        self.assertContains(response, "end_date=2026-08-09")

    def test_non_htmx_creation_returns_to_same_filters_and_employee(self):
        response = self.client.post(self.url, self.data)
        expected = reverse("culet:payroll_report") + "?" + self.query + f"#payroll-employee-{self.employee.pk}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_payroll_missing_invalid_and_out_of_period_inputs(self):
        for changes in ({"date": ""}, {"clock_in": ""}, {"clock_out": ""}, {"clock_in": "bad"},
                        {"clock_out": "08:08"}, {"clock_out": "07:00"}, {"date": "2026-08-10"}):
            with self.subTest(changes=changes):
                response = self.client.post(self.url, {**self.data, **changes}, **self.htmx)
                self.assertEqual(response.status_code, 422)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(response["HX-Retarget"], f"#payroll-add-{self.employee.pk}")
                self.assertFalse(TimeClock.objects.exists())

    def test_duplicate_overlap_and_adjacent_intervals(self):
        self.client.post(self.url, self.data)
        for start, end in (("08:08", "16:52"), ("09:00", "10:00"), ("07:00", "18:00"),
                           ("07:00", "09:00"), ("16:00", "18:00")):
            response = self.client.post(self.url, {**self.data, "clock_in": start, "clock_out": end}, **self.htmx)
            self.assertEqual(response.status_code, 422)
            self.assertContains(response, "overlaps", status_code=422)
        self.assertEqual(TimeClock.objects.count(), 1)
        response = self.client.post(self.url, {**self.data, "clock_in": "16:52", "clock_out": "18:00"}, **self.htmx)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TimeClock.objects.count(), 2)

    def test_overlap_checks_raw_adjusted_invalid_and_open_intervals(self):
        existing = self.event(adjusted_clock_in=self.instant(hour=7, minute=0),
                              adjusted_clock_out=self.instant(hour=18, minute=0), valid=False)
        existing.save()
        for start, end in ((6, 8), (17, 19)):
            with self.assertRaises(ValidationError):
                save_manual_timeclock(self.event(start=self.instant(hour=start, minute=0), end=self.instant(hour=end, minute=0)))
        existing.clock_out = None
        existing.adjusted_clock_out = None
        existing.save()
        with self.assertRaises(ValidationError):
            save_manual_timeclock(self.event(start=self.instant(day=4), end=self.instant(day=4, hour=16)))
        self.assertEqual(TimeClock.objects.count(), 1)

    def test_race_revalidation_returns_inline_error(self):
        with patch("culet.views.save_manual_timeclock", side_effect=ValidationError("Concurrent overlap")):
            response = self.client.post(self.url, self.data, **self.htmx)
        self.assertContains(response, "Concurrent overlap", status_code=422)
        self.assertFalse(TimeClock.objects.exists())

    def test_dst_gaps_folds_and_elapsed_hours_across_transition(self):
        with timezone.override(ZoneInfo("America/New_York")):
            for day, start in ((date(2026, 3, 8), "02:30"), (date(2026, 11, 1), "01:30")):
                form = PayrollTimeClockCreateForm({"date": day, "clock_in": start, "clock_out": "04:00"},
                    employee=self.employee, start_date=day, end_date=day)
                self.assertFalse(form.is_valid())
                self.assertIn("clock_in", form.errors)
            for day, hours in ((date(2026, 3, 8), 2), (date(2026, 11, 1), 4)):
                form = PayrollTimeClockCreateForm({"date": day, "clock_in": "00:30", "clock_out": "03:30"},
                    employee=self.employee, start_date=day, end_date=day)
                self.assertTrue(form.is_valid(), form.errors)
                event = save_manual_timeclock(TimeClock(employee=self.employee,
                    clock_in=form.cleaned_data["clock_in"], clock_out=form.cleaned_data["clock_out"]))
                event.refresh_from_db()
                self.assertEqual(event.rounded_hours, hours)

    def test_permissions_csrf_and_employee_filter(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        self.client.force_login(self.user)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, self.data).status_code, 403)
        self.assertEqual(self.client.get(self.admin_url).status_code, 302)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(self.admin_url).status_code, 403)
        self.user.user_permissions.add(Permission.objects.get(codename="add_timeclock"))
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)
        wrong = reverse("culet:payroll_timeclock_create", args=[self.other.pk]) + "?" + self.query
        self.assertEqual(self.client.post(wrong, self.data).status_code, 403)
        self.assertFalse(TimeClock.objects.exists())

    def test_manual_event_does_not_change_current_punch_or_employee_status(self):
        current = TimeClock.objects.create(employee=self.employee, clock_in=self.instant(day=5))
        self.employee.clocked_in = True
        self.employee.save(update_fields=["clocked_in"])
        save_manual_timeclock(self.event())
        current.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertIsNone(current.clock_out)
        self.assertTrue(self.employee.clocked_in)

    def test_normal_clock_in_and_out_preserve_adjustments(self):
        start = self.instant()
        end = start + timedelta(hours=2)
        with patch("culet.services.timezone.now", return_value=start):
            self.assertTrue(clock_in_employee(self.employee).created_clock)
            self.assertFalse(clock_in_employee(self.employee).created_clock)
        event = TimeClock.objects.get()
        self.assertIsNone(event.adjusted_clock_in)
        self.assertTrue(event.valid)
        event.adjusted_clock_out = end + timedelta(hours=1)
        event.save(update_fields=["adjusted_clock_out"])
        with patch("culet.services.timezone.now", return_value=end):
            clock_out_employee(self.employee)
        event.refresh_from_db()
        self.assertEqual(event.clock_out, end)
        self.assertEqual(event.adjusted_clock_out, end + timedelta(hours=1))


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class TimeClockCreationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.employee = Employee.objects.create(user=User.objects.create_user("clock-race"))
        self.start = timezone.now() - timedelta(hours=3)
        self.end = self.start + timedelta(hours=1)

    def race(self, callback):
        barrier = Barrier(2)
        def run(action):
            close_old_connections()
            try:
                employee = Employee.objects.get(pk=self.employee.pk)
                barrier.wait(timeout=10)
                return action(employee)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            actions = [callback, callback] if callable(callback) else callback
            futures = [pool.submit(run, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_manual_events_create_exactly_one(self):
        def create(employee):
            try:
                save_manual_timeclock(TimeClock(employee=employee, clock_in=self.start, clock_out=self.end))
                return "created"
            except ValidationError:
                return "rejected"
        self.assertCountEqual(self.race(create), ["created", "rejected"])
        self.assertEqual(TimeClock.objects.count(), 1)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.clocked_in)

    def test_concurrent_employee_punches_create_exactly_one(self):
        outcomes = self.race(lambda employee: clock_in_employee(employee).created_clock)
        self.assertCountEqual(outcomes, [True, False])
        self.assertEqual(TimeClock.objects.count(), 1)

    def test_manual_event_racing_employee_punch_cannot_overlap(self):
        now = timezone.now()
        def manual(employee):
            try:
                save_manual_timeclock(TimeClock(employee=employee,
                    clock_in=now - timedelta(hours=1), clock_out=now + timedelta(hours=1)))
                return True
            except ValidationError:
                return False
        outcomes = self.race([manual, lambda employee: clock_in_employee(employee).created_clock])
        self.assertCountEqual(outcomes, [True, False])
        event = TimeClock.objects.get()
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.clocked_in, event.clock_out is None)
