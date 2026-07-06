from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from .models import Activity, TimeClock


@dataclass
class ClockOutResult:
    clocked_out: bool
    stopped_activity_count: int = 0
    stopped_job_count: int = 0
    message: str = ""


@dataclass
class ClockInResult:
    clocked_in: bool
    created_clock: bool
    message: str = ""


def stop_activity(activity, stopped_at=None):
    """
    Stops one open Activity and marks the related Job as not in work.
    Matches your current app fields: active/end/start/duration.
    """
    stopped_at = stopped_at or timezone.now()

    activity.end = stopped_at
    activity.active = False

    if hasattr(activity, "duration") and activity.start:
        activity.duration = activity.end - activity.start

    activity.save()

    if activity.job:
        activity.job.in_work = False
        activity.job.save()

    return activity


@transaction.atomic
def clock_in_employee(employee):
    """
    Clocks an employee in if they are not already clocked in.
    Keeps Employee.clocked_in and TimeClock in sync.
    """
    open_clock = TimeClock.objects.filter(
        employee=employee,
        clock_out__isnull=True,
    ).first()

    if open_clock or employee.clocked_in:
        employee.clocked_in = True
        employee.save(update_fields=["clocked_in"])

        return ClockInResult(
            clocked_in=True,
            created_clock=False,
            message="Already clocked in.",
        )

    TimeClock.objects.create(
        employee=employee,
        clock_in=timezone.now(),
    )

    employee.clocked_in = True
    employee.save(update_fields=["clocked_in"])

    return ClockInResult(
        clocked_in=True,
        created_clock=True,
        message="You have been clocked in.",
    )


@transaction.atomic
def clock_out_employee(employee):
    """
    Clocks employee out and stops all open activities.
    This is the shared logic we can later reuse for logout.
    """
    now = timezone.now()

    open_clock = (
        TimeClock.objects
        .filter(employee=employee, clock_out__isnull=True)
        .order_by("-clock_in")
        .first()
    )

    open_activities = Activity.objects.filter(
        employee=employee,
        active=True,
        end__isnull=True,
    )

    stopped_count = 0
    stopped_job_count = open_activities.values("job_id").distinct().count()
    for activity in open_activities:
        stop_activity(activity, stopped_at=now)
        stopped_count += 1

    if open_clock:
        open_clock.clock_out = now
        open_clock.save()

    employee.clocked_in = False
    employee.save(update_fields=["clocked_in"])

    if stopped_job_count:
        message = f"You have been clocked out. {stopped_job_count} active job(s) were stopped."
    else:
        message = "You have been clocked out."

    return ClockOutResult(
        clocked_out=True,
        stopped_activity_count=stopped_count,
        stopped_job_count=stopped_job_count,
        message=message,
    )