from collections import OrderedDict
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Employee, TimeClock


def payroll_week_starts(start_date, end_date):
    """Return every Monday-starting payroll week touched by the range."""
    week_start = start_date - timedelta(days=start_date.weekday())
    final_week_start = end_date - timedelta(days=end_date.weekday())
    weeks = []
    while week_start <= final_week_start:
        weeks.append(week_start)
        week_start += timedelta(days=7)
    return weeks


def build_payroll_report(*, start_date, end_date, selected_employee=None):
    """Build the shared payroll data consumed by the HTML and XLSX reports."""
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max))

    employees = (
        Employee.objects.select_related("user", "department", "role")
        .filter(role__requires_clock_in=True)
        .order_by("user__last_name", "user__first_name")
    )
    if selected_employee:
        employees = employees.filter(pk=selected_employee.pk)

    employee_rows = []
    report_totals = {"raw_hours": 0, "rounded_hours": 0, "overtime_hours": 0}

    for employee in employees:
        entries = (
            TimeClock.objects.filter(employee=employee, clock_in__lte=end_dt)
            .filter(Q(clock_out__gte=start_dt) | Q(clock_out__isnull=True))
            .order_by("clock_in")
        )
        weeks = OrderedDict()
        employee_raw_hours = 0
        employee_rounded_hours = 0

        for entry in entries:
            work_date = timezone.localtime(entry.clock_in).date()
            if work_date < start_date or work_date > end_date:
                continue

            # Payroll weeks have historically been grouped Monday through Sunday.
            week_start = work_date - timedelta(days=work_date.weekday())
            week = weeks.setdefault(
                week_start,
                {
                    "week_start": week_start,
                    "week_end": week_start + timedelta(days=6),
                    "days": OrderedDict(),
                    "raw_hours": 0,
                    "rounded_hours": 0,
                },
            )
            day = week["days"].setdefault(
                work_date,
                {
                    "date": work_date,
                    "entries": [],
                    "raw_hours": 0,
                    "rounded_hours": 0,
                },
            )
            raw_hours = entry.raw_hours
            rounded_hours = entry.rounded_hours
            day["entries"].append(
                {
                    "timeclock": entry,
                    "raw_clock_in": entry.clock_in,
                    "rounded_clock_in": entry.rounded_clock_in,
                    "raw_clock_out": entry.clock_out,
                    "rounded_clock_out": entry.rounded_clock_out,
                    "raw_hours": raw_hours,
                    "rounded_hours": rounded_hours,
                }
            )
            day["raw_hours"] += raw_hours
            day["rounded_hours"] += rounded_hours
            week["raw_hours"] += raw_hours
            week["rounded_hours"] += rounded_hours
            employee_raw_hours += raw_hours
            employee_rounded_hours += rounded_hours

        if weeks:
            employee_overtime_hours = 0
            for week in weeks.values():
                week["overtime_hours"] = max(week["rounded_hours"] - 40, 0)
                employee_overtime_hours += week["overtime_hours"]
            employee_rows.append(
                {
                    "employee": employee,
                    "weeks": list(weeks.values()),
                    "weeks_by_start": weeks,
                    "raw_hours": employee_raw_hours,
                    "rounded_hours": employee_rounded_hours,
                    "overtime_hours": employee_overtime_hours,
                }
            )
            report_totals["raw_hours"] += employee_raw_hours
            report_totals["rounded_hours"] += employee_rounded_hours
            report_totals["overtime_hours"] += employee_overtime_hours

    return {
        "employee_rows": employee_rows,
        "report_totals": report_totals,
        "week_starts": payroll_week_starts(start_date, end_date),
    }
