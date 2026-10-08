"""Shipping operations; disagreements require review, never implicit repair."""
from django.core.exceptions import ValidationError
from django.db import connection, transaction

from .models import Activity, Job, JobShip, JobStatus, PieceworkMemoLine
from .services import move_job


def save_job_form_fields(form):
    """Do not write stale lifecycle fields excluded from the editing form."""
    job = form.save(commit=False)
    fields = [field.name for field in Job._meta.concrete_fields
              if not field.primary_key and field.name in form.fields]
    job.save(update_fields=[*fields, "last_updated"])
    return job


@transaction.atomic
def ship_jobs(*, job_ids, employee, notes=""):
    if not job_ids:
        raise ValidationError("Enter at least one job to ship.")
    if len(set(job_ids)) != len(job_ids):
        raise ValidationError("The same job was entered more than once.")
    jobs = list(Job.objects.select_for_update().filter(
        pk__in=job_ids,
    ).order_by("pk"))
    if len(jobs) != len(job_ids):
        raise ValidationError("A selected job no longer exists. Refresh and try again.")
    shipments = set(JobShip.objects.filter(job_id__in=job_ids).values_list("job_id", flat=True))
    working = set(Activity.objects.filter(
        job_id__in=job_ids, active=True, end__isnull=True,
    ).values_list("job_id", flat=True))
    piecework = set(PieceworkMemoLine.objects.filter(
        job_id__in=job_ids, returned_at__isnull=True,
    ).values_list("job_id", flat=True))
    errors = []
    for job in jobs:
        label = str(job.stock_num or job.barcode or f"Job {job.pk}")
        if job.shipped:
            # Legacy shipped jobs need not have a shipment row.
            errors.append(f"Already shipped job: {label}.")
        elif job.pk in shipments:
            errors.append(
                f"Shipping-state conflict for {label}: a shipment already exists "
                "but the job is marked unshipped. Ask a manager to review it; "
                "the existing shipment has not been changed."
            )
        if job.pk in working:
            errors.append(f"Job {label} is currently being worked on and must be stopped before shipping.")
        if job.pk in piecework:
            errors.append(f"Job {label} is still out for piecework and cannot be shipped.")
    if errors:
        raise ValidationError(errors)
    try:
        status = JobStatus.objects.get(name__iexact="Shipped")
    except (JobStatus.DoesNotExist, JobStatus.MultipleObjectsReturned):
        raise ValidationError("Shipping status configuration needs administrator review.")
    for job in jobs:
        job, _ = move_job(job=job, movement_type="shipped-unassigned",
                          to_employee=None, performed_by=employee)
        job, _ = move_job(job=job, movement_type="shipped-released",
                          to_employee=None, performed_by=employee)
        job.shipped = True
        job.active = False
        job.in_work = False
        job.status = status
        job.save(update_fields=["shipped", "active", "in_work", "status", "last_updated"])
        JobShip.objects.create(job=job, shipped_by=employee, notes=notes)
    return len(jobs)


def is_shipment_uniqueness_conflict(exc):
    """Recognize only the PostgreSQL unique constraint on JobShip.job."""
    cause = exc.__cause__
    if getattr(cause, "sqlstate", getattr(cause, "pgcode", None)) != "23505":
        return False
    diagnostic = getattr(cause, "diag", None)
    if getattr(diagnostic, "table_name", None) != JobShip._meta.db_table:
        return False
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, JobShip._meta.db_table)
    constraint = constraints.get(getattr(diagnostic, "constraint_name", None), {})
    return bool(constraint.get("unique") and constraint.get("columns") == ["job_id"])
