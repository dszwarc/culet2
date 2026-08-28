from dataclasses import dataclass
from datetime import timedelta
import re

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from .models import Activity, TimeClock

from django.core.exceptions import ValidationError

import logging


from .models import (
    Activity,
    ActivityStep,
    Department,
    Employee,
    Job,
    JobStone,
    JobMovement,
    MovementType,
    PieceworkMemo,
    PieceworkMemoLine,
    TimeClock,
    WorkBatch,
)


PROGRESS_EXCLUDED_STEP_CODES = {"piecework", "repair"}
PROGRESS_DEPARTMENT_GROUPS = {
    "Jewelry": "Jewelry",
    "Polishing": "Polishing",
    "Polishing 37": "Polishing",
    "Setting": "Setting",
}
PROGRESS_GROUP_ORDER = ("Jewelry", "Polishing", "Setting")


def with_job_progress_data(queryset):
    """Prefetch the per-job facts needed by ``attach_job_progress``."""
    completed_activities = (
        Activity.objects.filter(
            active=False,
            end__isnull=False,
            step__isnull=False,
        )
        .select_related("step")
        .only(
            "job_id",
            "step_id",
            "end",
            "step__id",
            "step__name",
        )
    )
    open_activities = (
        Activity.objects.filter(
            active=True,
            end__isnull=True,
            step__isnull=False,
        )
        .select_related("step")
        .only(
            "job_id",
            "step_id",
            "start",
            "step__id",
            "step__name",
        )
    )

    return queryset.prefetch_related(
        Prefetch(
            "activity_set",
            queryset=completed_activities,
            to_attr="progress_completed_activities",
        ),
        Prefetch(
            "activity_set",
            queryset=open_activities,
            to_attr="progress_open_activities",
        ),
        Prefetch(
            "job_stones",
            queryset=JobStone.objects.only("id", "job_id"),
            to_attr="progress_job_stones",
        ),
    )


def get_progress_steps():
    """Return steps belonging to one of the supported logical progress groups."""
    return list(
        ActivityStep.objects.exclude(
            code__in=PROGRESS_EXCLUDED_STEP_CODES,
        )
        .filter(departments__name__in=PROGRESS_DEPARTMENT_GROUPS)
        .distinct()
        .prefetch_related(
            Prefetch(
                "departments",
                queryset=Department.objects.order_by("id"),
            )
        )
        .order_by("id")
    )


def get_job_progress(job, progress_steps=None):
    """Calculate distinct completed production steps grouped by department."""
    progress_steps = progress_steps if progress_steps is not None else get_progress_steps()

    if hasattr(job, "progress_completed_activities"):
        completed_activities = job.progress_completed_activities
        completed_step_ids = {
            activity.step_id
            for activity in completed_activities
        }
    else:
        completed_activities = list(
            job.activity_set.filter(
                active=False,
                end__isnull=False,
                step__isnull=False,
            ).select_related("step")
        )
        completed_step_ids = {
            activity.step_id
            for activity in completed_activities
        }

    if hasattr(job, "progress_open_activities"):
        open_activities = job.progress_open_activities
        open_step_ids = {
            activity.step_id
            for activity in open_activities
        }
    else:
        open_activities = list(
            job.activity_set.filter(
                active=True,
                end__isnull=True,
                step__isnull=False,
            ).select_related("step")
        )
        open_step_ids = {
            activity.step_id
            for activity in open_activities
        }

    # Once a step has ever been completed, a later repeat does not reduce it
    # from complete to in progress.
    open_step_ids -= completed_step_ids

    if hasattr(job, "progress_job_stones"):
        has_stones = bool(job.progress_job_stones)
    else:
        has_stones = job.job_stones.exists()

    grouped_steps = {
        group_name: set()
        for group_name in PROGRESS_GROUP_ORDER
    }
    for step in progress_steps:
        logical_groups = {
            PROGRESS_DEPARTMENT_GROUPS[department.name]
            for department in step.departments.all()
            if department.name in PROGRESS_DEPARTMENT_GROUPS
        }
        for group_name in logical_groups:
            grouped_steps[group_name].add(step.pk)

    groups = []
    for group_name in PROGRESS_GROUP_ORDER:
        if group_name == "Setting" and not has_stones:
            continue

        step_ids = grouped_steps[group_name]
        if not step_ids:
            continue

        completed = len(completed_step_ids.intersection(step_ids))
        in_progress = len(open_step_ids.intersection(step_ids))
        completion_ratio = completed / len(step_ids)
        latest_open_activity = max(
            (
                activity
                for activity in open_activities
                if activity.step_id in step_ids
            ),
            key=lambda activity: activity.start,
            default=None,
        )
        latest_completed_activity = max(
            (
                activity
                for activity in completed_activities
                if activity.step_id in step_ids
            ),
            key=lambda activity: activity.end,
            default=None,
        )
        display_activity = latest_open_activity or latest_completed_activity
        if completion_ratio < 0.34:
            progress_grade = "low"
        elif completion_ratio < 0.67:
            progress_grade = "medium"
        else:
            progress_grade = "high"
        groups.append({
            "name": group_name,
            "completed": completed,
            "in_progress": in_progress,
            "total": len(step_ids),
            "percent": round(completion_ratio * 100),
            "display_step": (
                display_activity.step.name
                if display_activity
                else group_name
            ),
            "is_in_progress": latest_open_activity is not None,
            "progress_grade": progress_grade,
            "segments": [
                {
                    "state": (
                        "completed"
                        if index < completed
                        else "active"
                        if index < completed + in_progress
                        else "pending"
                    )
                }
                for index in range(len(step_ids))
            ],
        })

    total_steps = sum(group["total"] for group in groups)
    completed_steps = sum(group["completed"] for group in groups)
    percent = round((completed_steps / total_steps) * 100) if total_steps else 0

    return {
        "completed_steps": completed_steps,
        "total_steps": total_steps,
        "percent": percent,
        "groups": groups,
    }


