from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from .models import Activity, TimeClock

from django.core.exceptions import ValidationError

from .models import (
    Activity,
    Employee,
    Job,
    JobMovement,
    MovementType,
    TimeClock,
)

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
    Close one Activity and synchronize Job.in_work with the remaining
    open activities for that job.
    """
    stopped_at = stopped_at or timezone.now()

    activity.end = stopped_at
    activity.active = False

    if activity.start:
        activity.duration = stopped_at - activity.start

    activity.save(
        update_fields=[
            "end",
            "active",
            "duration",
        ]
    )

    job = activity.job

    if job:
        still_in_work = Activity.objects.filter(
            job=job,
            active=True,
            end__isnull=True,
        ).exists()

        if job.in_work != still_in_work:
            job.in_work = still_in_work
            job.save(update_fields=["in_work", "last_updated"])

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

def sync_job_in_work(job):
    """
    Synchronize Job.in_work with whether the job has any open activities.
    Job.active is intentionally untouched.
    """
    should_be_in_work = Activity.objects.filter(
        job=job,
        active=True,
        end__isnull=True,
    ).exists()

    if job.in_work != should_be_in_work:
        job.in_work = should_be_in_work
        job.save(update_fields=["in_work", "last_updated"])

    return should_be_in_work

@transaction.atomic
def move_job(
    *,
    job,
    movement_type,
    to_employee,
    performed_by,
):
    """
    Change either Job.assigned_to or Job.holder and record the change.

    movement_type may be:
        - a MovementType instance
        - a MovementType code/slug

    Returns:
        (job, movement)

    If the employee field already has the requested value, no movement
    is created and movement will be None.
    """

    if isinstance(movement_type, str):
        try:
            movement_type = MovementType.objects.get(
                code=movement_type,
            )
        except MovementType.DoesNotExist as exc:
            raise ValidationError(
                f'Unknown movement type code: "{movement_type}".'
            ) from exc

    if not isinstance(movement_type, MovementType):
        raise ValidationError(
            "movement_type must be a MovementType instance or code."
        )

    if to_employee is not None and not isinstance(to_employee, Employee):
        raise ValidationError(
            "to_employee must be an Employee instance or None."
        )

    if performed_by is not None and not isinstance(
        performed_by,
        Employee,
    ):
        raise ValidationError(
            "performed_by must be an Employee instance or None."
        )

    job = (
        Job.objects
        .select_for_update()
        .select_related(
            "assigned_to",
            "holder",
        )
        .get(pk=job.pk)
    )

    job_field = movement_type.job_field

    if job_field not in {
        MovementType.JobField.ASSIGNED_TO,
        MovementType.JobField.HOLDER,
    }:
        raise ValidationError(
            f'Movement type "{movement_type}" has unsupported '
            f'job field "{job_field}".'
        )

    from_employee = getattr(job, job_field)

    if from_employee == to_employee:
        return job, None

    setattr(job, job_field, to_employee)

    job.save(
        update_fields=[
            job_field,
            "last_updated",
        ]
    )

    movement = JobMovement.objects.create(
        job=job,
        movement_type=movement_type,
        from_employee=from_employee,
        to_employee=to_employee,
        performed_by=performed_by,
    )

    return job, movement
