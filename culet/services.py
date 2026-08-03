from dataclasses import dataclass
from datetime import timedelta
import re

from django.db import transaction
from django.utils import timezone

from .models import Activity, TimeClock

from django.core.exceptions import ValidationError

import logging


from .models import (
    Activity,
    Employee,
    Job,
    JobMovement,
    MovementType,
    TimeClock,
    WorkBatch,
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


@dataclass(frozen=True)
class ParsedBarcodeInput:
    values: list[str]
    duplicate_values: list[str]
    duplicate_count: int


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

    for job in jobs:
        identifier = job.stock_num or job.barcode
        if not job.active:
            errors.append(f"Job {identifier} is inactive.")
        elif job.shipped:
            errors.append(f"Job {identifier} has already been shipped.")
        elif job.is_piecework:
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
