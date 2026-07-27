from django.shortcuts import redirect
from django.urls import reverse


class RequirePasswordChangeMiddleware:
    """
    Force employees marked with must_change_password=True to change their
    password before using the rest of Culet.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        employee = getattr(request.user, "employee", None)

        if employee is None or not employee.must_change_password:
            return self.get_response(request)

        password_change_url = reverse(
            "culet:required_password_change"
        )
        logout_url = reverse("culet:culet_logout")

        allowed_paths = {
            password_change_url,
            logout_url,
        }

        if request.path not in allowed_paths:
            return redirect("culet:required_password_change")

        return self.get_response(request)

import logging

from django.shortcuts import redirect
from django.urls import reverse

from .services import get_request_log_context


logger = logging.getLogger("culet")


class CuletExceptionLoggingMiddleware:
    """
    Log uncaught view exceptions with request and user context.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(
        self,
        request,
        exception,
    ):
        logger.exception(
            "Unhandled request exception. "
            "request=%s exception=%s",
            get_request_log_context(request),
            exception,
        )

        # Return None so Django continues its normal
        # exception handling and displays the proper
        # debug page or HTTP 500 response.
        return None