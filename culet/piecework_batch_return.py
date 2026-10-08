"""Atomic multi-memo coordination around the existing line-return service."""
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from .models import Job, JobShip, PieceworkMemo, PieceworkMemoLine
from .services import _prepare_piecework_return, return_piecework_lines


def line_snapshot(line):
    return {
        "line": line.pk, "job": line.job_id, "memo": line.memo_id,
        "operation": line.memo.activity_step_id,
        "assigned_to": line.memo.assigned_to_id,
        "created_at": line.memo.created_at.isoformat(),
    }


@transaction.atomic
def process_piecework_batch(*, barcodes, returned_by, confirmed=None):
    """Preview if confirmed is None; otherwise return only the confirmed lines."""
    jobs = list(Job.objects.filter(barcode__in=barcodes))
    jobs_by_barcode = {str(job.barcode): job for job in jobs}
    errors = [f"Barcode {barcode}: no job exists." for barcode in barcodes if barcode not in jobs_by_barcode]
    job_ids = [job.pk for job in jobs]
    candidates = list(PieceworkMemoLine.objects.filter(
        job_id__in=job_ids, returned_at__isnull=True,
    ).values("id", "memo_id", "job_id"))
    memo_ids = sorted({line["memo_id"] for line in candidates})
    line_ids = sorted(line["id"] for line in candidates)
    # Match individual returns: memo -> line -> job, each in consistent PK order.
    list(PieceworkMemo.objects.select_for_update().filter(pk__in=memo_ids).order_by("pk"))
    list(PieceworkMemoLine.objects.select_for_update().filter(pk__in=line_ids).order_by("pk"))
    locked_jobs = {
        job.pk: job for job in Job.objects.select_for_update().filter(pk__in=job_ids).order_by("pk")
    }
    lines = list(PieceworkMemoLine.objects.filter(pk__in=line_ids).select_related(
        "memo__activity_step", "job",
    ).order_by("pk"))
    by_job = {line.job_id: line for line in lines}
    current_open = dict(PieceworkMemoLine.objects.filter(
        job_id__in=job_ids, returned_at__isnull=True,
    ).values_list("job_id", "pk"))
    historical_jobs = set(PieceworkMemoLine.objects.filter(job_id__in=job_ids).values_list("job_id", flat=True))
    shipped_jobs = set(JobShip.objects.filter(job_id__in=job_ids).values_list("job_id", flat=True))
    for original in jobs:
        job = locked_jobs.get(original.pk)
        label = f"Barcode {original.barcode} (stock {original.stock_num or 'not recorded'})"
        if job is None:
            errors.append(f"{label}: job no longer exists.")
            continue
        if job.barcode != original.barcode:
            errors.append(f"{label}: barcode changed during lookup. Review the scan list again.")
        if not job.active:
            errors.append(f"{label}: job is inactive.")
        if job.shipped or job.pk in shipped_jobs:
            errors.append(f"{label}: job is shipped or has an existing shipment.")
        line = by_job.get(job.pk)
        if line is None:
            reason = "already returned; no open piecework line" if job.pk in historical_jobs else "not on a piecework memo"
            errors.append(f"{label}: {reason}.")
        elif line.returned_at is not None or current_open.get(job.pk) != line.pk:
            errors.append(f"{label}: piecework assignment changed or was already returned. Review the scans again.")
        elif line.memo.returned_at is not None:
            errors.append(f"{label}: memo {line.memo.memo_num} is already complete; review its inconsistent open line.")
    if errors:
        raise ValidationError(errors)
    if not lines:
        raise ValidationError("Scan at least one job barcode.")
    snapshot = [line_snapshot(line) for line in lines]
    if confirmed is not None and snapshot != confirmed:
        labels = ", ".join(
            f"barcode {line.job.barcode} (stock {line.job.stock_num or 'not recorded'})"
            for line in lines
        )
        raise ValidationError(
            f"The piecework assignment or memo operation changed after confirmation for {labels}. "
            "Nothing was returned. Review the scan list again."
        )

    groups = defaultdict(list)
    for line in lines:
        groups[line.memo_id].append(line.pk)
    operation_time = timezone.now()
    operations = {}
    # Run the very same business-rule validation for every memo before writing any.
    for memo_id, selected_lines in sorted(groups.items()):
        try:
            plan = _prepare_piecework_return(memo=memo_id, line_ids=selected_lines, returned_at=operation_time)
            operations[memo_id] = plan[3].name
        except ValidationError as exc:
            labels = ", ".join(
                f"barcode {line.job.barcode} (stock {line.job.stock_num or 'not recorded'})"
                for line in lines if line.memo_id == memo_id
            )
            errors.extend(f"{labels}: {message}" for message in exc.messages)
    if errors:
        raise ValidationError(errors)
    if confirmed is not None:
        for memo_id, selected_lines in sorted(groups.items()):
            return_piecework_lines(
                memo=memo_id, line_ids=selected_lines, returned_by=returned_by,
                return_to=returned_by, returned_at=operation_time,
            )
    return {
        "snapshot": snapshot,
        "rows": [{"barcode": str(line.job.barcode), "stock_number": line.job.stock_num or "Not recorded",
                  "memo_number": line.memo.memo_num, "memo_id": line.memo_id,
                  "operation": operations[line.memo_id]} for line in lines],
    }


def is_piecework_uniqueness_conflict(exc):
    cause = exc.__cause__
    if getattr(cause, "sqlstate", getattr(cause, "pgcode", None)) != "23505":
        return False
    diagnostic = getattr(cause, "diag", None)
    if getattr(diagnostic, "table_name", None) != PieceworkMemoLine._meta.db_table:
        return False
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, PieceworkMemoLine._meta.db_table)
    constraint = constraints.get(getattr(diagnostic, "constraint_name", None), {})
    return bool(constraint.get("unique") and constraint.get("columns") in (
        ["activity_id"], ["job_id"], ["memo_id", "job_id"],
    ))
