# culet/mixins.py

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect


class CuletPermissionRequiredMixin(LoginRequiredMixin):
    permission_function = None
    permission_denied_message = "You do not have permission to access this page."
    permission_denied_url = "culet:home"

    def has_permission(self):
        if self.permission_function is None:
            raise NotImplementedError(
                "Set permission_function on the view."
            )

        return self.__class__.permission_function(
            self.request.user
        )

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not self.has_permission():
            messages.error(
                request,
                self.permission_denied_message,
            )
            return redirect(self.permission_denied_url)

        return super().dispatch(request, *args, **kwargs)

    from django.contrib import messages

from .services import log_validation_failure


class LoggedFormInvalidMixin:
    """
    Log invalid ModelForm/FormView submissions consistently.

    Views with formsets should override get_logging_formsets().
    """

    validation_error_message = (
        "The submission could not be completed. "
        "Please correct the errors below."
    )

    def get_logging_formsets(self, context):
        return {}

    def get_logging_extra(self):
        return {}

    def form_invalid(self, form):
        context = self.get_context_data(form=form)

        log_validation_failure(
            request=self.request,
            view_name=self.__class__.__name__,
            form=form,
            formsets=self.get_logging_formsets(
                context
            ),
            extra=self.get_logging_extra(),
        )

        messages.error(
            self.request,
            self.validation_error_message,
        )

        return self.render_to_response(context)