def attach_job_progress(jobs):
    """Attach reusable progress dictionaries to an already-fetched job page."""
    progress_steps = get_progress_steps()
    for job in jobs:
        job.production_progress = get_job_progress(job, progress_steps)
    return jobs

@dataclass
class ClockOutResult:
    clocked_out: bool
    stopped_activity_count: int = 0
    stopped_job_count: int = 0
    message: str = ""


@dataclass(frozen=True)
class JobHistoryEvent:
    event_type: str
    event_id: int
    timestamp: object
    record: object


def get_job_history(job):
    """Return normalized Activity and JobMovement events, newest first."""
    activities = (
        Activity.objects.filter(job=job)
        .select_related("employee__user", "step")
    )
    movements = (
        JobMovement.objects.filter(job=job)
        .select_related(
            "movement_type",
            "from_employee__user",
            "to_employee__user",
            "performed_by__user",
        )
    )
    events = [
        JobHistoryEvent("activity", activity.pk, activity.start, activity)
        for activity in activities
    ]
    events.extend(
        JobHistoryEvent("movement", movement.pk, movement.created_at, movement)
        for movement in movements
    )
    return sorted(
        events,
        key=lambda event: (
            event.timestamp,
            event.event_type == "movement",
            event.event_id,
        ),
        reverse=True,
    )


def get_job_history_page(job, offset=0, limit=10):
    events = get_job_history(job)
    page = events[offset:offset + limit]
    return page, offset + len(page) < len(events), offset + len(page)


@dataclass
class ClockInResult:
    clocked_in: bool
    created_clock: bool
    message: str = ""


@dataclass(frozen=True)
class ParsedBarcodeInput:
    values: list[str]
    duplicate_values: list[str]
    duplicate_count: int


@dataclass(frozen=True)
class PieceworkReturnResult:
    returned_count: int
    remaining_count: int
    memo_completed: bool
    returned_at: object


def parse_barcode_input(raw_text):
    """Parse common barcode delimiters and deduplicate in first-seen order."""
    values = []
    seen = set()
    duplicate_values = []
    duplicate_values_seen = set()
    duplicate_count = 0

    for token in re.split(r"[\s,;]+", raw_text or ""):
        barcode = token.strip()
        if not barcode:
            continue

        if barcode in seen:
            duplicate_count += 1
            if barcode not in duplicate_values_seen:
                duplicate_values.append(barcode)
                duplicate_values_seen.add(barcode)
            continue

        seen.add(barcode)
        values.append(barcode)

    return ParsedBarcodeInput(
        values=values,
        duplicate_values=duplicate_values,
        duplicate_count=duplicate_count,
    )


