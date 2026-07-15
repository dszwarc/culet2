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