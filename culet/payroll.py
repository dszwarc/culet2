from collections import OrderedDict
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.db.models.functions import Coalesce
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


def build_payroll_report(*, start_date, end_date, selected_employee=None, include_incomplete=False):
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
            TimeClock.objects.filter(employee=employee)
            .annotate(payroll_in=Coalesce("adjusted_clock_in", "clock_in"),
                      payroll_out=Coalesce("adjusted_clock_out", "clock_out"))
            .annotate(payroll_date=Coalesce("adjusted_clock_in", "clock_in", "adjusted_clock_out", "clock_out"))
            .order_by("payroll_date", "pk")
        )
        matching = Q(payroll_in__lte=end_dt) & (Q(payroll_out__gte=start_dt) | Q(payroll_out__isnull=True))
        if include_incomplete:
            matching |= Q(payroll_in__isnull=True, payroll_out__range=(start_dt, end_dt))
            matching |= Q(payroll_in__isnull=True, payroll_out__isnull=True)
        entries = entries.filter(matching)
        weeks = OrderedDict()
        employee_raw_hours = 0
        employee_rounded_hours = 0

        for entry in entries:
            timestamp = entry.effective_clock_in or entry.effective_clock_out
            work_date = timezone.localtime(timestamp).date() if timestamp else None
            if work_date is not None and (work_date < start_date or work_date > end_date):
                continue

            # Payroll weeks have historically been grouped Monday through Sunday.
            week_start = work_date - timedelta(days=work_date.weekday()) if work_date else None
            week = weeks.setdefault(
                week_start,
                {
                    "week_start": week_start,
                    "week_end": week_start + timedelta(days=6) if week_start else None,
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
            raw_hours = entry.effective_hours
            rounded_hours = entry.rounded_hours
            # Preserve existing template keys; "raw" here means unrounded payroll time.
            day["entries"].append(
                {
                    "timeclock": entry,
                    "raw_clock_in": entry.effective_clock_in,
                    "rounded_clock_in": entry.rounded_clock_in,
                    "raw_clock_out": entry.effective_clock_out,
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


def build_payroll_display(**filters):
    """Presentation metadata only; paid hours and weekly OT use the shared report."""
    report = build_payroll_report(**filters, include_incomplete=True)
    for row in report["employee_rows"]:
        row["regular_hours"] = row["rounded_hours"] - row["overtime_hours"]
        row["days"] = [day for week in row["weeks"] for day in week["days"].values()]
        for day in row["days"]:
            previous = None
            for entry in day["entries"]:
                clock = entry["timeclock"]
                start, end = entry["rounded_clock_in"], entry["rounded_clock_out"]
                entry["work_date"] = day["date"]
                entry["in_warning"] = ""
                entry["out_warning"] = ""
                if not clock.valid:
                    continue
                if start and timezone.localtime(start).time() < time(8):
                    entry["in_warning"] = "Paid time before 8:00 AM"
                if end and timezone.localtime(end).time() > time(16, 30):
                    entry["out_warning"] = "Paid time after 4:30 PM"
                if previous and start and previous["rounded_clock_out"]:
                    gap = start - previous["rounded_clock_out"]
                    if timedelta(0) < gap < timedelta(minutes=30):
                        entry["in_warning"] = "Lunch break under 30 minutes"
                # A missing endpoint breaks adjacency; never guess a lunch gap.
                previous = entry
    totals = report["report_totals"]
    totals["regular_hours"] = totals["rounded_hours"] - totals["overtime_hours"]
    return report
