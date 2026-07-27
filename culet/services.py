from dataclasses import dataclass

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