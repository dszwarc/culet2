from .models import TimeClock, Activity


def clock_status(request):
    employee = None
    nav_requires_clock_in = False
    nav_current_clock = None

    if request.user.is_authenticated:
        employee = getattr(request.user, "employee", None)

        if employee:
            nav_requires_clock_in = bool(
                employee.role and employee.role.requires_clock_in
            )

            if nav_requires_clock_in:
                nav_current_clock = (
                    TimeClock.objects
                    .filter(employee=employee, clock_out__isnull=True)
                    .order_by("-clock_in")
                    .first()
                )

    nav_open_job_count = 0

    if employee and nav_requires_clock_in:
        nav_open_job_count = (
            Activity.objects
            .filter(employee=employee, active=True, end__isnull=True)
            .values("job_id")
            .distinct()
            .count()
        )

    return {
        "nav_employee": employee,
        "nav_requires_clock_in": nav_requires_clock_in,
        "nav_clocked_in": nav_current_clock is not None,
        "nav_current_clock": nav_current_clock,
        "nav_open_job_count": nav_open_job_count,
    }