def stop_activity(activity, stopped_at=None):
    """
    Close one Activity and synchronize Job.in_work with the remaining
    open activities for that job.
    """
    if activity.batch_id and activity.batch.active:
        raise ValidationError(
            "This activity is part of an active batch. Stop the batch instead."
        )

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


def validate_batch_jobs(*, employee, jobs, step):
    errors = []
    jobs = list(jobs)

    if not employee.can_start_batch:
        errors.append("You do not have permission to start batch work.")

    if len(jobs) < 2:
        errors.append("A batch must contain at least two distinct jobs.")
    elif len({job.pk for job in jobs}) != len(jobs):
        errors.append("A batch cannot contain the same job more than once.")

    if not employee.clocked_in:
        errors.append("Please clock in before starting batch work.")

    if not employee.department or not step.departments.filter(
        pk=employee.department_id,
    ).exists():
        errors.append("That activity step is not available for your department.")
    elif step.code == "piecework":
        errors.append("Piecework cannot be selected for batch work.")

    if WorkBatch.objects.filter(employee=employee, active=True).exists():
        errors.append("You already have an active batch.")

    if Activity.objects.filter(
        employee=employee,
        active=True,
        end__isnull=True,
        batch__isnull=True,
    ).exists():
        errors.append("Stop your active individual work before starting a batch.")

    open_job_ids = set(
        Activity.objects.filter(
            job_id__in=[job.pk for job in jobs],
            active=True,
            end__isnull=True,
        ).values_list("job_id", flat=True)
    )
    open_piecework_job_ids = set(
        PieceworkMemoLine.objects.filter(
            job_id__in=[job.pk for job in jobs],
            returned_at__isnull=True,
        ).values_list("job_id", flat=True)
    )

    for job in jobs:
        identifier = job.stock_num or job.barcode
        if not job.active:
            errors.append(f"Job {identifier} is inactive.")
        elif job.shipped:
            errors.append(f"Job {identifier} has already been shipped.")
        elif job.pk in open_piecework_job_ids:
            errors.append(f"Job {identifier} is assigned as piecework.")
        elif job.assigned_to_id != employee.pk:
            errors.append(f"Job {identifier} is not assigned to you.")
        elif job.holder_id != employee.pk:
            errors.append(f"Job {identifier} must be received before starting work.")
        elif job.pk in open_job_ids:
            errors.append(f"Job {identifier} already has active work.")

    return errors


@transaction.atomic
def start_work_batch(*, employee, jobs, step, started_at=None):
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    job_ids = [job.pk for job in jobs]
    locked_jobs_by_id = {
        job.pk: job
        for job in Job.objects.select_for_update().filter(pk__in=job_ids)
    }

    if len(locked_jobs_by_id) != len(set(job_ids)):
        raise ValidationError("One or more selected jobs no longer exist.")

    locked_jobs = [locked_jobs_by_id[job_id] for job_id in job_ids]
    errors = validate_batch_jobs(employee=employee, jobs=locked_jobs, step=step)
    if errors:
        raise ValidationError(errors)

    shared_start = started_at or timezone.now()
    batch = WorkBatch.objects.create(
        employee=employee,
        step=step,
        started_at=shared_start,
    )
    Activity.objects.bulk_create(
        [
            Activity(
                job=job,
                employee=employee,
                step=step,
                name=step.name,
                start=shared_start,
                active=True,
                batch=batch,
            )
            for job in locked_jobs
        ]
    )
    Job.objects.filter(pk__in=job_ids).update(
        in_work=True,
        last_updated=shared_start,
    )
    return batch


