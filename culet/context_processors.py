from .models import TimeClock


def clock_status(request):
    employee = None
    nav_requires_clock_in = False
    nav_current_clock = None

    if request.user.is_authenticated:
        employee = getattr(request.user, "employee", None)

        if employee:
            nav_requires_clock_in = bool(
                employee.role_fk and employee.role_fk.requires_clock_in
            )

            if nav_requires_clock_in:
                nav_current_clock = (
                    TimeClock.objects
                    .filter(employee=employee, clock_out__isnull=True)
                    .order_by("-clock_in")
                    .first()
                )

    return {
        "nav_employee": employee,
        "nav_requires_clock_in": nav_requires_clock_in,
        "nav_clocked_in": nav_current_clock is not None,
        "nav_current_clock": nav_current_clock,
    }