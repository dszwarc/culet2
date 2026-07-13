# culet/permissions.py

HOURLY_LEVEL = 0
DEPARTMENT_HEAD_LEVEL = 10
MANAGER_LEVEL = 30
SUPER_LEVEL = 50


def get_employee(user):
    """
    Safely return the Employee associated with a user.
    """
    if not user or not user.is_authenticated:
        return None

    try:
        return user.employee
    except AttributeError:
        return None


def get_role_level(user):
    employee = get_employee(user)

    if not employee or not employee.role:
        return None

    return employee.role.level


def has_level(user, minimum_level):
    level = get_role_level(user)

    return level is not None and level >= minimum_level


def is_super(user):
    return has_level(user, SUPER_LEVEL)

def is_manager(user):
    return has_level(user, MANAGER_LEVEL)


def is_department_head(user):
    return has_level(user, DEPARTMENT_HEAD_LEVEL)

def can_view_own_jobs(user):
    return has_level(user, HOURLY_LEVEL)


def can_receive_own_jobs(user):
    return has_level(user, HOURLY_LEVEL)


def can_start_work(user):
    return has_level(user, HOURLY_LEVEL)


def can_assign_jobs(user):
    return has_level(user, DEPARTMENT_HEAD_LEVEL)


def can_view_production_reports(user):
    return has_level(user, MANAGER_LEVEL)


def can_manage_production(user):
    return has_level(user, MANAGER_LEVEL)


def can_ship_jobs(user):
    return is_super(user)


def can_print_jobs(user):
    return is_super(user)


def can_create_repairs(user):
    return is_super(user)


def can_view_all_styles(user):
    return is_super(user)


def can_view_all_inventory(user):
    return is_super(user)


def can_perform_quality_inspection(user):
    employee = get_employee(user)

    if not employee:
        return False

    return is_super(user) or employee.can_qc

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def culet_permission_required(
    permission_function,
    message="You do not have permission to perform this action.",
):
    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")

            if not permission_function(request.user):
                messages.error(request, message)
                return redirect("culet:home")

            return view_function(request, *args, **kwargs)

        return wrapped_view

    return decorator