@transaction.atomic
def stop_work_batch(*, batch, stopped_at=None):
    batch = WorkBatch.objects.select_for_update().get(pk=batch.pk)
    if not batch.active:
        return batch

    activities = list(
        batch.activities.select_for_update()
        .filter(active=True, end__isnull=True)
        .order_by("pk")
    )
    if not activities:
        raise ValidationError("The active batch has no open activities.")

    shared_stop = stopped_at or timezone.now()
    elapsed = shared_stop - batch.started_at
    total_microseconds = (
        elapsed.days * 86_400_000_000
        + elapsed.seconds * 1_000_000
        + elapsed.microseconds
    )
    if total_microseconds < 0:
        raise ValidationError("Batch stop time cannot precede its start time.")

    base_microseconds, remainder = divmod(total_microseconds, len(activities))
    for index, activity in enumerate(activities):
        allocated_microseconds = base_microseconds + (index < remainder)
        activity.end = shared_stop
        activity.duration = timedelta(microseconds=allocated_microseconds)
        activity.active = False

    Activity.objects.bulk_update(activities, ["end", "duration", "active"])
    job_ids = [activity.job_id for activity in activities]
    Job.objects.filter(pk__in=job_ids).update(
        in_work=False,
        last_updated=shared_stop,
    )

    batch.stopped_at = shared_stop
    batch.active = False
    batch.save(update_fields=["stopped_at", "active"])
    return batch


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

    active_batch = WorkBatch.objects.filter(
        employee=employee,
        active=True,
    ).first()
    batch_activity_count = 0
    batch_job_count = 0
    if active_batch:
        batch_activity_count = active_batch.activities.filter(
            active=True,
            end__isnull=True,
        ).count()
        batch_job_count = batch_activity_count
        stop_work_batch(batch=active_batch, stopped_at=now)

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
        batch__isnull=True,
    )

    stopped_count = batch_activity_count
    stopped_job_count = (
        batch_job_count
        + open_activities.values("job_id").distinct().count()
    )
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

    if (
        to_employee is not None
        and not isinstance(to_employee, Employee)
    ):
        raise ValidationError(
            "to_employee must be an Employee instance or None."
        )

    if (
        performed_by is not None
        and not isinstance(performed_by, Employee)
    ):
        raise ValidationError(
            "performed_by must be an Employee instance or None."
        )

    # Lock only the Job row.
    #
    # Do not use select_related("assigned_to", "holder") here.
    # Those fields are nullable, so PostgreSQL creates outer joins and
    # cannot apply FOR UPDATE to the nullable side of those joins.
    job = (
        Job.objects
        .select_for_update(of=("self",))
        .get(pk=job.pk)
    )

    job_field = movement_type.job_field

    valid_job_fields = {
        MovementType.JobField.ASSIGNED_TO,
        MovementType.JobField.HOLDER,
    }

    if job_field not in valid_job_fields:
        raise ValidationError(
            f'Movement type "{movement_type}" has unsupported '
            f'job field "{job_field}".'
        )

    from_employee = getattr(
        job,
        job_field,
    )

    if from_employee == to_employee:
        return job, None

    open_activity = (
        Activity.objects
        .filter(
            job=job,
            active=True,
            end__isnull=True,
        )
        .select_related("employee__user", "step")
        .order_by("start", "pk")
        .first()
    )

    if open_activity is not None:
        employee_name = str(open_activity.employee)
        activity_name = (
            open_activity.step.name
            if open_activity.step_id
            else open_activity.name
        )
        raise ValidationError(
            f"This job is currently being worked on by {employee_name} "
            f"({activity_name}) and cannot be moved until that activity "
            "is stopped."
        )

    setattr(
        job,
        job_field,
        to_employee,
    )

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


