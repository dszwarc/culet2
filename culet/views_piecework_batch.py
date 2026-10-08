from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect
from django.views.generic import TemplateView

from .forms import BatchPieceworkReturnForm
from .permissions import get_employee, is_department_head
from .piecework_batch_return import process_piecework_batch, is_piecework_uniqueness_conflict

CONFIRMATION_SALT = "culet.piecework.batch-return"


class BatchPieceworkReturnView(LoginRequiredMixin, TemplateView):
    template_name = "piecework/batch_return.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_department_head(request.user):
            raise PermissionDenied("You do not have permission to return piecework.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", BatchPieceworkReturnForm())
        context["returned_rows"] = self.request.session.pop("piecework_batch_return_result", None)
        return context

    def post(self, request, *args, **kwargs):
        form = BatchPieceworkReturnForm(request.POST)
        context = {"form": form}
        if not form.is_valid():
            return self.render_to_response(context)
        # Redisplay the canonical scan list; duplicates and blanks are removed server-side.
        scans = form.cleaned_data["scans"]
        form.data = form.data.copy()
        form.data["scans"] = scans
        if request.POST.get("action") == "edit":
            return self.render_to_response(context)
        employee = get_employee(request.user)
        confirmed = None
        try:
            if request.POST.get("action") == "return":
                try:
                    payload = signing.loads(request.POST.get("confirmation", ""),
                                            salt=CONFIRMATION_SALT, max_age=1800)
                except signing.BadSignature:
                    raise ValidationError("The confirmation expired or is invalid. Review the scan list again.")
                if payload["employee"] != employee.pk or payload["scans"] != scans:
                    raise ValidationError("The scan list changed. Review it again before returning jobs.")
                confirmed = payload["snapshot"]
            result = process_piecework_batch(
                barcodes=scans.splitlines(), returned_by=employee, confirmed=confirmed,
            )
        except ValidationError as exc:
            for error in exc.messages:
                form.add_error(None, error)
            return self.render_to_response(context)
        except IntegrityError as exc:
            if not is_piecework_uniqueness_conflict(exc):
                raise
            form.add_error(None, "A piecework record changed during this return. Nothing was returned. Review the scans again.")
            return self.render_to_response(context)
        if confirmed is not None:
            request.session["piecework_batch_return_result"] = result["rows"]
            messages.success(request, f"Returned {len(result['rows'])} piecework job(s).")
            return redirect("culet:piecework_batch_return")
        context.update(
            preview_rows=result["rows"], scans=scans,
            duplicate_count=getattr(form, "duplicate_count", 0),
            confirmation=signing.dumps(
                {"employee": employee.pk, "scans": scans, "snapshot": result["snapshot"]},
                salt=CONFIRMATION_SALT, compress=True,
            ),
        )
        return self.render_to_response(context)
