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

# culet/context_processors.py

from . import permissions


def culet_permissions(request):
    user = request.user

    return {
        "culet_perms": {
            # Role tiers
            "is_department_head": permissions.is_department_head(user),
            "is_manager": permissions.is_manager(user),
            "is_super": permissions.is_super(user),

            # Specific permissions
            "view_own_jobs": permissions.can_view_own_jobs(user),
            "receive_own_jobs": permissions.can_receive_own_jobs(user),
            "start_work": permissions.can_start_work(user),
            "assign_jobs": permissions.can_assign_jobs(user),
            "manage_production": permissions.can_manage_production(user),
            "view_production_reports": (
                permissions.can_view_production_reports(user)
            ),
            "quality_inspection": (
                permissions.can_perform_quality_inspection(user)
            ),
            "ship_jobs": permissions.can_ship_jobs(user),
            "print_jobs": permissions.can_print_jobs(user),
            "create_repairs": permissions.can_create_repairs(user),
            "view_all_styles": permissions.can_view_all_styles(user),
            "view_all_inventory": (
                permissions.can_view_all_inventory(user)
            ),
        }
    }