"""Conservative historical reconciliation; never infer a period from job alone."""
from django.db.models import Q

from .models import Activity, PieceworkMemoLine


def classify_piecework_activity(line, step, *, has_activity_link=True):
    start, end = line.memo.created_at, line.returned_at
    if not start or not end or end < start or not line.memo.assigned_to_id or not step:
        return "skipped", [], "Missing reference data or invalid period timestamps"

    linked_id = line.activity_id if has_activity_link else None
    candidates = Activity.objects.filter(job_id=line.job_id).filter(
        Q(is_piecework=True) | Q(step=step) | Q(name__iexact="Piecework")
    )
    activities = []
    unexplained_disjoint = False
    for activity in candidates.order_by("pk"):
        touches_period = (
            activity.pk == linked_id or activity.start == start or activity.end == end
            or (activity.start < end and (activity.end is None or activity.end > start))
        )
        # A disjoint Activity is safe to ignore only when another completed
        # line explains it exactly. Otherwise shifted timestamps may conceal
        # an existing Activity for this period, so require manual review.
        explained_elsewhere = False
        if not touches_period and activity.end and activity.is_piecework and not activity.active:
            explained_elsewhere = (
                activity.step_id == step.pk
                and activity.duration == activity.end - activity.start
                and PieceworkMemoLine.objects.filter(
                    job_id=line.job_id, memo__assigned_to_id=activity.employee_id,
                    memo__created_at=activity.start, returned_at=activity.end,
                ).exclude(pk=line.pk).exists()
            )
        if not explained_elsewhere:
            activities.append(activity)
            unexplained_disjoint = unexplained_disjoint or not touches_period
    # Include an invalid link even if its job/step no longer matches.
    if linked_id and linked_id not in [a.pk for a in activities]:
        activities.append(Activity.objects.get(pk=linked_id))
    exact = [a for a in activities if (
        a.job_id == line.job_id and a.employee_id == line.memo.assigned_to_id
        and a.step_id == step.pk and a.is_piecework and not a.active
        and a.start == start and a.end == end and a.duration == end - start
    )]
    if unexplained_disjoint:
        return "ambiguous", activities, "A disjoint Piecework Activity is not explained exactly by another line"
    if len(activities) > 1:
        return "duplicate_suspected", activities, "Multiple possible Activities for this period"
    if linked_id and (len(exact) != 1 or exact[0].pk != linked_id):
        return "ambiguous", activities, "Linked Activity does not match the completed period"

    other_periods = PieceworkMemoLine.objects.filter(job_id=line.job_id).exclude(pk=line.pk).filter(
        Q(memo__created_at=start, returned_at=end)
        | (Q(memo__created_at__lt=end) & (Q(returned_at__gt=start) | Q(returned_at__isnull=True)))
    )
    if other_periods.exists():
        return "ambiguous", activities, "Another piecework line overlaps this period"
    if exact:
        if has_activity_link and PieceworkMemoLine.objects.filter(activity=exact[0]).exclude(pk=line.pk).exists():
            return "ambiguous", activities, "Activity is linked to another line"
        return "existing", exact, "Exact job, employee, step, flags, timestamps and duration match"
    if activities:
        return "ambiguous", activities, "Possible Activity has different employee, timestamps, step or completion fields"
    return "would_create", [], "Complete period data; no exact or overlapping Piecework Activity"


def create_piecework_activity(line, step):
    return Activity.objects.create(
        job_id=line.job_id, employee_id=line.memo.assigned_to_id, step=step,
        start=line.memo.created_at, end=line.returned_at,
        is_piecework=True, active=False,
    )