@transaction.atomic
def return_piecework_lines(
    *,
    memo,
    line_ids,
    returned_by,
    return_to=None,
    returned_at=None,
):
    """Return selected open lines from one piecework memo atomically."""
    return_to = return_to or returned_by

    try:
        submitted_ids = [int(line_id) for line_id in line_ids]
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "One or more selected piecework lines are invalid. Refresh and try again."
        ) from exc

    if not submitted_ids:
        raise ValidationError("Select at least one job to return.")

    if len(submitted_ids) != len(set(submitted_ids)):
        raise ValidationError(
            "The same piecework line was submitted more than once. Refresh and try again."
        )

    memo_id = memo.pk if isinstance(memo, PieceworkMemo) else memo
    try:
        locked_memo = PieceworkMemo.objects.select_for_update().get(pk=memo_id)
    except PieceworkMemo.DoesNotExist as exc:
        raise ValidationError("This piecework memo no longer exists.") from exc

    if locked_memo.returned_at is not None:
        raise ValidationError(
            f"Piecework memo {locked_memo.memo_num} is already complete.",
            code="memo_complete",
        )

    # Lock base line rows without joining nullable relations.
    locked_lines = list(
        PieceworkMemoLine.objects.select_for_update()
        .filter(pk__in=submitted_ids)
        .order_by("pk")
    )
    lines_by_id = {line.pk: line for line in locked_lines}

    errors = []
    missing_ids = sorted(set(submitted_ids) - set(lines_by_id))
    if missing_ids:
        errors.append(
            "These selected piecework lines no longer exist: "
            + ", ".join(str(line_id) for line_id in missing_ids)
            + "."
        )

    wrong_memo_ids = [
        line.pk for line in locked_lines if line.memo_id != locked_memo.pk
    ]
    if wrong_memo_ids:
        errors.append(
            "These selected lines do not belong to this memo: "
            + ", ".join(str(line_id) for line_id in wrong_memo_ids)
            + "."
        )

    returned_lines = [
        line for line in locked_lines if line.returned_at is not None
    ]
    if returned_lines:
        errors.append(
            "These selected jobs have already been returned: "
            + ", ".join(str(line.job_id) for line in returned_lines)
            + ". Refresh the page before trying again."
        )

    if errors:
        raise ValidationError(errors)

    job_ids = [line.job_id for line in locked_lines]
    locked_jobs = {
        job.pk: job
        for job in Job.objects.select_for_update().filter(pk__in=job_ids)
    }
    missing_job_ids = sorted(set(job_ids) - set(locked_jobs))
    if missing_job_ids:
        raise ValidationError(
            "These piecework lines no longer have valid jobs: "
            + ", ".join(str(job_id) for job_id in missing_job_ids)
            + "."
        )

    conflict_messages = []
    for job in locked_jobs.values():
        identifier = job.stock_num or job.barcode or job.pk
        if job.shipped:
            conflict_messages.append(
                f"Job {identifier} has been shipped and cannot be returned from piecework."
            )

    active_job_ids = set(
        Activity.objects.filter(
            job_id__in=job_ids,
            active=True,
            end__isnull=True,
        ).values_list("job_id", flat=True)
    )
    for job_id in sorted(active_job_ids):
        job = locked_jobs[job_id]
        identifier = job.stock_num or job.barcode or job.pk
        conflict_messages.append(
            f"Job {identifier} has active work that must be resolved before return."
        )

    if conflict_messages:
        raise ValidationError(conflict_messages)

    try:
        piecework_step = ActivityStep.objects.get(code="piecework")
        assignment_return_type = MovementType.objects.get(
            code="returned-to-manager"
        )
        holder_return_type = MovementType.objects.get(code="returned")
    except (ActivityStep.DoesNotExist, MovementType.DoesNotExist) as exc:
        raise ValidationError(
            "Piecework return reference data is incomplete. Contact an administrator."
        ) from exc

    operation_time = returned_at or timezone.now()

    for line in locked_lines:
        job = locked_jobs[line.job_id]

        Activity.objects.create(
            job=job,
            employee=locked_memo.assigned_to,
            step=piecework_step,
            start=locked_memo.created_at,
            end=operation_time,
            duration=operation_time - locked_memo.created_at,
            is_piecework=True,
            active=False,
        )

        job, _ = move_job(
            job=job,
            movement_type=assignment_return_type,
            to_employee=return_to,
            performed_by=returned_by,
        )
        job, _ = move_job(
            job=job,
            movement_type=holder_return_type,
            to_employee=return_to,
            performed_by=returned_by,
        )

        job.is_piecework = False
        job.in_work = False
        job.piecework_assigned_at = None
        job.save(
            update_fields=[
                "is_piecework",
                "in_work",
                "piecework_assigned_at",
                "last_updated",
            ]
        )

        line.returned_at = operation_time
        line.returned_by = returned_by
        line.save(update_fields=["returned_at", "returned_by"])

    remaining_count = PieceworkMemoLine.objects.filter(
        memo_id=locked_memo.pk,
        returned_at__isnull=True,
    ).count()
    memo_completed = remaining_count == 0

    if memo_completed:
        locked_memo.returned_at = operation_time
        locked_memo.returned_by = returned_by
        locked_memo.save(update_fields=["returned_at", "returned_by"])
    elif locked_memo.returned_at is not None or locked_memo.returned_by_id is not None:
        locked_memo.returned_at = None
        locked_memo.returned_by = None
        locked_memo.save(update_fields=["returned_at", "returned_by"])

    return PieceworkReturnResult(
        returned_count=len(locked_lines),
        remaining_count=remaining_count,
        memo_completed=memo_completed,
        returned_at=operation_time,
    )

