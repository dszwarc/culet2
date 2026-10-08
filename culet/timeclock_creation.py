"""Validation and persistence for completed, manually recorded clock events."""
from datetime import timezone as datetime_timezone

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Employee, TimeClock


def overlapping_timeclocks(employee_id, start, end=None):
    # Reserve both the original and corrected intervals, including invalidated
    # records. Missing endpoints are open bounds; entirely undated rows cannot
    # establish an interval. Adjacent intervals are allowed.
    clocks = TimeClock.objects.filter(employee_id=employee_id).annotate(
        effective_start=Coalesce("adjusted_clock_in", "clock_in"),
        effective_end=Coalesce("adjusted_clock_out", "clock_out"),
    )
    def intersects(start_field, end_field):
        condition = Q(**{end_field + "__isnull": True}) | Q(**{end_field + "__gt": start})
        if end is not None:
            condition &= Q(**{start_field + "__isnull": True}) | Q(**{start_field + "__lt": end})
        return condition & ~Q(**{start_field + "__isnull": True, end_field + "__isnull": True})
    return clocks.filter(
        intersects("clock_in", "clock_out") | intersects("effective_start", "effective_end")
    )


def validate_manual_timeclock(employee, start, end):
    if employee is None or start is None or end is None:
        raise ValidationError("Employee, clock-in and clock-out are required.")
    if timezone.is_naive(start) or timezone.is_naive(end):
        raise ValidationError("Clock timestamps must include a timezone.")
    if end.astimezone(datetime_timezone.utc) <= start.astimezone(datetime_timezone.utc):
        raise ValidationError("Clock-out must be strictly later than clock-in.")
    if overlapping_timeclocks(employee.pk, start, end).exists():
        raise ValidationError("This event duplicates or overlaps an existing clock interval for this employee.")


@transaction.atomic
def save_manual_timeclock(event):
    if event.pk is not None:
        raise ValidationError("Manual creation cannot modify an existing event.")
    Employee.objects.select_for_update().get(pk=event.employee_id)
    validate_manual_timeclock(event.employee, event.clock_in, event.clock_out)
    event.clock_in = event.clock_in.astimezone(datetime_timezone.utc)
    event.clock_out = event.clock_out.astimezone(datetime_timezone.utc)
    event.adjusted_clock_in = None
    event.adjusted_clock_out = None
    event.valid = True
    event.save()
    return event
