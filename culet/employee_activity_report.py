"""Read-only labor reporting using recorded durations and local calendar days."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone as datetime_timezone

from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Activity, TimeClock

MINUTE_US = 60_000_000
UTC = datetime_timezone.utc


def microseconds(duration):
    return (duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds


def local_midnight(day, tz):
    return timezone.make_aware(datetime.combine(day, time.min), tz).astimezone(UTC)


def day_slices(start, end, lower, upper, tz):
    """Yield clipped UTC intervals bounded by local midnights (including DST)."""
    cursor, stop = max(start, lower), min(end, upper)
    while cursor < stop:
        day = cursor.astimezone(tz).date()
        boundary = min(stop, local_midnight(day + timedelta(days=1), tz))
        yield day, cursor, boundary
        cursor = boundary


def minutes_label(minutes):
    sign = "−" if minutes < 0 else ""
    hours, remaining = divmod(abs(minutes), 60)
    if hours:
        return f"{sign}{hours}h {remaining:02d}m"
    return f"{sign}{remaining}m"


def assign_display_minutes(entries):
    """Distribute sub-minute remainders so displayed rows and totals reconcile."""
    for entry in entries:
        entry["minutes"] = entry["duration_us"] // MINUTE_US
    target = (sum(entry["duration_us"] for entry in entries) + MINUTE_US // 2) // MINUTE_US
    extra = target - sum(entry["minutes"] for entry in entries)
    ranked = sorted(entries, key=lambda entry: entry["duration_us"] % MINUTE_US, reverse=True)
    for entry in ranked[:extra]:
        entry["minutes"] += 1
    for entry in entries:
        entry["duration_label"] = minutes_label(entry["minutes"])
    return target


def build_employee_activity_report(*, start_date, end_date, employee=None, style=None):
    tz = timezone.get_current_timezone()
    lower = local_midnight(start_date, tz)
    upper = local_midnight(end_date + timedelta(days=1), tz)
    activities = Activity.objects.filter(
        end__isnull=False, start__lt=upper, end__gte=lower,
    ).select_related("employee__user", "employee__department", "job__style", "step").order_by("start", "pk")
    if employee:
        activities = activities.filter(employee=employee)
    if style:
        activities = activities.filter(job__style=style)

    employees = {}
    for activity in activities:
        start, end = activity.start.astimezone(UTC), activity.end.astimezone(UTC)
        if end == lower and start < end:
            continue  # An interval ending exactly at the selected boundary contributes nothing.
        row = employees.setdefault(activity.employee_id, {
            "employee": activity.employee, "days_by_date": {}, "active_us": 0,
            "clocked_us": 0, "clock_days": defaultdict(int), "invalid_activities": 0,
            "piecework_count": 0, "historical_duration_count": 0,
            "overlapping_clocks": 0, "invalid_clocks": 0,
        })
        elapsed = microseconds(end - start)
        recorded = None if activity.duration is None else microseconds(activity.duration)
        if elapsed < 0 or (not activity.is_piecework and (
            recorded is None or recorded < 0 or (elapsed == 0 and recorded != 0)
        )):
            row["invalid_activities"] += 1
            continue
        if activity.is_piecework:
            row["piecework_count"] += 1
        elif recorded != elapsed and not activity.batch_id:
            row["historical_duration_count"] += 1
        slices = list(day_slices(start, end, lower, upper, tz))
        if start == end and lower <= start < upper:
            slices = [(start.astimezone(tz).date(), start, end)]
        for day_date, segment_start, segment_end in slices:
            day = row["days_by_date"].setdefault(day_date, {
                "date": day_date, "entries": [], "active_us": 0,
            })
            allocated = 0
            if not activity.is_piecework and elapsed:
                # Cumulative integer allocation preserves batch shares and exact totals.
                allocated = (recorded * microseconds(segment_end - start) // elapsed
                             - recorded * microseconds(segment_start - start) // elapsed)
            entry = {
                "activity": activity, "duration_us": allocated,
                "piecework": activity.is_piecework,
                "continued": segment_start > start,
            }
            day["entries"].append(entry)
            day["active_us"] += allocated
            row["active_us"] += allocated

    clocks = TimeClock.objects.filter(employee_id__in=employees, valid=True).annotate(
        effective_in=Coalesce("adjusted_clock_in", "clock_in"),
        effective_out=Coalesce("adjusted_clock_out", "clock_out"),
    ).filter(
        effective_in__isnull=False, effective_out__isnull=False,
        effective_in__lt=upper, effective_out__gt=lower,
    ).order_by("employee_id", "effective_in", "pk")
    intervals = defaultdict(list)
    for clock in clocks:
        start, end = clock.effective_in.astimezone(UTC), clock.effective_out.astimezone(UTC)
        row = employees[clock.employee_id]
        if end <= start:
            row["invalid_clocks"] += 1
            continue
        start, end = max(start, lower), min(end, upper)
        merged = intervals[clock.employee_id]
        if merged and start < merged[-1][1]:
            row["overlapping_clocks"] += 1
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    for employee_id, merged in intervals.items():
        row = employees[employee_id]
        for start, end in merged:
            for day, segment_start, segment_end in day_slices(start, end, lower, upper, tz):
                duration = microseconds(segment_end - segment_start)
                row["clock_days"][day] += duration
                row["clocked_us"] += duration

    rows = list(employees.values())
    rows.sort(key=lambda row: (
        row["employee"].user.last_name.casefold(), row["employee"].user.first_name.casefold(),
        row["employee"].user.username.casefold(), row["employee"].pk,
    ))
    for row in rows:
        row["days"] = sorted(row.pop("days_by_date").values(), key=lambda day: day["date"])
        entries = [entry for day in row["days"] for entry in day["entries"] if not entry["piecework"]]
        active_minutes = assign_display_minutes(entries)
        for day in row["days"]:
            day["duration_label"] = minutes_label(sum(
                entry["minutes"] for entry in day["entries"] if not entry["piecework"]
            ))
        clock_minutes = (row["clocked_us"] + MINUTE_US // 2) // MINUTE_US
        row.update(
            active_label=minutes_label(active_minutes), clocked_label=minutes_label(clock_minutes),
            difference_us=row["clocked_us"] - row["active_us"],
            difference_label=minutes_label(clock_minutes - active_minutes),
            activity_percentage=(100 * row["active_us"] / row["clocked_us"] if row["clocked_us"] else None),
        )
    return {"employee_rows": rows, "report_timezone": str(tz)}