logger = logging.getLogger("culet")


SENSITIVE_POST_KEYS = {
    "password",
    "password1",
    "password2",
    "old_password",
    "new_password1",
    "new_password2",
    "csrfmiddlewaretoken",
    "pin",
}


def get_request_log_context(request):
    """
    Return safe, reusable request information for log records.

    Does not include full POST data or passwords.
    """
    user = getattr(request, "user", None)

    if user and user.is_authenticated:
        user_id = user.pk
        username = user.get_username()
    else:
        user_id = None
        username = "anonymous"

    return {
        "method": request.method,
        "path": request.path,
        "user_id": user_id,
        "username": username,
        "remote_addr": request.META.get("REMOTE_ADDR"),
        "user_agent": request.META.get(
            "HTTP_USER_AGENT",
            "",
        )[:250],
    }


def get_safe_post_data(request):
    """
    Return submitted field names and safe values for debugging.

    Sensitive fields are replaced rather than logged.
    """
    safe_data = {}

    for key in request.POST.keys():
        values = request.POST.getlist(key)

        if key.lower() in SENSITIVE_POST_KEYS:
            safe_data[key] = "[REDACTED]"
            continue

        if len(values) == 1:
            safe_data[key] = values[0]
        else:
            safe_data[key] = values

    return safe_data


def serialize_form_errors(form):
    """
    Convert a bound Django form's errors into log-friendly data.
    """
    if form is None:
        return {}

    return {
        "fields": form.errors.get_json_data(),
        "non_field_errors": [
            str(error)
            for error in form.non_field_errors()
        ],
    }


def serialize_formset_errors(formset):
    """
    Convert formset and individual-row errors into log-friendly data.
    """
    if formset is None:
        return {}

    row_errors = []

    for index, form in enumerate(formset.forms):
        if not form.errors and not form.non_field_errors():
            continue

        row_errors.append(
            {
                "row": index,
                "errors": form.errors.get_json_data(),
                "non_field_errors": [
                    str(error)
                    for error in form.non_field_errors()
                ],
                "marked_for_deletion": bool(
                    form.cleaned_data.get("DELETE")
                    if hasattr(form, "cleaned_data")
                    else False
                ),
            }
        )

    return {
        "prefix": formset.prefix,
        "total_forms": formset.total_form_count(),
        "initial_forms": formset.initial_form_count(),
        "non_form_errors": [
            str(error)
            for error in formset.non_form_errors()
        ],
        "rows": row_errors,
    }


def log_validation_failure(
    *,
    request,
    view_name,
    form=None,
    formsets=None,
    extra=None,
):
    """
    Log invalid forms and formsets consistently across Culet views.

    Usage:
        log_validation_failure(
            request=request,
            view_name="JobCreateView",
            form=form,
            formsets={
                "metals": metal_formset,
                "stones": stone_formset,
            },
        )
    """
    formsets = formsets or {}
    extra = extra or {}

    log_data = {
        "request": get_request_log_context(request),
        "form_errors": serialize_form_errors(form),
        "formset_errors": {
            name: serialize_formset_errors(formset)
            for name, formset in formsets.items()
        },
        "extra": extra,
    }

    logger.warning(
        "%s validation failed: %s",
        view_name,
        log_data,
    )


def log_view_exception(
    *,
    request,
    view_name,
    exception,
    extra=None,
):
    """
    Log an unexpected view exception with its full traceback.

    Call only from inside an except block.
    """
    logger.exception(
        "%s raised an unexpected exception. "
        "request=%s extra=%s exception=%s",
        view_name,
        get_request_log_context(request),
        extra or {},
        exception,
    )
