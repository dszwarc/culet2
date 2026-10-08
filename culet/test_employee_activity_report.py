from datetime import date, datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .employee_activity_report import build_employee_activity_report, MINUTE_US
from .models import Activity, ActivityStep, Customer, Employee, Job, Style, TimeClock, WorkBatch
from .services import stop_work_batch


@override_settings(TIME_ZONE="America/New_York")
class EmployeeActivityReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = User.objects.create_user(username="activity-viewer")
        cls.alice = Employee.objects.create(
            user=User.objects.create_user(username="alice", first_name="Alice", last_name="Alpha"),
            must_change_password=False,
        )
        cls.bob = Employee.objects.create(
            user=User.objects.create_user(username="bob", first_name="Bob", last_name="Zulu"),
            must_change_password=False, active=False,
        )
        cls.customer = Customer.objects.create(name="Activity Customer")
        cls.style = Style.objects.create(name="ACT-STYLE", customer=cls.customer)
        cls.job = Job.objects.create(stock_num="ACT-123", barcode=61001, style=cls.style,
                                     due=date(2026, 10, 9))
        cls.step = ActivityStep.objects.create(name="Assembly", code="activity-assembly")

    def setUp(self):
        self.client.force_login(self.viewer)
        self.url = reverse("culet:report_employee_activity")

    @staticmethod
    def stamp(day, hour=0, minute=0, *, month=10, second=0, fold=0):
        return datetime(2026, month, day, hour, minute, second,
                        tzinfo=ZoneInfo("America/New_York"), fold=fold).astimezone(datetime_timezone.utc)

    def activity(self, start=None, end=None, employee=None, **kwargs):
        return Activity.objects.create(
            employee=employee or self.alice,
            start=start or self.stamp(5, 9), end=end,
            **{"job": self.job, "step": self.step, "active": False, **kwargs},
        )

    def clock(self, start, end, employee=None, **kwargs):
        return TimeClock.objects.create(employee=employee or self.alice,
                                        clock_in=start, clock_out=end, **kwargs)

    def report(self, **kwargs):
        return build_employee_activity_report(
            **{"start_date": date(2026, 10, 5), "end_date": date(2026, 10, 6), **kwargs}
        )["employee_rows"]

    def get_report(self, **kwargs):
        return self.client.get(self.url, {"start_date": "2026-10-05", "end_date": "2026-10-06", **kwargs})

    def test_original_duration_sum_failure_and_missing_relations_render(self):
        activity = self.activity(end=self.stamp(5, 10, 20), job=None, step=None, name="Manual task")
        response = self.get_report(employee=self.alice.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1h 20m")
        self.assertContains(response, "Manual task")
        self.assertContains(response, "N/A (no clocked-in time)")
        activity.refresh_from_db()
        self.assertEqual(activity.duration, timedelta(minutes=80))

    def test_all_employees_and_individual_rows_daily_and_employee_totals(self):
        later = self.activity(start=self.stamp(5, 11), end=self.stamp(5, 11, 45))
        earlier = self.activity(end=self.stamp(5, 10, 20))
        tomorrow = self.activity(start=self.stamp(6, 9), end=self.stamp(6, 10, 10))
        self.activity(end=self.stamp(5, 10), employee=self.bob)
        self.clock(self.stamp(5, 8), self.stamp(5, 12))
        rows = self.report()
        self.assertEqual([row["employee"] for row in rows], [self.alice, self.bob])
        row = rows[0]
        self.assertEqual([entry["activity"] for entry in row["days"][0]["entries"]], [earlier, later])
        self.assertEqual([day["duration_label"] for day in row["days"]], ["2h 05m", "1h 10m"])
        self.assertEqual(row["active_label"], "3h 15m")
        self.assertEqual(row["clocked_label"], "4h 00m")
        self.assertEqual(row["difference_label"], "45m")
        self.assertEqual(row["activity_percentage"], 81.25)
        self.assertEqual(row["active_us"], sum(day["active_us"] for day in row["days"]))
        self.assertEqual(row["clocked_us"], sum(row["clock_days"].values()))
        response = self.get_report()
        self.assertContains(response, self.job.get_absolute_url(), count=4)
        self.assertContains(response, "ACT-STYLE", count=5)  # Four rows plus the style filter option.
        self.assertContains(response, "81.3%")

    def test_piecework_visible_but_excluded_even_for_piecework_only_employee(self):
        self.activity(end=self.stamp(5, 10))
        self.activity(end=self.stamp(5, 17), is_piecework=True)
        self.activity(end=self.stamp(5, 17), is_piecework=True, employee=self.bob)
        rows = self.report()
        self.assertEqual([row["active_label"] for row in rows], ["1h 00m", "0m"])
        self.assertEqual([row["piecework_count"] for row in rows], [1, 1])
        self.assertContains(self.get_report(), "Piecework · excluded", count=2)

    def test_missing_negative_and_incomplete_durations_excluded(self):
        self.activity()  # Open: never included in completed totals.
        missing = self.activity(end=self.stamp(5, 10))
        Activity.objects.filter(pk=missing.pk).update(duration=None)
        negative = self.activity(end=self.stamp(5, 10))
        Activity.objects.filter(pk=negative.pk).update(duration=-timedelta(minutes=1))
        self.activity(start=self.stamp(5, 11), end=self.stamp(5, 10))
        valid = self.activity(end=self.stamp(5, 10), active=True)  # end is completion authority.
        self.activity(start=self.stamp(5, 12), end=self.stamp(5, 12))  # Valid zero duration.
        row = self.report()[0]
        self.assertEqual(row["invalid_activities"], 3)
        self.assertEqual(row["active_label"], "1h 00m")
        self.assertEqual(len(row["days"][0]["entries"]), 2)
        self.assertContains(self.get_report(), "3 completed activity record(s) excluded")
        missing.refresh_from_db()
        self.assertIsNone(missing.duration)

    def test_effective_adjustments_invalid_open_and_missing_clock_values(self):
        self.activity(end=self.stamp(5, 10))
        raw_start, raw_end = self.stamp(1, 8, month=9), self.stamp(1, 17, month=9)
        adjusted = self.clock(raw_start, raw_end, adjusted_clock_in=self.stamp(5, 8, 7),
                              adjusted_clock_out=self.stamp(5, 10, 9))
        self.clock(self.stamp(5, 10, 9), None, adjusted_clock_out=self.stamp(5, 11, 9))
        self.clock(None, self.stamp(5, 12, 9), adjusted_clock_in=self.stamp(5, 11, 9))
        self.clock(self.stamp(5, 12, 9), self.stamp(5, 18), valid=False)
        self.clock(self.stamp(5, 12, 9), None)
        self.clock(None, None)
        self.clock(self.stamp(5, 18), self.stamp(5, 17))
        row = self.report()[0]
        self.assertEqual(row["clocked_label"], "4h 02m")
        self.assertEqual(row["invalid_clocks"], 1)
        self.assertEqual(row["clocked_us"], 242 * MINUTE_US)
        adjusted.refresh_from_db()
        self.assertEqual(adjusted.clock_in, raw_start)
        self.assertEqual(adjusted.clock_out, raw_end)

    def test_cross_midnight_range_clipping_and_exact_boundaries(self):
        first = self.activity(start=self.stamp(4, 23), end=self.stamp(5, 1))
        self.activity(start=self.stamp(5, 23), end=self.stamp(6, 1))
        self.activity(start=self.stamp(6, 23), end=self.stamp(7, 1))
        self.activity(start=self.stamp(4, 22), end=self.stamp(5))  # No overlap.
        self.activity(start=self.stamp(7), end=self.stamp(7, 1))  # No overlap.
        self.clock(self.stamp(4, 23), self.stamp(7, 1))
        row = self.report()[0]
        self.assertEqual([day["duration_label"] for day in row["days"]], ["2h 00m", "2h 00m"])
        self.assertEqual(row["active_label"], "4h 00m")
        self.assertEqual(row["clocked_label"], "48h 00m")
        self.assertTrue(row["days"][0]["entries"][0]["continued"])
        self.assertEqual(row["days"][0]["entries"][0]["activity"], first)
        self.assertEqual(list(row["clock_days"].values()), [24 * 60 * MINUTE_US] * 2)

    def test_batch_recorded_shares_are_prorated_not_recomputed(self):
        start, end = self.stamp(5, 23), self.stamp(6, 1)
        batch = WorkBatch.objects.create(employee=self.alice, step=self.step, started_at=start)
        activities = [self.activity(start=start, end=None, batch=batch, active=True) for _ in range(2)]
        stop_work_batch(batch=batch, stopped_at=end)
        row = self.report()[0]
        self.assertEqual(row["active_label"], "2h 00m")
        self.assertEqual([day["duration_label"] for day in row["days"]], ["1h 00m", "1h 00m"])
        self.assertEqual(row["historical_duration_count"], 0)
        self.assertEqual(self.report(start_date=date(2026, 10, 6))[0]["active_label"], "1h 00m")
        for activity in activities:
            activity.refresh_from_db()
            self.assertEqual(activity.duration, timedelta(hours=1))

    def test_historical_nonbatch_duration_mismatch_is_preserved_and_explained(self):
        activity = self.activity(end=self.stamp(5, 11))
        Activity.objects.filter(pk=activity.pk).update(duration=timedelta(minutes=45))
        row = self.report()[0]
        self.assertEqual(row["active_label"], "45m")
        self.assertEqual(row["historical_duration_count"], 1)
        self.assertContains(self.get_report(), "recorded duration different from elapsed time")
        activity.refresh_from_db()
        self.assertEqual(activity.duration, timedelta(minutes=45))

    def test_dst_spring_and_fall_days_use_actual_elapsed_time(self):
        for month, day, hours in ((3, 8, 23), (11, 1, 25)):
            start, end = self.stamp(day, month=month), self.stamp(day + 1, month=month)
            self.activity(start=start, end=end)
            self.clock(start, end)
            row = self.report(start_date=date(2026, month, day), end_date=date(2026, month, day))[0]
            self.assertEqual(row["active_us"], hours * 60 * MINUTE_US)
            self.assertEqual(row["clocked_us"], hours * 60 * MINUTE_US)
            self.assertEqual(row["activity_percentage"], 100)

    def test_dst_repeated_hour_and_local_date_not_utc_date(self):
        start = self.stamp(1, 1, 15, month=11, fold=0)
        end = self.stamp(1, 1, 45, month=11, fold=1)
        self.activity(start=start, end=end)
        self.clock(start, end)
        row = self.report(start_date=date(2026, 11, 1), end_date=date(2026, 11, 1))[0]
        self.assertEqual(row["active_label"], "1h 30m")
        self.assertEqual(row["clocked_label"], "1h 30m")
        self.activity(start=self.stamp(5, 23, 10), end=self.stamp(5, 23, 50))
        row = self.report()[0]
        self.assertEqual(row["days"][0]["date"], date(2026, 10, 5))

    def test_overlapping_clocks_count_once_but_labor_records_remain_separate(self):
        self.activity(end=self.stamp(5, 12))
        self.activity(end=self.stamp(5, 12))
        self.clock(self.stamp(5, 9), self.stamp(5, 11))
        self.clock(self.stamp(5, 10), self.stamp(5, 12))
        row = self.report()[0]
        self.assertEqual(row["active_label"], "6h 00m")
        self.assertEqual(row["clocked_label"], "3h 00m")
        self.assertEqual(row["difference_label"], "−3h 00m")
        self.assertEqual(row["activity_percentage"], 200)
        self.assertContains(self.get_report(), "overlapping clock interval(s) counted once")

    def test_subminute_rows_daily_and_employee_displays_reconcile(self):
        for second in (0, 10, 20):
            self.activity(start=self.stamp(5, 9, second=second), end=self.stamp(5, 9, second=second + 30))
        row = self.report()[0]
        self.assertEqual(row["active_us"], 90_000_000)
        self.assertEqual(row["active_label"], "2m")
        day = row["days"][0]
        self.assertEqual(day["duration_label"], "2m")
        self.assertEqual(sum(entry["minutes"] for entry in day["entries"]), 2)

    def test_employee_and_style_filters_and_zero_clocks(self):
        self.activity(end=self.stamp(5, 10))
        self.activity(end=self.stamp(5, 11), employee=self.bob)
        self.assertEqual(len(self.get_report(employee=self.bob.pk).context["employee_rows"]), 1)
        row = self.report(employee=self.bob, style=self.style)[0]
        self.assertIsNone(row["activity_percentage"])
        self.assertEqual(row["clocked_label"], "0m")
        self.assertEqual(row["difference_label"], "−2h 00m")

    def test_default_payroll_week_empty_permissions_and_invalid_dates(self):
        with patch("culet.views.timezone.localdate", return_value=date(2026, 10, 8)):
            response = self.client.get(self.url)
        self.assertEqual(response.context["form"].cleaned_data["start_date"], date(2026, 10, 4))
        self.assertEqual(response.context["form"].cleaned_data["end_date"], date(2026, 10, 10))
        self.assertContains(response, "No completed activities")
        self.assertContains(response, "All Employees")
        self.assertContains(self.get_report(end_date="2026-10-04"), "End date cannot be before start date")
        self.assertEqual(self.get_report(start_date="bad").status_code, 200)
        self.client.logout()
        self.assertEqual(self.get_report().status_code, 302)

    def test_query_count_is_constant_and_rendering_has_no_relationship_n_plus_one(self):
        self.activity(end=self.stamp(5, 10))
        self.clock(self.stamp(5, 8), self.stamp(5, 17))
        with self.assertNumQueries(2):
            self.report()
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as first:
            self.get_report()
        for index in range(8):
            employee = Employee.objects.create(user=User.objects.create_user(username=f"extra-{index}"))
            self.activity(end=self.stamp(5, 10), employee=employee)
            self.clock(self.stamp(5, 8), self.stamp(5, 17), employee=employee)
        with self.assertNumQueries(2):
            self.report()
        with CaptureQueriesContext(connection) as expanded:
            response = self.get_report()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(expanded), len(first))
