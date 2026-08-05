from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from django.template.loader import render_to_string
import copy
from django.db import transaction
from django.db import models
from django.db.models import Exists, F, Q, Max, OuterRef, Subquery, Sum, Count, Avg, ExpressionWrapper, DurationField, DateField, DateTimeField, IntegerField, CharField, Case, When, Value
from django.db.models.functions import TruncDate, Coalesce
from django.views import generic
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .filters import JobFilter, ActivityFilter, StyleFilter, JobReportFilter, MetalVendorLotFilter, OpenPieceworkFilter, JobShipFilter
from datetime import timedelta, datetime, time, date
from collections import defaultdict
from decimal import Decimal
from itertools import chain
from operator import itemgetter
from django.core.exceptions import PermissionDenied, ValidationError
import re
import logging
logger = logging.getLogger("culet")
from django.contrib.auth.views import PasswordChangeView
from collections import OrderedDict, Counter


from .services import (
    clock_in_employee,
    clock_out_employee,
    get_request_log_context,
    log_validation_failure,
    log_view_exception,
    move_job,
    stop_activity,
    sync_job_in_work,
    start_work_batch,
    stop_work_batch,
    validate_batch_jobs,
    parse_barcode_input,
)
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm

from .models import (
    TimeClock,
    Activity,
    JobMovement,
    MovementType,
    Department,
    ActivityStep,
    Step,
    JobMetalLot,
    FailureType,
    QualityInspection,
    QualityInspectionFailure,
    PieceworkMemoLine,
    PieceworkMemo,
    MetalPart,
    JobShip,
    JobMetal,
    MetalVendorLot, 
    MetalReceiptLine, 
    Job, 
    Style, 
    Activity, 
    Employee, 
    TimeClock, 
    StyleMetal, 
    StyleStone, 
    MetalLot, 
    MetalReceipt,
    JobMetal,
    JobStone,
    JobWeight,
    Location,
    JobStatus,
    StyleFinding,
    JobFinding,
    JobTransferMemo,
    JobTransferMemoLine,
    PieceworkMemo,
    WorkBatch,
)

from .forms import (
    StartWorkForm,
    BatchStartForm,
    RepairLookupForm,
    RepairCreateForm,
    QualityInspectionForm,
    QualityFailureReportForm,   
    MemoFilterForm,
    PieceworkScanForm,
    PieceworkMemoCreateForm,
    StyleStepTimeReportForm,
    JobShippedReportForm,
    MetalPartInventoryFilterForm,
    JobTransferMemoForm,
    JobShipLineFormSet,
    BulkJobShipForm,
    JobForm, 
    StyleForm, 
    JobUpdateForm, 
    StyleMetalFormSet, 
    StyleStoneFormSet, 
    MetalLotFormSet, 
    MetalReceiptForm, 
    MetalReceiptLineForm, 
    MetalReceiptLineFormSet,
    JobMetalFormSet,
    JobStoneFormSet,
    JobMetalLotFormSet,
    get_job_metal_formset,
    get_job_stone_formset,
    JobWeightForm,
    JobWeightLookupForm,
    ActivityStartForm,
    InactiveJobsReportForm,
    WeightLossByStyleReportForm,
    EmployeeActivityReportForm,
    TimeClockReportForm,
    TimeClockEditForm,
    get_job_finding_formset,
    JobFindingFormSet,
    StyleFindingFormSet,
    JobsByHolderReportForm,
    )

from .mixins import (
    CuletPermissionRequiredMixin,
    LoggedFormInvalidMixin,
)
from .permissions import can_perform_quality_inspection

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib.auth import logout as auth_logout


@login_required
def culet_logout(request):
    if request.method != "POST":
        return redirect("culet:home")

    employee = getattr(request.user, "employee", None)

    if employee and employee.role and employee.role.requires_clock_in:
        result = clock_out_employee(employee)
        messages.success(request, result.message)

    auth_logout(request)

    return redirect("login")

def get_home_summary_context():
    today = timezone.localdate()
    inactive_cutoff = timezone.now() - timedelta(days=7)

    active_jobs = Job.objects.filter(
        active=True,
        shipped=False,
    )

    clocked_in_employee_ids = (
        TimeClock.objects
        .filter(
            clock_out__isnull=True,
            employee__role__requires_clock_in=True,
        )
        .values_list(
            "employee_id",
            flat=True,
        )
    )

    active_work_employee_ids = (
        Activity.objects
        .filter(
            active=True,
            end__isnull=True,
            job__active=True,
            job__shipped=False,
        )
        .values_list(
            "employee_id",
            flat=True,
        )
    )

    jobs_in_work_count = (
        active_jobs
        .filter(
            activity__active=True,
            activity__end__isnull=True,
        )
        .distinct()
        .count()
    )

    clocked_in_count = (
        Employee.objects
        .filter(
            id__in=clocked_in_employee_ids,
        )
        .distinct()
        .count()
    )

    idle_employee_count = (
        Employee.objects
        .filter(
            id__in=clocked_in_employee_ids,
        )
        .exclude(
            id__in=active_work_employee_ids,
        )
        .distinct()
        .count()
    )

    late_jobs_count = active_jobs.filter(
        due__lt=today,
    ).count()

    inactive_jobs_count = (
        active_jobs
        .annotate(
            last_activity_start=Max("activity__start"),
        )
        .filter(
            Q(last_activity_start__lt=inactive_cutoff)
            |
            Q(
                last_activity_start__isnull=True,
                created__lt=inactive_cutoff,
            )
        )
        .count()
    )

    shipped_today_count = JobShip.objects.filter(
        shipped_at__date=today,
    ).count()

    return {
        "jobs_in_work_count": jobs_in_work_count,
        "clocked_in_count": clocked_in_count,
        "idle_employee_count": idle_employee_count,
        "late_jobs_count": late_jobs_count,
        "inactive_jobs_count": inactive_jobs_count,
        "shipped_today_count": shipped_today_count,
    }

def get_employee(user):
    return get_object_or_404(Employee, user=user)


def find_job_by_scan(scan):
    scan = scan.strip()

    query = Q()

    job_field_names = {field.name for field in Job._meta.get_fields()}

    if "barcode" in job_field_names and scan.isdecimal():
        query |= Q(barcode=int(scan))

    if "stock_num" in job_field_names:
        query |= Q(stock_num=scan)

    if "customer_ref_num" in job_field_names:
        query |= Q(customer_ref_num=scan)

    return Job.objects.filter(query).first()

class ClockedInRequiredMixin:
    """
    Require an employee to be clocked in before accessing a view.

    Users whose role does not require clock-in are allowed through.
    """

    clocked_in_message = "You must be clocked in to do that."

    def dispatch(self, request, *args, **kwargs):
        employee = getattr(request.user, "employee", None)

        if employee is None:
            messages.error(request, "Your user account is not linked to an employee.")
            return redirect("culet:home")

        role = employee.role
        requires_clock_in = True

        if role is not None:
            requires_clock_in = role.requires_clock_in

        if requires_clock_in and not employee.clocked_in:
            messages.error(request, self.clocked_in_message)

            return redirect(
                request.META.get(
                    "HTTP_REFERER",
                    reverse("culet:home")
                )
            )

        return super().dispatch(request, *args, **kwargs)

class HomeView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_home_summary_context())
        return context


class HomeSummaryPartialView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "partials/home_summary_sidebar.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_home_summary_context())
        return context

def index(request):
    return render(request, 'authentication/login.html')

class JobListView(LoginRequiredMixin, generic.ListView):
    model = Job
    template_name = "jobs/index.html"
    context_object_name = "latest_job_list"
    paginate_by = 50

    SORT_FIELDS = {
        "barcode": "barcode",
        "stock_num": "stock_num",
        "name": "name",
        "style": "style__name",
        "assigned_to": "assigned_to__user__last_name",
        "holder": "holder__user__last_name",
        "location": "location__name",
        "customer": "customer__name",
        "due": "due",
    }

    DEFAULT_SORT = "due"

    def get_queryset(self):
        jobs = (
            Job.objects
            .select_related(
                "style",
                "assigned_to",
                "assigned_to__user",
                "assigned_to__department",
                "holder",
                "holder__user",
                "holder__department",
                "customer",
                "status",
            )
        )

        # Hide shipped jobs by default.
        #
        # Apply this to the queryset rather than inserting a fake
        # value into request.GET. If the user explicitly selects a
        # Shipped filter option, JobFilter controls the result.
        if "shipped" not in self.request.GET:
            jobs = jobs.filter(
                shipped=False,
            )

        self.filter = JobFilter(
            self.request.GET or None,
            queryset=jobs,
        )

        sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )

        direction = self.request.GET.get(
            "direction",
            "asc",
        )

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in {
            "asc",
            "desc",
        }:
            direction = "asc"

        self.current_sort = sort
        self.current_direction = direction

        order_field = self.SORT_FIELDS[sort]

        if direction == "desc":
            order_field = f"-{order_field}"

        return self.filter.qs.order_by(
            order_field,
            "barcode",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["filter"] = self.filter
        context["current_sort"] = self.current_sort
        context["current_direction"] = self.current_direction

        # Preserve filters and sorting during pagination.
        query_params = self.request.GET.copy()
        query_params.pop("page", None)

        context["query_params"] = (
            query_params.urlencode()
        )

        # Build sorting links while preserving all current filters.
        sort_links = {}

        for key in self.SORT_FIELDS:
            params = self.request.GET.copy()
            params.pop("page", None)

            next_direction = "asc"

            if (
                self.current_sort == key
                and self.current_direction == "asc"
            ):
                next_direction = "desc"

            params["sort"] = key
            params["direction"] = next_direction

            sort_links[key] = params.urlencode()

        context["sort_links"] = sort_links

        return context


class MyJobListView(
    LoginRequiredMixin,
    generic.ListView,
):
    model = Job
    template_name = "jobs/my_jobs.html"
    context_object_name = "latest_job_list"

    def get_employee(self):
        if not hasattr(self, "_employee"):
            self._employee = Employee.objects.get(
                user=self.request.user,
            )

        return self._employee

    def get_queryset(self):
        employee = self.get_employee()

        active_repair_activity = Activity.objects.filter(
            job=OuterRef("pk"),
            employee=employee,
            active=True,
            end__isnull=True,
            step__code="repair",
            batch__isnull=True,
        ).order_by("-start", "-pk")

        active_activity = Activity.objects.filter(
            job=OuterRef("pk"),
            employee=employee,
            active=True,
            end__isnull=True,
            batch__isnull=True,
        ).order_by("-start", "-pk")

        active_batch_activity = Activity.objects.filter(
            job=OuterRef("pk"),
            employee=employee,
            active=True,
            end__isnull=True,
            batch__active=True,
        ).order_by("-batch__started_at", "-pk")

        return (
            Job.objects
            .filter(
                holder=employee,
                shipped=False,
                is_piecework=False,
            )
            .annotate(
                has_active_work=Exists(active_activity),
                has_active_repair=Exists(active_repair_activity),
                active_repair_start=Subquery(
                    active_repair_activity.values("start")[:1],
                    output_field=DateTimeField(),
                ),
                active_repair_id=Subquery(
                    active_repair_activity.values("pk")[:1],
                    output_field=IntegerField(),
                ),
                active_work_start=Subquery(
                    active_activity.values("start")[:1],
                    output_field=DateTimeField(),
                ),
                active_work_id=Subquery(
                    active_activity.values("pk")[:1],
                    output_field=IntegerField(),
                ),
                active_batch_start=Subquery(
                    active_batch_activity.values("batch__started_at")[:1],
                    output_field=DateTimeField(),
                ),
                active_batch_id=Subquery(
                    active_batch_activity.values("batch_id")[:1],
                    output_field=IntegerField(),
                ),
            )
            .annotate(
                # Deterministic precedence for inconsistent legacy data is
                # repair, then batch, then ordinary individual work.
                running_start=Case(
                    When(
                        active_repair_start__isnull=False,
                        then=F("active_repair_start"),
                    ),
                    When(
                        active_batch_start__isnull=False,
                        then=F("active_batch_start"),
                    ),
                    default=F("active_work_start"),
                    output_field=DateTimeField(),
                ),
                running_activity_id=Case(
                    When(
                        active_repair_id__isnull=False,
                        then=F("active_repair_id"),
                    ),
                    default=F("active_work_id"),
                    output_field=IntegerField(),
                ),
                running_timer_type=Case(
                    When(
                        active_repair_start__isnull=False,
                        then=Value("repair"),
                    ),
                    When(
                        active_batch_start__isnull=False,
                        then=Value("batch"),
                    ),
                    When(
                        active_work_start__isnull=False,
                        then=Value("normal"),
                    ),
                    default=Value(""),
                    output_field=CharField(),
                ),
            )
            .filter(
                Q(assigned_to=employee)
                | Q(has_active_repair=True),
            )
            .select_related(
                "style",
            )
            .order_by(
                "-has_active_repair",
                "-has_active_work",
                F("due").asc(nulls_last=True),
                "stock_num",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        active_batch = (
            WorkBatch.objects
            .filter(employee=self.get_employee(), active=True)
            .select_related("step")
            .prefetch_related(
                models.Prefetch(
                    "activities",
                    queryset=Activity.objects.filter(
                        active=True,
                        end__isnull=True,
                    ).select_related(
                        "job__style",
                        "job__customer",
                    ).order_by("job__due", "job__stock_num"),
                    to_attr="open_activities",
                ),
            )
            .first()
        )

        context["active_work_batch"] = active_batch
        if active_batch:
            context["latest_job_list"] = [
                job
                for job in context["latest_job_list"]
                if job.active_batch_id != active_batch.pk
            ]
        context["my_jobs_total_count"] = (
            len(context["latest_job_list"])
            + (
                len(active_batch.open_activities)
                if active_batch
                else 0
            )
        )

        context["activity_start_form"] = ActivityStartForm(
            employee=self.get_employee(),
        )

        return context

def get_receivable_jobs_for_employee(employee):
    return (
        Job.objects
        .filter(assigned_to=employee,
                shipped=False)
        .exclude(holder=employee)
    )

class MyPieceworkListView(
    LoginRequiredMixin,
    generic.ListView,
):
    model = Job
    template_name = "piecework/my_piecework.html"
    context_object_name = "piecework_job_list"

    def get_employee(self):
        if not hasattr(self, "_employee"):
            self._employee = Employee.objects.get(
                user=self.request.user,
            )

        return self._employee

    def get_queryset(self):
        employee = self.get_employee()

        current_piecework_due_back = (
            PieceworkMemoLine.objects
            .filter(
                job=OuterRef("pk"),
                memo__assigned_to=employee,
                memo__returned_at__isnull=True,
            )
            .order_by(
                "-memo__created_at",
                "-memo__pk",
            )
            .values(
                "memo__due_back",
            )[:1]
        )

        return (
            Job.objects
            .filter(
                pieceworkmemoline__memo__assigned_to=employee,
                pieceworkmemoline__memo__returned_at__isnull=True,
            )
            .select_related(
                "style",
                "customer",
                "assigned_to",
                "holder",
                "status",
            )
            .prefetch_related(
                "pieceworkmemoline_set__memo",
            )
            .annotate(
                piecework_return_date=Subquery(
                    current_piecework_due_back,
                    output_field=DateField(),
                ),
            )
            .order_by(
                models.F(
                    "piecework_return_date",
                ).asc(
                    nulls_last=True,
                ),
                models.F(
                    "due",
                ).asc(
                    nulls_last=True,
                ),
                "stock_num",
            )
            .distinct()
        )

class ReceiveListView(LoginRequiredMixin, generic.ListView):
    model = Job
    template_name = "jobs/receive_list.html"
    context_object_name = "receive_list"

    def get_queryset(self):
        employee = self.request.user.employee

        return (
            get_receivable_jobs_for_employee(employee)
            .select_related("style", "customer", "assigned_to", "holder")
            .order_by("due", "barcode")
        )


class ReceiveJobView(LoginRequiredMixin, generic.View):
    def post(self, request, *args, **kwargs):
        employee = request.user.employee

        job = get_object_or_404(
            get_receivable_jobs_for_employee(employee),
            pk=request.POST.get("job_id"),
        )

        job, movement = move_job(
            job=job,
            movement_type="received",
            to_employee=employee,
            performed_by=employee,
        )

        if movement is None:
            messages.info(
                request,
                f"Job {job.barcode} was already received.",
            )
        else:
            messages.success(
                request,
                f"Job {job.barcode} received.",
            )

        return redirect("culet:receive_list")


class ReceiveAllJobsView(
    LoginRequiredMixin,
    generic.View,
):
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        employee = request.user.employee

        jobs = list(
            get_receivable_jobs_for_employee(employee)
            .select_related("holder")
        )

        received_count = 0

        for job in jobs:
            job, movement = move_job(
                job=job,
                movement_type="received",
                to_employee=employee,
                performed_by=employee,
            )

            if movement is not None:
                received_count += 1

        if received_count:
            job_word = (
                "job"
                if received_count == 1
                else "jobs"
            )

            messages.success(
                request,
                f"{received_count} {job_word} received.",
            )
        else:
            messages.info(
                request,
                "There are no jobs waiting to be received.",
            )

        return redirect("culet:my_jobs")

# class ReceiveAndAssignJobsView(LoginRequiredMixin, generic.TemplateView):
#     template_name = "jobs/receive_and_assign.html"

#     def get_assignable_employees(self):
#         current_employee = self.request.user.employee

#         employees = (
#             Employee.objects
#             .select_related("user", "department", "role")
#             .order_by(
#                 "role__name",
#                 "user__last_name",
#                 "user__first_name",
#             )
#             .exclude(id=current_employee.id)
#         )

#         role_name = current_employee.role.name if current_employee.role else ""

#         if role_name == "Super":
#             return employees

#         if role_name == "Manager":
#             return employees.filter(
#                 department=current_employee.department
#             )

#         return Employee.objects.none()

#     def get(self, request, *args, **kwargs):
#         employees = self.get_assignable_employees()

#         return render(request, self.template_name, {
#             "managers": employees.filter(role__name="Manager") | employees.filter(role__name="Super"),
#             "employees": employees.exclude(role__name="Manager").exclude(role__name="Super"),
#         })

#     @transaction.atomic
#     def post(self, request, *args, **kwargs):
#         current_employee = request.user.employee
#         employees = self.get_assignable_employees()

#         receiving_employee = get_object_or_404(
#             employees,
#             id=request.POST.get("employee")
#         )

#         scanned_jobs = [
#             barcode.strip()
#             for barcode in request.POST.getlist("job")
#             if barcode.strip()
#         ]

#         if not scanned_jobs:
#             messages.error(request, "Please scan at least one job.")
#             return redirect("culet:receive_and_assign_jobs")

#         assigned_count = 0
#         missing_jobs = []

#         for barcode in scanned_jobs:
#             try:
#                 job = Job.objects.get(barcode=barcode)

#                 job.holder = receiving_employee
#                 job.assigned_to = receiving_employee
#                 job.save()

#                 assigned_count += 1

#             except Job.DoesNotExist:
#                 missing_jobs.append(barcode)

#         if assigned_count:
#             messages.success(
#                 request,
#                 f"{assigned_count} job(s) received and assigned to {receiving_employee}."
#             )

#         if missing_jobs:
#             messages.error(
#                 request,
#                 f"These jobs were not found: {', '.join(missing_jobs)}"
#             )

#         return redirect("culet:receive_and_assign_jobs")

class ReportingListView(LoginRequiredMixin, generic.ListView):
    model = Activity
    template_name = "reporting/index.html"
    context_object_name = "activities"

    def get_context_data(self, **kwargs):
        activities = Activity.objects.all()
        myFilter = ActivityFilter(self.request.GET, queryset=activities)
        filt_activities = myFilter.qs

        total_time = timedelta()

        for act in filt_activities:
            if act.duration:
                total_time += act.duration

        if total_time > timedelta() and filt_activities.exists():
            avg_time = total_time / filt_activities.count()
        else:
            avg_time = timedelta()

        context = {
            'activities': filt_activities,
            'filter': myFilter,
            'total_time': total_time,
            'avg_time': avg_time,
        }
        return context

class ActivityListView(LoginRequiredMixin,generic.ListView):
    model = Activity
    template_name = "activities/index.html"
    context_object_name = "activities"
    def get_queryset(self):
        return Activity.objects.order_by("-start")
    
class JobDetailView(
    LoginRequiredMixin,
    generic.DetailView,
):
    # permission_function = can_job_detail
    # permission_denied_message = (
    #     "You do not have permission to view job details."
    # )
    model = Job
    template_name = "jobs/detail.html"
    context_object_name = "job"

    def get_queryset(self):
        return (
            Job.objects
            .select_related(
                "customer",
                "style",
                "assigned_to__user",
                "holder__user",
                "location",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["job_metals"] = (
            JobMetal.objects
            .filter(job=self.object)
            .select_related(
                "part",
                "metal_type",
            )
            .prefetch_related(
                "lot_assignments__metal_lot__vendor_lot",
                "lot_assignments__metal_lot__part",
            )
            .order_by("pk")
        )

        context["job_stones"] = (
            JobStone.objects
            .filter(job=self.object)
            .select_related(
                "stone_type",
                "stone_shape",
            )
            .order_by("pk")
        )

        context["activity"] = (
            Activity.objects
            .filter(job=self.object)
            .select_related(
                "employee__user",
                "step",
            )
            .order_by("-start")
        )

        context["job_movements"] = (
            JobMovement.objects
            .filter(job=self.object)
            .select_related(
                "movement_type",
                "from_employee__user",
                "to_employee__user",
                "performed_by__user",
            )
            .order_by("-created_at", "-pk")
        )
        context["job_weights"] = (
            JobWeight.objects
            .filter(job=self.object)
            ).order_by("-created_at")

        return context

class JobCreateView(LoginRequiredMixin,LoggedFormInvalidMixin, generic.CreateView):
    model = Job
    form_class = JobForm
    template_name = "jobs/create.html"
    success_url = reverse_lazy("culet:index_job")

    def get_logging_formsets(self, context):
        return {
            "metals": context["metal_formset"],
            "stones": context["stone_formset"],
            "findings": context["finding_formset"],
        }


    def get_logging_extra(self):
        return {
            "repair_from": self.request.POST.get(
                "repair_from"
            ),
            "style_id": self.request.POST.get(
                "style"
            ),
        }

    def get_original_repair_job(self):
        repair_from = self.request.GET.get("repair_from") or self.request.POST.get("repair_from")

        if not repair_from:
            return None

        return get_object_or_404(Job, pk=repair_from)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        if self.get_original_repair_job():
            kwargs["is_repair"] = True

        return kwargs

    def get_base_stock_num(self, stock_num):
        import re
        return re.sub(r"-R\d+$", "", str(stock_num))

    def get_next_repair_stock_num(self, original_job):
        base_stock_num = self.get_base_stock_num(original_job.stock_num)

        repair_number = 1

        while Job.objects.filter(stock_num=f"{base_stock_num}-R{repair_number}").exists():
            repair_number += 1

        return f"{base_stock_num}-R{repair_number}"

    def get_initial(self):
        initial = super().get_initial()
        original_job = self.get_original_repair_job()

        if original_job:
            initial.update({
                "customer": original_job.customer,
                "customer_ref_num": None,
                "stock_num": self.get_next_repair_stock_num(original_job),
                "style": original_job.style,
                "due": None,
                "assigned_to": original_job.assigned_to,
                "holder": original_job.holder,
                "location": original_job.location,
                "status": original_job.status,
                "stamp": original_job.stamp,
                "notes": original_job.notes,
                "size": original_job.size,
            })

        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job_form"] = context["form"]
        original_job = self.get_original_repair_job()
        context["original_repair_job"] = original_job
        context["repair_from"] = original_job.pk if original_job else None

        if self.request.POST:
            context["metal_formset"] = JobMetalFormSet(self.request.POST, prefix="metals")
            context["stone_formset"] = JobStoneFormSet(self.request.POST, prefix="stones")
            context["finding_formset"] = JobFindingFormSet(self.request.POST, prefix="findings")
            return context

        if original_job:
            metal_initial = [
                {
                    "part": metal.part,
                    "qty_req": metal.qty_req,
                    "weight_req": metal.weight_req,
                    "metal_type": metal.metal_type,
                }
                for metal in original_job.job_metals.all()
            ]

            stone_initial = [
                {
                    "stone_type": stone.stone_type,
                    "stone_shape": stone.stone_shape,
                    "stone_size": stone.stone_size,
                    "qty_req": stone.qty_req,
                }
                for stone in original_job.job_stones.all()
            ]

            finding_initial = [
                {
                    "finding": finding.finding,
                    "qty_req": finding.qty_req,
                    "qty_used": finding.qty_used,
                }
                for finding in original_job.job_findings.all()
            ]

            JobMetalCreateFormSet = get_job_metal_formset(extra=len(metal_initial))
            JobStoneCreateFormSet = get_job_stone_formset(extra=len(stone_initial))
            JobFindingCreateFormSet = get_job_finding_formset(extra=len(finding_initial))

            context["metal_formset"] = JobMetalCreateFormSet(prefix="metals", initial=metal_initial)
            context["stone_formset"] = JobStoneCreateFormSet(prefix="stones", initial=stone_initial)
            context["finding_formset"] = JobFindingCreateFormSet(prefix="findings", initial=finding_initial)

        else:
            context["metal_formset"] = JobMetalFormSet(prefix="metals")
            context["stone_formset"] = JobStoneFormSet(prefix="stones")
            context["finding_formset"] = JobFindingFormSet(prefix="findings")

        return context

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data(form=form)

        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]
        finding_formset = context["finding_formset"]

        metal_valid = metal_formset.is_valid()
        stone_valid = stone_formset.is_valid()
        finding_valid = finding_formset.is_valid()

        if not (
            metal_valid
            and stone_valid
            and finding_valid
        ):
            log_validation_failure(
                request=self.request,
                view_name=self.__class__.__name__,
                form=form,
                formsets={
                    "metals": metal_formset,
                    "stones": stone_formset,
                    "findings": finding_formset,
                },
                extra={
                    "repair_from": self.request.POST.get(
                        "repair_from"
                    ),
                    "style_id": self.request.POST.get(
                        "style"
                    ),
                },
            )

            messages.error(
                self.request,
                "The job could not be created. "
                "Please correct the requirement errors below.",
            )

            return self.render_to_response(context)

        original_job = self.get_original_repair_job()

        self.object = form.save(commit=False)

        if original_job:
            self.object.is_repair = True
            self.object.repair_of = original_job
        else:
            self.object.is_repair = False
            self.object.assigned_to = None
            self.object.holder = None
        try:
            office_location = Location.objects.get(
                name="Office"
            )
            waiting_status = JobStatus.objects.get(
                name="Waiting on Metal"
            )
        except (
            Location.DoesNotExist,
            Location.MultipleObjectsReturned,
            JobStatus.DoesNotExist,
            JobStatus.MultipleObjectsReturned,
        ) as exc:
            log_view_exception(
                request=self.request,
                view_name=self.__class__.__name__,
                exception=exc,
                extra={
                    "operation": (
                        "load job creation defaults"
                    ),
                },
            )
            raise
        self.object.location = office_location
        self.object.status = waiting_status

        if self.object.style:
            if not self.object.stamp:
                self.object.stamp = self.object.style.stamp or ""

            if not self.object.notes:
                self.object.notes = self.object.style.description or ""

        self.object.save()
        form.save_m2m()
        finding_formset.save()

        metal_formset.instance = self.object
        metal_formset.save()

        stone_formset.instance = self.object
        stone_formset.save()

        finding_formset.instance = self.object
        finding_formset.save()

        logger.info(
            "Job created successfully. "
            "job_id=%s stock_num=%s style_id=%s user=%s",
            self.object.pk,
            self.object.stock_num,
            self.object.style_id,
            self.request.user.get_username(),
        )

        return redirect(self.object.get_absolute_url())

class JobStyleDefaultsHTMXView(LoginRequiredMixin, generic.View):
    template_name = "jobs/partials/job_style_defaults.html"

    def get_base_stock_num(self, stock_num):
        import re
        return re.sub(r"-R\d+$", "", str(stock_num))

    def get_next_repair_stock_num(self, original_job):
        base_stock_num = self.get_base_stock_num(original_job.stock_num)

        repair_number = 1

        while Job.objects.filter(stock_num=f"{base_stock_num}-R{repair_number}").exists():
            repair_number += 1

        return f"{base_stock_num}-R{repair_number}"

    def get(self, request, *args, **kwargs):
        style_id = request.GET.get("style_id")
        repair_from = request.GET.get("repair_from")

        repair_original_job = None

        if repair_from:
            repair_original_job = get_object_or_404(Job, pk=repair_from)

        if repair_original_job:
            style = repair_original_job.style
        else:
            if not style_id:
                return HttpResponseBadRequest("Missing style_id")

            style = get_object_or_404(Style, pk=style_id)

        if repair_original_job:
            job_initial = {
                "customer": repair_original_job.customer_id,
                "customer_ref_num": None,
                "stock_num": self.get_next_repair_stock_num(repair_original_job),
                "style": repair_original_job.style_id,
                "due": None,
                "assigned_to": repair_original_job.assigned_to_id,
                "holder": repair_original_job.holder_id,
                "location": repair_original_job.location_id,
                "status": repair_original_job.status_id,
                "stamp": repair_original_job.stamp,
                "notes": repair_original_job.notes,
                "size": repair_original_job.size,
            }

            metal_initial = [
                {
                    "part": metal.part_id,
                    "qty_req": metal.qty_req,
                    "weight_req": metal.weight_req,
                    "metal_type": metal.metal_type_id,
                }
                for metal in repair_original_job.job_metals.all()
            ]

            stone_initial = [
                {
                    "stone_type": stone.stone_type_id,
                    "stone_shape": stone.stone_shape_id,
                    "stone_size": stone.stone_size,
                    "qty_req": stone.qty_req,
                }
                for stone in repair_original_job.job_stones.all()
            ]

            finding_initial = [
                {
                    "finding": finding.finding_id,
                    "qty_req": finding.qty_req,
                    "qty_used": finding.qty_used,
                }
                for finding in repair_original_job.job_findings.all()
            ]

        else:
            job_initial = {
                "customer": style.customer_id,
                "style": style.pk,
                "stamp": style.stamp or "",
                "notes": style.description or "",
                "due": None,
            }

            metal_initial = [
                {
                    "part": sm.part_id,
                    "qty_req": sm.qty_req,
                    "weight_req": sm.weight,
                    "metal_type": sm.metal_type_id,
                }
                for sm in style.stylemetal_set.all()
            ]

            stone_initial = [
                {
                    "stone_type": ss.stone_type_id,
                    "stone_shape": ss.stone_shape_id,
                    "stone_size": ss.stone_size,
                    "qty_req": ss.qty_req,
                }
                for ss in style.stylestone_set.all()
            ]

            finding_initial = [
                {
                    "finding": sf.finding_id,
                    "qty_req": sf.qty_req,
                }
                for sf in style.style_findings.all()
            ]

        job_form = JobForm(
            initial=job_initial,
            is_repair=bool(repair_original_job),
        )

        JobMetalCreateFormSet = get_job_metal_formset(extra=len(metal_initial))
        JobStoneCreateFormSet = get_job_stone_formset(extra=len(stone_initial))
        JobFindingCreateFormSet = get_job_finding_formset(extra=len(finding_initial))

        metal_formset = JobMetalCreateFormSet(prefix="metals", initial=metal_initial)
        stone_formset = JobStoneCreateFormSet(prefix="stones", initial=stone_initial)
        finding_formset = JobFindingCreateFormSet(prefix="findings", initial=finding_initial)

        return render(
            request,
            self.template_name,
            {
                "job_form": job_form,
                "metal_formset": metal_formset,
                "stone_formset": stone_formset,
                "finding_formset": finding_formset,
                "style": style,
                "repair_from": repair_from,
                "repair_original_job": repair_original_job,
            },
        )

def create_allocation_weight(job, allocated_weight_delta, user=None):
    allocated_weight_delta = Decimal(allocated_weight_delta or 0)

    if allocated_weight_delta == 0:
        return

    allocation_step = Step.objects.get(code="metal_allocated")

    latest_weight = (
        JobWeight.objects
        .filter(job=job)
        .order_by("-created_at", "-id")
        .first()
    )

    if latest_weight:
        new_piece_weight = Decimal(latest_weight.weight or 0) + allocated_weight_delta
    else:
        new_piece_weight = allocated_weight_delta

    JobWeight.objects.create(
        job=job,
        step=allocation_step,
        weight=new_piece_weight,
        sprue_weight=Decimal("0"),
        dust_weight=Decimal("0"),
        recorded_by=user,
    )

class JobMetalLotAssignView(LoginRequiredMixin, generic.View):
    template_name = "jobs/metal_lot_assignment.html"

    def get(self, request, pk):
        job_metal = get_object_or_404(JobMetal, pk=pk)

        formset = JobMetalLotFormSet(instance=job_metal, prefix="lots")
        for form in formset.forms:
            form.fields["metal_lot"].queryset = MetalLot.objects.filter(
                part=job_metal.part,
                qty_on_hand__gt=0
            ).select_related("vendor_lot", "part")

        return render(request, self.template_name, {
            "job_metal": job_metal,
            "formset": formset,
        })

    @transaction.atomic
    def post(self, request, pk):
        job_metal = get_object_or_404(JobMetal, pk=pk)

        existing_allocations = list(
            job_metal.lot_assignments.select_related("metal_lot")
        )

        old_allocated_weight = sum(
            Decimal(alloc.weight_used or 0)
            for alloc in existing_allocations
        )

        # restore previous allocations before recalculating
        for alloc in existing_allocations:
            MetalLot.objects.filter(pk=alloc.metal_lot_id).update(
                qty_on_hand=F("qty_on_hand") + alloc.qty_used,
                weight_on_hand=F("weight_on_hand") + alloc.weight_used,
            )

        job_metal.lot_assignments.all().delete()

        formset = JobMetalLotFormSet(request.POST, instance=job_metal, prefix="lots")

        for form in formset.forms:
            form.fields["metal_lot"].queryset = MetalLot.objects.filter(
                part=job_metal.part,
                qty_on_hand__gte=0
            ).select_related("vendor_lot", "part")

        if not formset.is_valid():
            return render(request, self.template_name, {
                "job_metal": job_metal,
                "formset": formset,
            })

        assignments = formset.save(commit=False)

        new_allocated_weight = sum(
            Decimal(alloc.weight_used or 0)
            for alloc in assignments
        )

        # Validate availability before saving
        for alloc in assignments:
            lot = alloc.metal_lot

            if alloc.qty_used > lot.qty_on_hand:
                formset.non_form_errors = lambda: ["Assigned quantity exceeds available quantity."]
                return render(request, self.template_name, {
                    "job_metal": job_metal,
                    "formset": formset,
                })

            if alloc.weight_used > lot.weight_on_hand:
                formset.non_form_errors = lambda: ["Assigned weight exceeds available weight."]
                return render(request, self.template_name, {
                    "job_metal": job_metal,
                    "formset": formset,
                })

        # Save allocations and decrement inventory
        for alloc in assignments:
            lot = alloc.metal_lot

            MetalLot.objects.filter(pk=lot.pk).update(
                qty_on_hand=F("qty_on_hand") - alloc.qty_used,
                weight_on_hand=F("weight_on_hand") - alloc.weight_used,
            )

            alloc.job_metal = job_metal
            alloc.save()

        allocated_weight_delta = new_allocated_weight - old_allocated_weight

        create_allocation_weight(
            job=job_metal.job,
            allocated_weight_delta=allocated_weight_delta,
            user=request.user,
        )

        return redirect(job_metal.job.get_absolute_url())

class JobMetalLotAssignmentHTMXView(LoginRequiredMixin, generic.View):
    template_name = "jobs/partials/job_metal_lot_formset.html"

    def get(self, request, pk, *args, **kwargs):
        job_metal = get_object_or_404(JobMetal, pk=pk)

        formset = JobMetalLotFormSet(instance=job_metal, prefix=f"lots-{job_metal.pk}")

        return render(request, self.template_name, {
            "job_metal": job_metal,
            "lot_formset": formset,
        })

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        job_metal = get_object_or_404(JobMetal, pk=pk)

        formset = JobMetalLotFormSet(
            request.POST,
            instance=job_metal,
            prefix=f"lots-{job_metal.pk}",
        )

        if not formset.is_valid():
            return render(request, self.template_name, {
                "job_metal": job_metal,
                "lot_formset": formset,
            })

        existing_assignments = list(
            job_metal.lot_assignments.select_related("metal_lot")
        )

        old_allocated_weight = sum(
            Decimal(existing.weight_used or 0)
            for existing in existing_assignments
        )

        # Restore existing assignments before checking availability
        for existing in existing_assignments:
            MetalLot.objects.filter(pk=existing.metal_lot_id).update(
                qty_on_hand=F("qty_on_hand") + existing.qty_used,
                weight_on_hand=F("weight_on_hand") + existing.weight_used,
            )

        job_metal.lot_assignments.all().delete()

        assignments = formset.save(commit=False)

        new_allocated_weight = sum(
            Decimal(assignment.weight_used or 0)
            for assignment in assignments
        )

        # Validate availability before saving/decrementing inventory
        for assignment in assignments:
            lot = MetalLot.objects.get(pk=assignment.metal_lot_id)

            if assignment.qty_used > lot.qty_on_hand:
                messages.error(
                    request,
                    f"Assigned quantity exceeds available quantity for {lot}."
                )

                return render(request, self.template_name, {
                    "job_metal": job_metal,
                    "lot_formset": JobMetalLotFormSet(
                        instance=job_metal,
                        prefix=f"lots-{job_metal.pk}",
                    ),
                })

            if assignment.weight_used > lot.weight_on_hand:
                messages.error(
                    request,
                    f"Assigned weight exceeds available weight for {lot}."
                )

                return render(request, self.template_name, {
                    "job_metal": job_metal,
                    "lot_formset": JobMetalLotFormSet(
                        instance=job_metal,
                        prefix=f"lots-{job_metal.pk}",
                    ),
                })

        # Save new assignments and decrement inventory
        for assignment in assignments:
            lot = assignment.metal_lot

            MetalLot.objects.filter(pk=lot.pk).update(
                qty_on_hand=F("qty_on_hand") - assignment.qty_used,
                weight_on_hand=F("weight_on_hand") - assignment.weight_used,
            )

            assignment.job_metal = job_metal
            assignment.save()

        allocated_weight_delta = new_allocated_weight - old_allocated_weight

        create_allocation_weight(
            job=job_metal.job,
            allocated_weight_delta=allocated_weight_delta,
            user=request.user,
        )

        return render(request, self.template_name, {
            "job_metal": job_metal,
            "lot_formset": JobMetalLotFormSet(
                instance=job_metal,
                prefix=f"lots-{job_metal.pk}",
            ),
            "saved": True,
        })

class JobUpdateView(LoginRequiredMixin, generic.UpdateView):
    model = Job
    form_class = JobForm
    template_name = "jobs/update.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.POST:
            context["metal_formset"] = JobMetalFormSet(self.request.POST, instance=self.object, prefix="metals")
            context["stone_formset"] = JobStoneFormSet(self.request.POST, instance=self.object, prefix="stones")
            context["finding_formset"] = JobFindingFormSet(self.request.POST, instance=self.object, prefix="findings")
        else:
            context["metal_formset"] = JobMetalFormSet(instance=self.object, prefix="metals")
            context["stone_formset"] = JobStoneFormSet(instance=self.object, prefix="stones")
            context["finding_formset"] = JobFindingFormSet(instance=self.object, prefix="findings")

        return context

    def form_invalid(self, form):
        context = self.get_context_data(form=form)

        log_validation_failure(
            request=self.request,
            view_name=self.__class__.__name__,
            form=form,
            formsets={
                "metals": context["metal_formset"],
                "stones": context["stone_formset"],
                "findings": context["finding_formset"],
            },
            extra={
                "job_id": self.object.pk,
                "stock_num": self.object.stock_num,
            },
        )

        messages.error(
            self.request,
            "The job could not be updated. "
            "Please correct the highlighted fields.",
        )

        return self.render_to_response(context)

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]
        finding_formset = context["finding_formset"]

        metal_valid = metal_formset.is_valid()
        stone_valid = stone_formset.is_valid()
        finding_valid = finding_formset.is_valid()

        if not (
            metal_valid
            and stone_valid
            and finding_valid
        ):
            log_validation_failure(
                request=self.request,
                view_name=self.__class__.__name__,
                form=form,
                formsets={
                    "metals": metal_formset,
                    "stones": stone_formset,
                    "findings": finding_formset,
                },
                extra={
                    "job_id": self.object.pk,
                    "stock_num": self.object.stock_num,
                },
            )

            messages.error(
                self.request,
                "The job could not be updated. "
                "Please correct the highlighted requirement fields.",
            )

            context.update(
                {
                    "form": form,
                    "metal_formset": metal_formset,
                    "stone_formset": stone_formset,
                    "finding_formset": finding_formset,
                }
            )

            return self.render_to_response(context)

        self.object = form.save()
        metal_formset.instance = self.object
        metal_formset.save()

        stone_formset.instance = self.object
        stone_formset.save()

        finding_formset.instance = self.object
        finding_formset.save()

        return redirect(self.object.get_absolute_url())

    def get_success_url(self):
        return self.object.get_absolute_url()

class StyleListView(LoginRequiredMixin, generic.ListView):
    model = Style
    template_name = "styles/index.html"
    context_object_name = "style_list"
    paginate_by = 50

    SORT_FIELDS = {
        "name": "name",
        "customer": "customer__name",
        "stamp": "stamp",
        "description": "description",
    }

    DEFAULT_SORT = "name"

    def get_queryset(self):
        styles = Style.objects.select_related(
            "customer",
        )

        self.filter = StyleFilter(
            self.request.GET,
            queryset=styles,
        )

        sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )

        direction = self.request.GET.get(
            "direction",
            "asc",
        )

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in ["asc", "desc"]:
            direction = "asc"

        self.current_sort = sort
        self.current_direction = direction

        order_field = self.SORT_FIELDS[sort]

        if direction == "desc":
            order_field = f"-{order_field}"

        return self.filter.qs.order_by(
            order_field,
            "name",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["filter"] = self.filter
        context["current_sort"] = self.current_sort
        context["current_direction"] = self.current_direction

        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["query_params"] = query_params.urlencode()

        sort_links = {}

        for key in self.SORT_FIELDS:
            params = self.request.GET.copy()
            params.pop("page", None)

            next_direction = "asc"

            if (
                self.current_sort == key
                and self.current_direction == "asc"
            ):
                next_direction = "desc"

            params["sort"] = key
            params["direction"] = next_direction

            sort_links[key] = params.urlencode()

        context["sort_links"] = sort_links

        return context

class StyleDetailView(LoginRequiredMixin,generic.DetailView):
    model = Style
    template_name = "styles/detail.html"
    context_object_name = "style"

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        req_metal = StyleMetal.objects.filter(style=data['style'])
        data['metal'] = req_metal
        req_stones = StyleStone.objects.filter(style=data['style'])
        data['stones'] = req_stones
        req_findings = StyleFinding.objects.filter(style=data["style"])
        data["findings"] = req_findings
        return data

class StyleCreateView(LoginRequiredMixin, generic.CreateView):
    model = Style
    form_class = StyleForm
    template_name = "styles/create.html"
    success_url = reverse_lazy("culet:index_style")  # adjust

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.POST:
            context["metal_formset"] = StyleMetalFormSet(self.request.POST)
            context["stone_formset"] = StyleStoneFormSet(self.request.POST)
            context["finding_formset"] = StyleFindingFormSet(self.request.POST)
        else:
            context["metal_formset"] = StyleMetalFormSet()
            context["stone_formset"] = StyleStoneFormSet()
            context["finding_formset"] = StyleFindingFormSet()

        return context

    def form_valid(self, form):
        context = self.get_context_data()
        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]

        finding_formset = context["finding_formset"]

        if (
            metal_formset.is_valid()
            and stone_formset.is_valid()
            and finding_formset.is_valid()
        ):
            self.object = form.save()

            metal_formset.instance = self.object
            metal_formset.save()

            stone_formset.instance = self.object
            stone_formset.save()

            finding_formset.instance = self.object
            finding_formset.save()

            return redirect(self.get_success_url())

        # If formset invalid, re-render page with errors
        return self.render_to_response(self.get_context_data(form=form))

class StyleUpdateView(LoginRequiredMixin, generic.UpdateView):
    model = Style
    form_class = StyleForm
    template_name = "styles/create.html"
    context_object_name = "style"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.POST:
            context["metal_formset"] = StyleMetalFormSet(
                self.request.POST,
                instance=self.object,
            )
            context["stone_formset"] = StyleStoneFormSet(
                self.request.POST,
                instance=self.object,
            )
            context["finding_formset"] = StyleFindingFormSet(
                self.request.POST,
                instance=self.object,
            )
        else:
            context["metal_formset"] = StyleMetalFormSet(
                instance=self.object,
            )
            context["stone_formset"] = StyleStoneFormSet(
                instance=self.object,
            )
            context["finding_formset"] = StyleFindingFormSet(
                instance=self.object,
            )

        context["is_edit"] = True
        return context

    def form_valid(self, form):
        context = self.get_context_data()

        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]
        finding_formset = context["finding_formset"]

        if (
            metal_formset.is_valid()
            and stone_formset.is_valid()
            and finding_formset.is_valid()
        ):
            self.object = form.save()

            metal_formset.instance = self.object
            metal_formset.save()

            stone_formset.instance = self.object
            stone_formset.save()

            finding_formset.instance = self.object
            finding_formset.save()

            return redirect(self.get_success_url())

        return self.render_to_response(
            self.get_context_data(form=form)
        )

    def get_success_url(self):
        return reverse_lazy(
            "culet:style_detail",
            kwargs={"pk": self.object.pk},
        )

class AssignJobView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "jobs/assign.html"

    def get_employees(self):
        return (
            Employee.objects
            .filter(
                active=True,
                user__is_active=True,
            )
            .select_related(
                "user",
                "department",
                "role",
            )
            .order_by(
                "user__last_name",
                "user__first_name",
            )
        )

    def get_departments(self):
        return (
            Department.objects
            .order_by("name")
        )

    def get_selected_job(self):
        job_id = self.request.GET.get(
            "job_id",
            "",
        ).strip()

        if not job_id:
            return None

        return (
            Job.objects
            .select_related(
                "style",
                "customer",
            )
            .filter(pk=job_id)
            .first()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["employees"] = self.get_employees()
        context["departments"] = self.get_departments()
        context["selected_job"] = self.get_selected_job()

        context.setdefault(
            "jobs_text",
            "",
        )

        context.setdefault(
            "selected_employee_id",
            "",
        )

        return context

    def render_form_with_errors(
        self,
        request,
        *,
        jobs_text="",
        selected_employee_id="",
        selected_job=None,
    ):
        context = self.get_context_data()

        context["jobs_text"] = jobs_text
        context["selected_employee_id"] = (
            selected_employee_id
        )

        if selected_job is not None:
            context["selected_job"] = selected_job

        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        employee_id = request.POST.get(
            "employee",
            "",
        ).strip()

        selected_job_id = request.POST.get(
            "job_id",
            "",
        ).strip()

        jobs_text = request.POST.get(
            "jobs_text",
            "",
        )

        # Accept barcodes separated by:
        # - new lines
        # - spaces
        # - tabs
        # - commas
        # - semicolons
        parsed_barcodes = parse_barcode_input(jobs_text)
        submitted_barcodes = parsed_barcodes.values
        duplicate_barcode_count = parsed_barcodes.duplicate_count

        redirect_url = reverse(
            "culet:assign_job",
        )

        if selected_job_id:
            redirect_url = (
                f"{redirect_url}"
                f"?job_id={selected_job_id}"
            )

        selected_job = None

        # Retrieve the selected Job Detail job before validating
        # the employee so that the job remains visible if the
        # form is rendered again with an error.
        if selected_job_id:
            selected_job = (
                Job.objects
                .select_related(
                    "style",
                    "customer",
                    "assigned_to",
                )
                .filter(
                    pk=selected_job_id,
                )
                .first()
            )

            if not selected_job:
                messages.error(
                    request,
                    "The selected job could not be found.",
                )

                return redirect(
                    "culet:assign_job",
                )

        if not employee_id:
            messages.error(
                request,
                (
                    "Please select the employee to assign "
                    "these jobs to."
                ),
            )

            return self.render_form_with_errors(
                request,
                jobs_text=jobs_text,
                selected_employee_id=employee_id,
                selected_job=selected_job,
            )

        employee = get_object_or_404(
            self.get_employees(),
            pk=employee_id,
        )

        # Assignment opened from a Job Detail page.
        if selected_job is not None:
            jobs = [
                selected_job,
            ]

        # Assignment using the textarea.
        else:
            if not submitted_barcodes:
                messages.error(
                    request,
                    (
                        "Please enter at least one "
                        "job barcode."
                    ),
                )

                return self.render_form_with_errors(
                    request,
                    jobs_text=jobs_text,
                    selected_employee_id=employee_id,
                )

            matching_jobs = list(
                Job.objects
                .filter(
                    barcode__in=submitted_barcodes,
                )
                .select_related(
                    "style",
                    "assigned_to",
                )
            )

            jobs_by_barcode = {
                str(job.barcode): job
                for job in matching_jobs
            }

            missing_barcodes = [
                barcode
                for barcode in submitted_barcodes
                if barcode not in jobs_by_barcode
            ]

            if missing_barcodes:
                messages.error(
                    request,
                    (
                        "No job was found for the "
                        "following barcode(s): "
                        + ", ".join(
                            missing_barcodes,
                        )
                    ),
                )

                return self.render_form_with_errors(
                    request,
                    jobs_text=jobs_text,
                    selected_employee_id=employee_id,
                )

            # Preserve the order in which the barcodes
            # were entered into the textarea.
            jobs = [
                jobs_by_barcode[barcode]
                for barcode in submitted_barcodes
            ]

        assigned_count = 0
        recovered_piecework_count = 0

        with transaction.atomic():
            job_ids = [
                job.pk
                for job in jobs
            ]

            # Lock only the Job rows. Do not use
            # select_related() with select_for_update()
            # because nullable joins may cause PostgreSQL
            # FOR UPDATE errors.
            locked_jobs = list(
                Job.objects
                .select_for_update()
                .filter(
                    pk__in=job_ids,
                )
            )

            locked_jobs_by_id = {
                job.pk: job
                for job in locked_jobs
            }

            # Preserve the original job order after
            # replacing the jobs with their locked copies.
            jobs = [
                locked_jobs_by_id[job.pk]
                for job in jobs
            ]

            open_piecework_job_ids = set(
                PieceworkMemoLine.objects
                .filter(
                    job_id__in=job_ids,
                    memo__returned_at__isnull=True,
                )
                .values_list(
                    "job_id",
                    flat=True,
                )
            )

            blocked_jobs = [
                job
                for job in jobs
                if job.pk in open_piecework_job_ids
            ]

            if blocked_jobs:
                blocked_identifiers = [
                    str(
                        job.stock_num
                        or job.barcode
                    )
                    for job in blocked_jobs
                ]

                messages.error(
                    request,
                    (
                        "The following job(s) are currently "
                        "assigned through an open piecework "
                        "memo and cannot be reassigned here: "
                        + ", ".join(
                            blocked_identifiers,
                        )
                        + ". Return the piecework memo first."
                    ),
                )

                return self.render_form_with_errors(
                    request,
                    jobs_text=jobs_text,
                    selected_employee_id=employee_id,
                    selected_job=selected_job,
                )

            for job in jobs:
                # Imported legacy jobs can retain the
                # is_piecework flag despite having no open
                # PieceworkMemoLine. Clear that stale state
                # before assigning the job normally.
                if job.is_piecework:
                    job.is_piecework = False
                    job.piecework_assigned_at = None
                    job.in_work = False

                    job.save(
                        update_fields=[
                            "is_piecework",
                            "piecework_assigned_at",
                            "in_work",
                            "last_updated",
                        ],
                    )

                    recovered_piecework_count += 1

                job, movement = move_job(
                    job=job,
                    movement_type="assigned",
                    to_employee=employee,
                    performed_by=request.user.employee,
                )

                if movement is not None:
                    assigned_count += 1

        if recovered_piecework_count:
            job_word = (
                "job"
                if recovered_piecework_count == 1
                else "jobs"
            )

            messages.warning(
                request,
                (
                    f"{recovered_piecework_count} legacy "
                    f"{job_word} had a piecework flag "
                    "without an open piecework memo. "
                    "The stale piecework status was cleared."
                ),
            )

        if assigned_count:
            job_word = (
                "job"
                if assigned_count == 1
                else "jobs"
            )

            repeated_barcode_message = ""

            if duplicate_barcode_count:
                repeated_barcode_word = (
                    "barcode"
                    if duplicate_barcode_count == 1
                    else "barcodes"
                )
                repeated_barcode_message = (
                    f" {duplicate_barcode_count} repeated "
                    f"{repeated_barcode_word} ignored."
                )

            messages.success(
                request,
                (
                    f"{assigned_count} {job_word} "
                    f"assigned.{repeated_barcode_message}"
                ),
            )
        else:
            messages.info(
                request,
                (
                    "The selected job(s) were already "
                    f"assigned to {employee}."
                ),
            )

        return redirect(
            "culet:home",
        )
    
class ReturnJobView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "jobs/return.html"

    def get_return_employees(self):
        return (
            Employee.objects
            .filter(
                can_receive_returned_jobs=True,
                user__is_active=True,
            )
            .select_related(
                "user",
                "department",
                "role",
            )
            .order_by(
                "user__last_name",
                "user__first_name",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["return_employees"] = (
            self.get_return_employees()
        )
        context.setdefault("barcodes_text", "")
        context.setdefault("selected_employee_id", "")

        return context

    def render_form_with_errors(self, *, barcodes_text, employee_id):
        return self.render_to_response(
            self.get_context_data(
                barcodes_text=barcodes_text,
                selected_employee_id=employee_id,
            ),
        )

    def post(self, request, *args, **kwargs):
        barcodes_text = request.POST.get("barcodes", "")
        parsed_barcodes = parse_barcode_input(barcodes_text)
        submitted_barcodes = parsed_barcodes.values

        employee_id = request.POST.get(
            "employee",
            "",
        ).strip()

        if not submitted_barcodes:
            messages.error(
                request,
                "Please enter or scan at least one job barcode.",
            )
            return self.render_form_with_errors(
                barcodes_text=barcodes_text,
                employee_id=employee_id,
            )

        invalid_barcodes = []
        for barcode in submitted_barcodes:
            try:
                int(barcode)
            except ValueError:
                invalid_barcodes.append(barcode)

        if invalid_barcodes:
            messages.error(
                request,
                "Invalid numeric barcode(s): " + ", ".join(invalid_barcodes),
            )
            return self.render_form_with_errors(
                barcodes_text=barcodes_text,
                employee_id=employee_id,
            )

        if not employee_id:
            messages.error(
                request,
                "Please select the employee receiving these jobs.",
            )
            return self.render_form_with_errors(
                barcodes_text=barcodes_text,
                employee_id=employee_id,
            )

        return_employee = get_object_or_404(
            self.get_return_employees(),
            pk=employee_id,
        )

        jobs = list(
            Job.objects
            .filter(
                barcode__in=submitted_barcodes,
            )
            .select_related(
                "style",
                "assigned_to",
            )
        )

        jobs_by_barcode = {
            str(job.barcode): job
            for job in jobs
        }

        missing_barcodes = [
            barcode
            for barcode in submitted_barcodes
            if barcode not in jobs_by_barcode
        ]

        if missing_barcodes:
            messages.error(
                request,
                "No job was found for the following barcode(s): "
                + ", ".join(missing_barcodes),
            )
            return self.render_form_with_errors(
                barcodes_text=barcodes_text,
                employee_id=employee_id,
            )

        returned_count = 0

        with transaction.atomic():
            for barcode in submitted_barcodes:
                job = jobs_by_barcode[barcode]

                job, movement = move_job(
                    job=job,
                    movement_type="returned-to-manager",
                    to_employee=return_employee,
                    performed_by=request.user.employee,
                )

                if movement is not None:
                    returned_count += 1

        if returned_count:
            job_word = (
                "job"
                if returned_count == 1
                else "jobs"
            )

            duplicate_message = ""
            if parsed_barcodes.duplicate_count:
                duplicate_word = (
                    "barcode was"
                    if parsed_barcodes.duplicate_count == 1
                    else "barcodes were"
                )
                duplicate_message = (
                    f" {parsed_barcodes.duplicate_count} repeated "
                    f"{duplicate_word} ignored."
                )

            messages.success(
                request,
                (
                    f"{returned_count} {job_word} "
                    f"returned to {return_employee}."
                    f"{duplicate_message}"
                ),
            )
        else:
            messages.info(
                request,
                (
                    "The selected job(s) were already assigned "
                    f"to {return_employee}."
                ),
            )

        return redirect("culet:return_job")


class BatchStartView(LoginRequiredMixin, generic.TemplateView):
    template_name = "jobs/batch_start.html"

    def get_employee(self):
        return get_object_or_404(Employee, user=self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not self.get_employee().can_start_batch:
            raise PermissionDenied("You do not have permission to start batch work.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", BatchStartForm(employee=self.get_employee()))
        return context

    def post(self, request, *args, **kwargs):
        employee = self.get_employee()
        form = BatchStartForm(request.POST, employee=employee)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        barcodes = form.cleaned_data["barcodes"]
        matching_jobs = list(Job.objects.filter(barcode__in=barcodes))
        jobs_by_barcode = {str(job.barcode): job for job in matching_jobs}
        missing = [barcode for barcode in barcodes if barcode not in jobs_by_barcode]
        if missing:
            form.add_error(
                "barcodes",
                "No job was found for: " + ", ".join(missing),
            )
            return self.render_to_response(self.get_context_data(form=form))

        jobs = [jobs_by_barcode[barcode] for barcode in barcodes]
        errors = validate_batch_jobs(
            employee=employee,
            jobs=jobs,
            step=form.cleaned_data["step"],
        )
        if errors:
            for error in errors:
                form.add_error(None, error)
            return self.render_to_response(self.get_context_data(form=form))

        if request.POST.get("action") != "confirm":
            return self.render_to_response(
                self.get_context_data(form=form, review_jobs=jobs),
            )

        try:
            batch = start_work_batch(
                employee=employee,
                jobs=jobs,
                step=form.cleaned_data["step"],
            )
        except ValidationError as exc:
            for error in exc.messages:
                form.add_error(None, error)
            return self.render_to_response(self.get_context_data(form=form))

        messages.success(
            request,
            (
                f"{batch.activities.count()} jobs added to the batch."
                + (
                    f" {form.parsed_barcode_input.duplicate_count} repeated "
                    + (
                        "barcode was ignored."
                        if form.parsed_barcode_input.duplicate_count == 1
                        else "barcodes were ignored."
                    )
                    if form.parsed_barcode_input.duplicate_count
                    else ""
                )
            ),
        )
        return redirect("culet:my_jobs")


class StopWorkBatchView(LoginRequiredMixin, generic.View):
    http_method_names = ["post"]

    def post(self, request, pk, *args, **kwargs):
        batch = get_object_or_404(
            WorkBatch,
            pk=pk,
            employee=request.user.employee,
        )
        if not batch.active:
            messages.info(request, "This batch has already been stopped.")
            return redirect("culet:my_jobs")

        stop_work_batch(batch=batch)
        messages.success(request, "Batch work stopped.")
        return redirect("culet:my_jobs")


class StartWorkView(
    LoginRequiredMixin,
    generic.View,
):
    template_name = "jobs/start_work.html"

    def get_job(self, pk):
        return get_object_or_404(
            Job.objects.select_related(
                "assigned_to",
                "holder",
            ),
            pk=pk,
        )

    def validate_job(self, request, job, employee):
        if WorkBatch.objects.filter(employee=employee, active=True).exists():
            messages.error(
                request,
                "Stop your active batch before starting individual work.",
            )
            return False

        if not job.active:
            messages.error(
                request,
                "This job is inactive and cannot be started.",
            )
            return False

        if job.shipped:
            messages.error(
                request,
                "This job has already been shipped.",
            )
            return False

        if job.is_piecework:
            messages.error(
                request,
                (
                    "This job is currently assigned as piecework "
                    "and cannot be started here."
                ),
            )
            return False

        if job.assigned_to != employee:
            messages.error(
                request,
                "You can only start jobs assigned to you.",
            )
            return False

        if job.holder != employee:
            messages.error(
                request,
                (
                    "You must receive this job before starting work."
                ),
            )
            return False

        if not employee.clocked_in:
            messages.error(
                request,
                "Please clock in before starting work.",
            )
            return False

        return True

    def get(self, request, pk):
        employee = request.user.employee
        job = self.get_job(pk)

        if not self.validate_job(
            request,
            job,
            employee,
        ):
            return redirect("culet:my_jobs")

        form = StartWorkForm(
            employee=employee,
        )

        return render(
            request,
            self.template_name,
            {
                "job": job,
                "form": form,
            },
        )

    def post(self, request, pk):
        employee = request.user.employee
        job = self.get_job(pk)

        if not self.validate_job(
            request,
            job,
            employee,
        ):
            return redirect("culet:my_jobs")

        has_open_activity = Activity.objects.filter(
            job=job,
            active=True,
            end__isnull=True,
        ).exists()

        if has_open_activity:
            if not job.in_work:
                job.in_work = True
                job.save(
                    update_fields=[
                        "in_work",
                        "last_updated",
                    ],
                )

            messages.error(
                request,
                f"Job {job.barcode} is already in work.",
            )
            return redirect("culet:my_jobs")

        form = StartWorkForm(
            employee=employee,
            data=request.POST,
        )

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "job": job,
                    "form": form,
                },
            )

        step = form.cleaned_data["step"]

        with transaction.atomic():
            Activity.objects.create(
                job=job,
                employee=employee,
                step=step,
                start=timezone.now(),
            )

            job.in_work = True
            job.save(
                update_fields=[
                    "in_work",
                    "last_updated",
                ],
            )

        messages.success(
            request,
            f"Started {step} on job {job.barcode}.",
        )

        return redirect("culet:my_jobs")

class ScanToStartView(
    LoginRequiredMixin,
    generic.View,
):
    """
    Receives a scanned barcode from the My Jobs page and
    redirects the employee to the existing StartWorkView.

    StartWorkView remains responsible for validating whether
    the job can actually be started.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        employee = request.user.employee

        barcode = request.POST.get(
            "barcode",
            "",
        ).strip()

        if not barcode:
            messages.error(
                request,
                "Please scan or enter a job barcode.",
            )
            return redirect("culet:my_jobs")

        job = (
            Job.objects
            .filter(
                barcode=barcode,
                assigned_to=employee,
            )
            .first()
        )

        if job is None:
            messages.error(
                request,
                (
                    f"Barcode {barcode} was not found "
                    "in your assigned jobs."
                ),
            )
            return redirect("culet:my_jobs")

        return redirect(
            "culet:job_start",
            pk=job.pk,
        )

class InProcessRepairView(
    LoginRequiredMixin,
    generic.View,
):
    template_name = "jobs/inprocess_repair.html"

    def get_employee(self):
        return get_object_or_404(
            Employee,
            user=self.request.user,
        )

    def dispatch(self, request, *args, **kwargs):
        employee = self.get_employee()

        if not employee.can_inprocess_repair:
            messages.error(
                request,
                (
                    "You do not have permission to perform "
                    "in-process repairs."
                ),
            )
            return redirect("culet:home")

        return super().dispatch(
            request,
            *args,
            **kwargs,
        )

    def get(self, request, *args, **kwargs):
        return render(
            request,
            self.template_name,
            {
                "job": None,
                "open_scanner": True,
            },
        )

    def find_job(self, barcode):
        return (
            Job.objects
            .select_related(
                "assigned_to",
                "holder",
                "style",
                "customer",
            )
            .filter(
                barcode=barcode,
            )
            .first()
        )

    def get_validation_error(
        self,
        *,
        job,
        repairing_employee,
    ):
        if not repairing_employee.clocked_in:
            return (
                "Please clock in before accepting an "
                "in-process repair."
            )

        if not job.active:
            return (
                f"Job {job.stock_num or job.barcode} is inactive."
            )

        if job.shipped:
            return (
                f"Job {job.stock_num or job.barcode} "
                "has already been shipped."
            )

        if job.is_piecework:
            return (
                f"Job {job.stock_num or job.barcode} is currently "
                "assigned as piecework."
            )

        if job.assigned_to is None:
            return (
                f"Job {job.stock_num or job.barcode} is not "
                "assigned to an employee."
            )

        if job.holder is None:
            return (
                f"Job {job.stock_num or job.barcode} does not "
                "currently have a holder."
            )

        if job.holder == repairing_employee:
            return (
                f"You already hold job "
                f"{job.stock_num or job.barcode}."
            )

        if job.holder_id != job.assigned_to_id:
            return (
                f"Job {job.stock_num or job.barcode} cannot be "
                "transferred for an in-process repair because "
                "its holder and assigned employee do not match."
            )

        repair_is_already_active = Activity.objects.filter(
            job=job,
            step__code="repair",
            active=True,
            end__isnull=True,
        ).exists()

        if repair_is_already_active:
            return (
                f"Job {job.stock_num or job.barcode} already "
                "has an active repair activity."
            )

        if Activity.objects.filter(
            job=job,
            batch__active=True,
            active=True,
            end__isnull=True,
        ).exists():
            return (
                f"Job {job.stock_num or job.barcode} is part of "
                "an active batch and cannot be transferred for repair."
            )

        return None

    def post(self, request, *args, **kwargs):
        repairing_employee = self.get_employee()

        action = request.POST.get(
            "action",
            "lookup",
        )

        if action == "start":
            return self.start_repair(
                request=request,
                repairing_employee=repairing_employee,
            )

        return self.confirm_repair(
            request=request,
            repairing_employee=repairing_employee,
        )

    def confirm_repair(
        self,
        *,
        request,
        repairing_employee,
    ):
        barcode = request.POST.get(
            "barcode",
            "",
        ).strip()

        if not barcode:
            messages.error(
                request,
                "Please scan or enter a job barcode.",
            )
            return redirect(
                "culet:inprocess_repair",
            )

        job = self.find_job(barcode)

        if job is None:
            messages.error(
                request,
                f"No job was found with barcode {barcode}.",
            )
            return redirect(
                "culet:inprocess_repair",
            )

        validation_error = self.get_validation_error(
            job=job,
            repairing_employee=repairing_employee,
        )

        if validation_error:
            messages.error(
                request,
                validation_error,
            )
            return redirect(
                "culet:inprocess_repair",
            )

        return render(
            request,
            self.template_name,
            {
                "job": job,
                "open_scanner": False,
            },
        )

    @transaction.atomic
    def start_repair(
        self,
        *,
        request,
        repairing_employee,
    ):
        job_id = request.POST.get(
            "job_id",
        )

        job = (
            Job.objects
            .select_for_update(of=("self",))
            .select_related(
                "assigned_to",
                "holder",
                "style",
                "customer",
            )
            .filter(
                pk=job_id,
            )
            .first()
        )

        if job is None:
            messages.error(
                request,
                "The selected job could not be found.",
            )
            return redirect(
                "culet:inprocess_repair",
            )

        validation_error = self.get_validation_error(
            job=job,
            repairing_employee=repairing_employee,
        )

        if validation_error:
            messages.error(
                request,
                validation_error,
            )
            return redirect(
                "culet:inprocess_repair",
            )

        repair_step = (
            ActivityStep.objects
            .filter(
                code="repair",
            )
            .first()
        )

        if repair_step is None:
            messages.error(
                request,
                (
                    'The activity step with code "repair" '
                    "has not been configured."
                ),
            )
            return redirect(
                "culet:inprocess_repair",
            )

        previous_holder = job.holder
        started_at = timezone.now()

        open_activities = (
            Activity.objects
            .select_for_update()
            .filter(
                job=job,
                active=True,
                end__isnull=True,
            )
        )

        for activity in open_activities:
            stop_activity(
                activity,
                stopped_at=started_at,
            )

        job, movement = move_job(
            job=job,
            movement_type="repair",
            to_employee=repairing_employee,
            performed_by=repairing_employee,
        )

        Activity.objects.create(
            job=job,
            employee=repairing_employee,
            step=repair_step,
            start=started_at,
            active=True,
        )

        if not job.in_work:
            job.in_work = True
            job.save(
                update_fields=[
                    "in_work",
                    "last_updated",
                ],
            )

        messages.success(
            request,
            (
                f"In-process repair started on "
                f"{job.stock_num or job.barcode}. "
                f"Transferred from {previous_holder}."
            ),
        )

        return redirect(
            "culet:my_jobs",
        )

@login_required
@transaction.atomic
def stopWork(request, pk, job_id):
    if request.method != "POST":
        return redirect("culet:my_jobs")

    employee = request.user.employee

    act = get_object_or_404(
        Activity.objects.select_for_update(),
        id=pk,
        job_id=job_id,
        employee=employee,
        active=True,
        end__isnull=True,
    )

    if act.batch_id:
        messages.error(
            request,
            "This job is part of an active batch. Stop the batch from My Jobs.",
        )
        return redirect("culet:my_jobs")

    job = get_object_or_404(
        Job.objects.select_for_update(),
        id=job_id,
        holder=employee,
        shipped=False,
    )

    stop_activity(act)

    if act.step.code == "repair":
        return_employee = job.assigned_to

        if return_employee:
            messages.success(
                request,
                (
                    f"In-process repair on job "
                    f"{job.stock_num or job.barcode} has been completed. "
                    f"Return the job to {return_employee} "
                    "so they can receive it."
                ),
            )
        else:
            messages.success(
                request,
                (
                    f"In-process repair on job "
                    f"{job.stock_num or job.barcode} has been completed."
                ),
            )

    else:
        messages.success(
            request,
            (
                f"Job {job.barcode} has been stopped. "
                f"({act.name})"
            ),
        )

    return redirect("culet:my_jobs")

@login_required
def clock_in(request):
    if request.method != "POST":
        return redirect("culet:home")

    result = clock_in_employee(request.user.employee)
    messages.success(request, result.message)

    return redirect("culet:home")


@login_required
def clock_out(request):
    if request.method != "POST":
        return redirect("culet:home")

    result = clock_out_employee(request.user.employee)
    messages.success(request, result.message)

    return redirect("culet:home")

def hours_between(start,end):
    if not start or not end or end <= start:
        return 0
    return (end - start).total_seconds() / 3600

def merge_intervals(intervals):
    intervals = sorted(intervals, key=lambda interval: interval[0])

    merged = []

    for start, end in intervals:
        if not start or not end or end <= start:
            continue
        if not merged:
            merged.append([start, end])
            continue

        last_start, last_end = merged[-1]

        if start <= last_end:
            merged[-1][1] = max(last_end, end)
        else:
            merged.append([start, end])

    return merged


def total_interval_hours(intervals):
    return sum(hours_between(start, end) for start, end in intervals)


def clipped_interval(start, end, range_start, range_end):
    clipped_start = max(start, range_start)
    clipped_end = min(end, range_end)

    if clipped_end <= clipped_start:
        return None

    return clipped_start, clipped_end    

# def receive(request):
#     #NOT TESTED. NEEDS UPDATING FOR LIMITING WHEN THIS IS ALLOWED
#     job = Job.objects.get(barcode=request.POST["job"])
#     job.holder = request.user.employee
#     job.save()
#     messages.success(request, f"Job {job.barcode} Received")
#     return HttpResponseRedirect(reverse('culet:my_jobs'))

class MetalVendorLotDetailView(LoginRequiredMixin, generic.DetailView):
    model = MetalVendorLot
    template_name = "inventory/vendor_lot_detail.html"
    context_object_name = "vendor_lot"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["part_lots"] = (
            self.object.part_lots
            .select_related("part")
            .order_by("part__sku")
        )

        context["receipt_lines"] = (
            self.object.receipt_lines
            .select_related("receipt", "part", "metal_lot")
            .order_by("-receipt__received_at", "part__sku")
        )

        context["job_lot_allocations"] = (
            JobMetalLot.objects
            .filter(metal_lot__vendor_lot=self.object)
            .select_related(
                "job_metal",
                "job_metal__job",
                "job_metal__job__customer",
                "job_metal__job__style",
                "job_metal__part",
                "metal_lot",
                "metal_lot__part",
            )
            .order_by("job_metal__job__due", "job_metal__job__barcode")
        )

        return context

class MetalInventoryListView(LoginRequiredMixin, generic.ListView):
    model = MetalPart
    template_name = "inventory/metal_inventory_list.html"
    context_object_name = "parts"

    def get_queryset(self):
        queryset = (
            MetalPart.objects
            .annotate(
                total_qty_on_hand=Sum("metallot__qty_on_hand"),
                total_weight_on_hand=Sum("metallot__weight_on_hand"),
                total_cost=Sum("metallot__cost"),
            )
            .order_by("sku")
        )

        self.filter_form = MetalPartInventoryFilterForm(self.request.GET or None)

        if self.filter_form.is_valid():
            q = self.filter_form.cleaned_data.get("q")
            if q:
                queryset = queryset.filter(
                    Q(sku__icontains=q) |
                    Q(description__icontains=q)
                )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context
    
class MetalPartInventoryDetailView(LoginRequiredMixin, generic.DetailView):
    model = MetalPart
    template_name = "inventory/metal_part_inventory_detail.html"
    context_object_name = "part"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["metal_lots"] = (
            MetalLot.objects
            .filter(part=self.object)
            .select_related("vendor_lot", "vendor_lot__vendor")
            .order_by("-vendor_lot__received_at", "vendor_lot__lot_num")
        )
        return context

class MetalLotReceiveView(LoginRequiredMixin, generic.FormView):
    template_name = "inventory/lot_receive.html"
    form_class = MetalLotFormSet
    success_url = reverse_lazy("culet:lot_list")

    def get_form(self, form_class=None):
        if self.request.POST:
            return MetalLotFormSet(self.request.POST)
        return MetalLotFormSet()
    
    @transaction.atomic
    def form_valid(self, formset):
        #Save each non-deleted, non-empty row
        for form in formset:
            if not form.has_changed():
                continue
            if formset.can_delete and form.cleaned_data.get("DELETE"):
                continue
            form.save()
        return redirect(self.get_success_url())
    def form_invalid(self,formset):
        return self.render_to_response(self.get_context_data(form=formset))
    
class MetalLotDetailView(LoginRequiredMixin, generic.DetailView):
    model = MetalLot
    template_name = "inventory/metal_lot_detail.html"
    context_object_name = "metal_lot"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["receipt_lines"] = (
            MetalReceiptLine.objects
            .filter(metal_lot=self.object)
            .select_related("receipt", "vendor_lot", "part")
            .order_by("-receipt__received_at", "-receipt__id")
        )
        return context

class MetalReceiptCreateView(
    LoginRequiredMixin,
    generic.CreateView,
):
    model = MetalReceipt
    form_class = MetalReceiptForm
    template_name = "inventory/metal_receipt_create.html"
    success_url = reverse_lazy(
        "culet:metal_vendor_lot_list"
    )

    def get_line_formset(self):
        if self.request.method == "POST":
            return MetalReceiptLineFormSet(
                self.request.POST,
                prefix="lines",
            )

        return MetalReceiptLineFormSet(
            prefix="lines",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if "line_formset" not in context:
            context["line_formset"] = (
                self.get_line_formset()
            )

        return context

    @transaction.atomic
    def form_valid(self, form):
        line_formset = self.get_line_formset()

        if not line_formset.is_valid():
            return self.render_to_response(
                self.get_context_data(
                    form=form,
                    line_formset=line_formset,
                )
            )

        self.object = form.save(commit=False)
        self.object.received_by = self.request.user
        self.object.save()

        lot_num = form.cleaned_data["lot_num"]
        vendor = form.cleaned_data["vendor"]

        vendor_lot, _created = (
            MetalVendorLot.objects.get_or_create(
                vendor=vendor,
                lot_num=lot_num,
            )
        )

        for line_form in line_formset:
            if not line_form.has_changed():
                continue

            if (
                line_formset.can_delete
                and line_form.cleaned_data.get("DELETE")
            ):
                continue

            line = line_form.save(commit=False)
            line.receipt = self.object
            line.vendor_lot = vendor_lot

            metal_lot, _created = (
                MetalLot.objects.get_or_create(
                    vendor_lot=vendor_lot,
                    part=line.part,
                    defaults={
                        "qty_on_hand": Decimal("0"),
                        "weight_on_hand": Decimal("0"),
                        "cost": Decimal("0"),
                    },
                )
            )

            update_kwargs = {
                "qty_on_hand": (
                    F("qty_on_hand")
                    + (
                        line.qty_received
                        or Decimal("0")
                    )
                ),
                "weight_on_hand": (
                    F("weight_on_hand")
                    + (
                        line.weight_received
                        or Decimal("0")
                    )
                ),
            }

            if line.cost is not None:
                update_kwargs["cost"] = line.cost

            MetalLot.objects.filter(
                pk=metal_lot.pk
            ).update(**update_kwargs)

            line.metal_lot = metal_lot
            line.save()

        return redirect(self.get_success_url())

    def form_invalid(self, form):
        return self.render_to_response(
            self.get_context_data(
                form=form,
                line_formset=self.get_line_formset(),
            )
        )
    
class MetalVendorLotListView(
    LoginRequiredMixin,
    generic.ListView,
):
    model = MetalVendorLot
    template_name = "inventory/vendor_lot_list.html"
    context_object_name = "vendor_lots"
    paginate_by = 50

    SORT_FIELDS = {
        "lot_num": "lot_num",
        "vendor": "vendor__name",
        "received_at": "received_at",
    }

    DEFAULT_SORT = "received_at"
    DEFAULT_DIRECTION = "desc"

    def get_queryset(self):
        queryset = (
            MetalVendorLot.objects
            .select_related("vendor")
        )

        self.filterset = MetalVendorLotFilter(
            self.request.GET,
            queryset=queryset,
        )

        queryset = self.filterset.qs

        sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )

        direction = self.request.GET.get(
            "direction",
            self.DEFAULT_DIRECTION,
        )

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in {"asc", "desc"}:
            direction = self.DEFAULT_DIRECTION

        order_field = self.SORT_FIELDS[sort]

        if direction == "desc":
            order_field = f"-{order_field}"

        return queryset.order_by(
            order_field,
            "-pk",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["filter"] = self.filterset

        current_sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )

        current_direction = self.request.GET.get(
            "direction",
            self.DEFAULT_DIRECTION,
        )

        if current_sort not in self.SORT_FIELDS:
            current_sort = self.DEFAULT_SORT

        if current_direction not in {"asc", "desc"}:
            current_direction = self.DEFAULT_DIRECTION

        context["current_sort"] = current_sort
        context["current_direction"] = current_direction

        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        query_params.pop("sort", None)
        query_params.pop("direction", None)

        sort_links = {}

        for field_name in self.SORT_FIELDS:
            params = query_params.copy()

            if current_sort == field_name:
                next_direction = (
                    "desc"
                    if current_direction == "asc"
                    else "asc"
                )
            else:
                next_direction = "asc"

            params["sort"] = field_name
            params["direction"] = next_direction

            sort_links[field_name] = params.urlencode()

        context["sort_links"] = sort_links
        context["query_params"] = query_params.urlencode()

        return context

class MetalVendorLotDetailView(LoginRequiredMixin, generic.DetailView):
    model = MetalVendorLot
    template_name = "inventory/vendor_lot_detail.html"
    context_object_name = "vendor_lot"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["part_lots"] = (
            self.object.part_lots
            .select_related("part")
            .order_by("part__sku")
        )

        context["receipt_lines"] = (
            self.object.receipt_lines
            .select_related("receipt", "part", "metal_lot")
            .order_by("-receipt__received_at", "part__sku")
        )

        return context

class MetalReceiptDetailView(LoginRequiredMixin, generic.DetailView):
    model = MetalReceipt
    template_name = "inventory/metal_receipt_detail.html"
    context_object_name = "receipt"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["lines"] = (
            self.object.lines
            .select_related("vendor_lot", "part", "metal_lot")
            .order_by("part__sku")
        )

        return context
    
class MetalReceiptDetailView(LoginRequiredMixin, generic.DetailView):
    model = MetalReceipt
    template_name = "inventory/metal_receipt_detail.html"
    context_object_name = "receipt"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["lines"] = (
            self.object.lines
            .select_related("part", "metal_lot")
            .all()
        )

        return context
    
class MetalReceiptListView(LoginRequiredMixin, generic.ListView):
    model = MetalReceipt
    template_name = "inventory/metal_receipt_list.html"
    context_object_name = "receipts"
    ordering = ["-received_at"]

    def get_queryset(self):
        return (
            MetalReceipt.objects
            .select_related("vendor", "received_by")
            .order_by("-received_at")
        )
    
class InventoryDashboardView(LoginRequiredMixin, generic.TemplateView):
    template_name = "inventory/inventory_dashboard.html"

class JobWeightCreateView(LoginRequiredMixin, generic.CreateView):
    model = JobWeight
    form_class = JobWeightForm
    template_name = "jobs/weight_create.html"

    def dispatch(self, request, *args, **kwargs):
        self.job = get_object_or_404(Job, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job"] = self.job
        context["job_weights"] = self.job.weights.order_by("-created_at", "-id")
        return context

    def form_valid(self, form):
        form.instance.job = self.job
        form.instance.recorded_by = self.request.user
        self.object = form.save()
        messages.success(self.request, f"Weight recorded for job {self.job.barcode}.")
        return redirect(self.job.get_absolute_url())
    
class JobWeightLookupView(LoginRequiredMixin, generic.FormView):
    template_name = "jobs/weight_lookup.html"
    form_class = JobWeightLookupForm

    def form_valid(self, form):
        barcode = form.cleaned_data.get("barcode")
        stock_num = form.cleaned_data.get("stock_num")

        if barcode is not None and stock_num:
            barcode_job = Job.objects.filter(barcode=barcode).first()
            stock_num_job = Job.objects.filter(stock_num=stock_num).first()

            if not barcode_job or not stock_num_job:
                form.add_error(None, "No job found with the provided number.")
                return self.form_invalid(form)

            if barcode_job != stock_num_job:
                form.add_error(
                    None,
                    "The barcode and stock number do not belong to the same job.",
                )
                return self.form_invalid(form)

            job = barcode_job
        elif barcode is not None:
            job = Job.objects.filter(barcode=barcode).first()
        else:
            job = Job.objects.filter(stock_num=stock_num).first()

        if not job:
            form.add_error(None, "No job found with the provided number.")
            return self.form_invalid(form)

        return redirect("culet:job_weight_create", pk=job.pk)

# Reports Below This Line

class InactiveJobsReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/inactive_jobs.html"

    SORT_FIELDS = {
        "stock_num": "stock_num",
        "customer": "customer__name",
        "style": "style__name",
        "status": "status__sort_order",
        "location": "location__name",
        "assigned_to": "assigned_to__user__last_name",
        "holder": "holder__user__last_name",
        "last_activity": "inactive_since",
        "days_inactive": "inactive_since",
        "created": "created",
    }

    DEFAULT_SORT = "days_inactive"
    DEFAULT_DIRECTION = "desc"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        report_form = InactiveJobsReportForm(
            self.request.GET or None
        )

        jobs = Job.objects.none()
        cutoff = None
        report_filter = None

        sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )
        direction = self.request.GET.get(
            "direction",
            self.DEFAULT_DIRECTION,
        )

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in ["asc", "desc"]:
            direction = self.DEFAULT_DIRECTION

        if report_form.is_valid():
            days = report_form.cleaned_data["days"]
            cutoff = timezone.now() - timedelta(days=days)

            jobs = (
                Job.objects
                .filter(
                    active=True,
                    shipped=False,
                )
                .annotate(
                    last_activity_start=Max(
                        "activity__start"
                    ),
                )
                .annotate(
                    inactive_since=Coalesce(
                        "last_activity_start",
                        "created",
                    ),
                )
                .filter(
                    inactive_since__lt=cutoff,
                )
                .select_related(
                    "customer",
                    "style",
                    "assigned_to__user",
                    "assigned_to__department",
                    "holder__user",
                    "holder__department",
                    "status",
                    "location",
                )
            )

            report_filter = JobReportFilter(
                self.request.GET,
                queryset=jobs,
            )

            jobs = report_filter.qs

            order_field = self.SORT_FIELDS[sort]

            # A greater number of inactive days means an older
            # inactive_since date. Reverse the date direction when
            # sorting by the displayed days-inactive value.
            if sort == "days_inactive":
                if direction == "asc":
                    order_field = f"-{order_field}"
            elif direction == "desc":
                order_field = f"-{order_field}"

            jobs = jobs.order_by(
                order_field,
                "stock_num",
            )

            now = timezone.now()

            for job in jobs:
                inactive_since = job.inactive_since

                if inactive_since:
                    job.days_inactive = (
                        now.date() -
                        timezone.localtime(
                            inactive_since
                        ).date()
                    ).days
                else:
                    job.days_inactive = 0

        context["form"] = report_form
        context["filter"] = report_filter
        context["jobs"] = jobs
        context["cutoff"] = cutoff
        context["current_sort"] = sort
        context["current_direction"] = direction

        sort_links = {}

        for key in self.SORT_FIELDS:
            params = self.request.GET.copy()
            params.pop("page", None)

            next_direction = "asc"

            if (
                sort == key
                and direction == "asc"
            ):
                next_direction = "desc"

            params["sort"] = key
            params["direction"] = next_direction

            sort_links[key] = params.urlencode()

        context["sort_links"] = sort_links

        return context
    
class ClockedInIdleEmployeesReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/clocked_in_idle_employees.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        last_activity = (
            Activity.objects
            .filter(employee=OuterRef("pk"))
            .order_by("-start", "-id")
        )

        employees = (
            Employee.objects
            .filter(clocked_in=True,
                    role__requires_clock_in=True,)
            .exclude(
                activity__active=True,
                activity__end__isnull=True,
            )
            .select_related("user", "department", "role")
            .annotate(
                last_activity_name=Subquery(last_activity.values("name")[:1]),
                last_activity_start=Subquery(last_activity.values("start")[:1]),
                last_activity_end=Subquery(last_activity.values("end")[:1]),
                last_activity_job_barcode=Subquery(last_activity.values("job__barcode")[:1]),
            )
            .order_by("user__last_name", "user__first_name")
            .distinct()
        )

        context["employees"] = employees
        return context

class WeightLossByStyleReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/weight_loss_by_style.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = WeightLossByStyleReportForm(self.request.GET or None)

        jobs = (
            Job.objects
            .filter(weights__isnull=False)
            .select_related("style", "customer")
            .prefetch_related("weights__step")
            .distinct()
            .order_by("style__name", "barcode")
        )

        if form.is_valid():
            style = form.cleaned_data.get("style")
            if style:
                jobs = jobs.filter(style=style)

        style_data = defaultdict(lambda: {
            "style": None,
            "job_count": 0,
            "initial_total": Decimal("0"),
            "latest_total": Decimal("0"),
            "loss_total": Decimal("0"),
        })

        step_data = defaultdict(lambda: {
            "style": None,
            "step": None,
            "loss_total": Decimal("0"),
            "event_count": 0,
        })

        for job in jobs:
            weights = list(job.weights.all().order_by("created_at", "id"))

            if len(weights) < 2:
                continue

            initial = weights[0].total_weight
            latest = weights[-1].total_weight

            if not initial or initial <= 0:
                continue

            loss = initial - latest

            style_key = job.style_id
            style_data[style_key]["style"] = job.style
            style_data[style_key]["job_count"] += 1
            style_data[style_key]["initial_total"] += initial
            style_data[style_key]["latest_total"] += latest
            style_data[style_key]["loss_total"] += loss

            for previous_weight, current_weight in zip(weights, weights[1:]):
                interval_loss = previous_weight.total_weight - current_weight.total_weight

                if interval_loss <= 0:
                    continue

                step_name = current_weight.step.name if current_weight.step else "No Step Recorded"
                step_key = (job.style_id, step_name)

                step_data[step_key]["style"] = job.style
                step_data[step_key]["step"] = step_name
                step_data[step_key]["loss_total"] += interval_loss
                step_data[step_key]["event_count"] += 1

        style_rows = []
        for row in style_data.values():
            row["loss_percent"] = (
                row["loss_total"] / row["initial_total"]
            ) * Decimal("100")
            style_rows.append(row)

        style_rows.sort(
            key=lambda row: row["loss_percent"],
            reverse=True,
        )

        step_rows = list(step_data.values())

        for row in step_rows:
            if row["event_count"]:
                row["avg_loss"] = row["loss_total"] / row["event_count"]
            else:
                row["avg_loss"] = Decimal("0")

        step_rows.sort(
            key=lambda row: row["loss_total"],
            reverse=True,
        )

        context["form"] = form
        context["style_rows"] = style_rows
        context["step_rows"] = step_rows
        return context

class EmployeeActivityReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/employee_activity.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = EmployeeActivityReportForm(self.request.GET or None)
        activities = Activity.objects.none()
        total_hours = 0

        if form.is_valid():
            employee = form.cleaned_data["employee"]
            style = form.cleaned_data.get("style")
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]

            activities = (
                Activity.objects
                .filter(
                    employee=employee,
                    end__isnull=False,
                    end__date__gte=start_date,
                    end__date__lte=end_date,
                )
                .select_related(
                    "employee__user",
                    "job",
                    "job__customer",
                    "job__style",
                    "step",
                )
                .order_by("-end", "-start")
            )

            if style:
                activities = activities.filter(job__style=style)

            total_hours = sum(
                activity.duration or 0
                for activity in activities
            )

        context["form"] = form
        context["activities"] = activities
        context["total_hours"] = total_hours

        return context
    
class TimeClockReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/time_clock_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = TimeClockReportForm(self.request.GET or None)
        employee_rows = []
        report_totals = {
            "clocked_hours": 0,
            "active_work_hours": 0,
            "job_labor_hours": 0,
            "downtime_hours": 0,
        }

        if form.is_valid():
            selected_employee = form.cleaned_data.get("employee")
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]

            start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
            end_dt = timezone.make_aware(datetime.combine(end_date, time.max))

            employees = (
                Employee.objects
                .select_related("user", "department", "role")
                .filter(role__requires_clock_in=True)
                .order_by("user__last_name", "user__first_name")
            )

            if selected_employee:
                employees = employees.filter(pk=selected_employee.pk)

            for employee in employees:
                clock_entries = (
                    TimeClock.objects
                    .filter(employee=employee, clock_in__lte=end_dt)
                    .filter(Q(clock_out__gte=start_dt) | Q(clock_out__isnull=True))
                    .order_by("clock_in")
                )

                activities = (
                    Activity.objects
                    .filter(employee=employee, start__lte=end_dt)
                    .filter(Q(end__gte=start_dt) | Q(end__isnull=True))
                    .select_related("job", "step")
                    .order_by("start")
                )

                days = {}
                current_day = start_date

                while current_day <= end_date:
                    day_start = timezone.make_aware(datetime.combine(current_day, time.min))
                    day_end = timezone.make_aware(datetime.combine(current_day, time.max))

                    clock_intervals = []
                    activity_intervals = []
                    job_labor_hours = 0

                    day_clock_entries = []

                    for entry in clock_entries:
                        clock_out = entry.clock_out or timezone.now()
                        interval = clipped_interval(
                            entry.clock_in,
                            clock_out,
                            day_start,
                            day_end,
                        )

                        if interval:
                            clock_intervals.append(interval)

                            day_clock_entries.append({
                                "timeclock": entry,
                                "clock_in": entry.clock_in,
                                "clock_out": entry.clock_out,
                                "duration_hours": hours_between(interval[0], interval[1]),
                            })

                    for activity in activities:
                        activity_end = activity.end or timezone.now()
                        interval = clipped_interval(
                            activity.start,
                            activity_end,
                            day_start,
                            day_end,
                        )

                        if interval:
                            activity_intervals.append(interval)

                            # This is summed job labor time.
                            # Overlapping jobs DO double-count here.
                            job_labor_hours += hours_between(interval[0], interval[1])

                    merged_clock_intervals = merge_intervals(clock_intervals)
                    merged_activity_intervals = merge_intervals(activity_intervals)

                    clocked_hours = total_interval_hours(merged_clock_intervals)

                    # This is actual active-work coverage.
                    # Overlapping jobs DO NOT double-count here.
                    active_work_hours = total_interval_hours(merged_activity_intervals)

                    downtime_hours = max(clocked_hours - active_work_hours, 0)

                    utilization_percent = None
                    if clocked_hours:
                        utilization_percent = (active_work_hours / clocked_hours) * 100

                    if clocked_hours or active_work_hours or job_labor_hours:
                        days[current_day] = {
                            "date": current_day,
                            "clock_entries": day_clock_entries,
                            "clocked_hours": clocked_hours,
                            "active_work_hours": active_work_hours,
                            "job_labor_hours": job_labor_hours,
                            "downtime_hours": downtime_hours,
                            "utilization_percent": utilization_percent,
                        }

                    report_totals["clocked_hours"] += clocked_hours
                    report_totals["active_work_hours"] += active_work_hours
                    report_totals["job_labor_hours"] += job_labor_hours
                    report_totals["downtime_hours"] += downtime_hours

                    current_day += timedelta(days=1)

                employee_clocked_hours = sum(
                    day["clocked_hours"] for day in days.values()
                )
                employee_active_work_hours = sum(
                    day["active_work_hours"] for day in days.values()
                )
                employee_job_labor_hours = sum(
                    day["job_labor_hours"] for day in days.values()
                )
                employee_downtime_hours = sum(
                    day["downtime_hours"] for day in days.values()
                )

                employee_utilization_percent = None
                if employee_clocked_hours:
                    employee_utilization_percent = (
                        employee_active_work_hours / employee_clocked_hours
                    ) * 100

                employee_rows.append({
                    "employee": employee,
                    "days": list(days.values()),
                    "clocked_hours": employee_clocked_hours,
                    "active_work_hours": employee_active_work_hours,
                    "job_labor_hours": employee_job_labor_hours,
                    "downtime_hours": employee_downtime_hours,
                    "utilization_percent": employee_utilization_percent,
                })

        context["form"] = form
        context["employee_rows"] = employee_rows
        context["report_totals"] = report_totals

        return context
    
class TimeClockUpdateView(LoginRequiredMixin, generic.UpdateView):
    model = TimeClock
    form_class = TimeClockEditForm
    template_name = "timeclock/time_clock_edit.html"

    def get_success_url(self):
        return reverse("culet:report_time_clock")
    

class LateJobsReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/late_jobs.html"

    SORT_FIELDS = {
        "stock_num": "stock_num",
        "customer": "customer__name",
        "style": "style__name",
        "due": "due",
        "days_late": "due",
        "status": "status__sort_order",
        "location": "location__name",
        "assigned_to": "assigned_to__user__last_name",
        "holder": "holder__user__last_name",
    }

    DEFAULT_SORT = "days_late"
    DEFAULT_DIRECTION = "desc"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = timezone.localdate()

        jobs = (
            Job.objects
            .filter(
                active=True,
                shipped=False,
                due__lt=today,
            )
            .select_related(
                "customer",
                "style",
                "assigned_to__user",
                "assigned_to__department",
                "holder__user",
                "holder__department",
                "status",
                "location",
            )
        )

        report_filter = JobReportFilter(
            self.request.GET,
            queryset=jobs,
        )

        jobs = report_filter.qs

        sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )
        direction = self.request.GET.get(
            "direction",
            self.DEFAULT_DIRECTION,
        )

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in ["asc", "desc"]:
            direction = self.DEFAULT_DIRECTION

        order_field = self.SORT_FIELDS[sort]

        # More days late means an earlier due date, so the visible
        # days-late direction is opposite the due-date direction.
        if sort == "days_late":
            if direction == "asc":
                order_field = f"-{order_field}"
        elif direction == "desc":
            order_field = f"-{order_field}"

        jobs = jobs.order_by(
            order_field,
            "stock_num",
        )

        job_rows = [
            {
                "job": job,
                "days_late": (today - job.due).days,
            }
            for job in jobs
        ]

        sort_links = {}

        for key in self.SORT_FIELDS:
            params = self.request.GET.copy()
            params.pop("page", None)

            next_direction = "asc"

            if (
                sort == key
                and direction == "asc"
            ):
                next_direction = "desc"

            params["sort"] = key
            params["direction"] = next_direction

            sort_links[key] = params.urlencode()

        context["today"] = today
        context["filter"] = report_filter
        context["job_rows"] = job_rows
        context["current_sort"] = sort
        context["current_direction"] = direction
        context["sort_links"] = sort_links

        return context

class JobsByHolderReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/jobs_by_holder.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = JobsByHolderReportForm(self.request.GET or None)

        employees = (
            Employee.objects
            .filter(
                held_jobs__active=True,
                held_jobs__shipped=False,
            )
            .select_related(
                "user",
                "department",
                "role",
            )
            .distinct()
            .order_by(
                "department__name",
                "user__last_name",
                "user__first_name",
            )
        )

        selected_employee = None

        if form.is_valid():
            selected_department = form.cleaned_data.get("department")
            selected_employee = form.cleaned_data.get("employee")

            if selected_department:
                employees = employees.filter(
                    department=selected_department
                )

            if selected_employee:
                employees = employees.filter(
                    pk=selected_employee.pk
                )

        department_rows = []

        departments = {}

        for employee in employees:
            jobs = (
                Job.objects
                .filter(
                    holder=employee,
                    active=True,
                    shipped=False,
                )
                .select_related(
                    "customer",
                    "style",
                    "status",
                    "location",
                )
                .order_by("due", "barcode")
            )

            dept = employee.department

            if dept not in departments:
                departments[dept] = {
                    "department": dept,
                    "employee_rows": [],
                }

            departments[dept]["employee_rows"].append({
                "employee": employee,
                "jobs": jobs,
                "job_count": jobs.count(),
            })

        department_rows = list(departments.values())

        context["form"] = form
        context["department_rows"] = department_rows
        context["selected_employee"] = selected_employee
        context["total_jobs"] = Job.objects.filter(holder__isnull=False,active=True,shipped=False,).count()
        return context
    
class BulkJobShipView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "jobs/job_ship_bulk.html"
    success_url = reverse_lazy("culet:job_ship_bulk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["form"] = kwargs.get(
            "form",
            BulkJobShipForm(),
        )

        return context

    @staticmethod
    def parse_identifiers(value):
        """
        Split textarea contents on commas, whitespace, tabs,
        carriage returns, or newlines.
        """
        if not value:
            return []

        return [
            item.strip()
            for item in re.split(r"[\s,]+", value)
            if item.strip()
        ]

    def render_invalid_form(self, form):
        return self.render_to_response(
            self.get_context_data(
                form=form,
            )
        )

    def post(self, request, *args, **kwargs):
        form = BulkJobShipForm(
            request.POST,
        )

        if not form.is_valid():
            return self.render_invalid_form(form)

        employee = get_object_or_404(
            Employee,
            user=request.user,
        )

        notes = form.cleaned_data.get(
            "notes",
            "",
        )

        barcodes = self.parse_identifiers(
            form.cleaned_data.get(
                "barcodes",
                "",
            )
        )

        stock_numbers = self.parse_identifiers(
            form.cleaned_data.get(
                "stock_numbers",
                "",
            )
        )

        duplicate_barcodes = self.find_duplicates(
            barcodes,
        )

        duplicate_stock_numbers = self.find_duplicates(
            stock_numbers,
        )

        if duplicate_barcodes:
            messages.error(
                request,
                (
                    "Duplicate barcode(s): "
                    + ", ".join(duplicate_barcodes)
                ),
            )

        if duplicate_stock_numbers:
            messages.error(
                request,
                (
                    "Duplicate stock number(s): "
                    + ", ".join(duplicate_stock_numbers)
                ),
            )

        if duplicate_barcodes or duplicate_stock_numbers:
            return self.render_invalid_form(form)

        jobs = []
        entered_identifiers = {}

        missing_barcodes = []
        missing_stock_numbers = []

        already_shipped = []
        in_work = []

        for barcode in barcodes:
            job = (
                Job.objects
                .select_related(
                    "assigned_to",
                    "holder",
                )
                .filter(
                    barcode__iexact=barcode,
                )
                .first()
            )

            if not job:
                missing_barcodes.append(barcode)
                continue

            entered_identifiers.setdefault(
                job.pk,
                [],
            ).append(
                f"barcode {barcode}"
            )

            if job not in jobs:
                jobs.append(job)

        for stock_number in stock_numbers:
            job = (
                Job.objects
                .select_related(
                    "assigned_to",
                    "holder",
                )
                .filter(
                    stock_num__iexact=stock_number,
                )
                .first()
            )

            if not job:
                missing_stock_numbers.append(
                    stock_number,
                )
                continue

            entered_identifiers.setdefault(
                job.pk,
                [],
            ).append(
                f"stock number {stock_number}"
            )

            if job not in jobs:
                jobs.append(job)

        if missing_barcodes:
            messages.error(
                request,
                (
                    "No job found for barcode(s): "
                    + ", ".join(missing_barcodes)
                ),
            )

        if missing_stock_numbers:
            messages.error(
                request,
                (
                    "No job found for stock number(s): "
                    + ", ".join(missing_stock_numbers)
                ),
            )

        duplicate_jobs = {
            job_pk: identifiers
            for job_pk, identifiers
            in entered_identifiers.items()
            if len(identifiers) > 1
        }

        if duplicate_jobs:
            duplicate_descriptions = []

            for job in jobs:
                identifiers = duplicate_jobs.get(
                    job.pk,
                )

                if not identifiers:
                    continue

                duplicate_descriptions.append(
                    (
                        f"{job.stock_num} "
                        f"({', '.join(identifiers)})"
                    )
                )

            messages.error(
                request,
                (
                    "The same job was entered more than once: "
                    + "; ".join(duplicate_descriptions)
                ),
            )

        if (
            missing_barcodes
            or missing_stock_numbers
            or duplicate_jobs
        ):
            return self.render_invalid_form(form)

        job_ids = [
            job.pk
            for job in jobs
        ]

        jobs_with_open_activity = set(
            Activity.objects.filter(
                job_id__in=job_ids,
                active=True,
                end__isnull=True,
            ).values_list(
                "job_id",
                flat=True,
            )
        )

        valid_jobs = []

        for job in jobs:
            if job.shipped:
                already_shipped.append(
                    job.stock_num,
                )
                continue

            if job.pk in jobs_with_open_activity:
                in_work.append(
                    job.stock_num,
                )
                continue

            valid_jobs.append(job)

        if already_shipped:
            messages.error(
                request,
                (
                    "Already shipped job(s): "
                    + ", ".join(already_shipped)
                ),
            )

        if in_work:
            messages.error(
                request,
                (
                    "These jobs are currently being worked on "
                    "and must be stopped before shipping: "
                    + ", ".join(in_work)
                ),
            )

        if already_shipped or in_work:
            return self.render_invalid_form(form)

        shipped_status = get_object_or_404(
            JobStatus,
            name__iexact="Shipped",
        )

        shipped_count = 0

        with transaction.atomic():
            for job in valid_jobs:
                # Clear the job's assignment and record movement.
                job, assignment_movement = move_job(
                    job=job,
                    movement_type="shipped-unassigned",
                    to_employee=None,
                    performed_by=employee,
                )

                # Clear physical possession and record movement.
                job, holder_movement = move_job(
                    job=job,
                    movement_type="shipped-released",
                    to_employee=None,
                    performed_by=employee,
                )

                job.shipped = True
                job.active = False
                job.in_work = False
                job.status = shipped_status

                job.save(
                    update_fields=[
                        "shipped",
                        "active",
                        "in_work",
                        "status",
                        "last_updated",
                    ],
                )

                JobShip.objects.create(
                    job=job,
                    shipped_by=employee,
                    notes=notes,
                )

                shipped_count += 1

        job_word = (
            "job"
            if shipped_count == 1
            else "jobs"
        )

        messages.success(
            request,
            f"Shipped {shipped_count} {job_word}.",
        )

        return redirect(self.success_url)

    @staticmethod
    def find_duplicates(values):
        """
        Return case-insensitive duplicate values while preserving
        a readable version of each value.
        """
        counts = {}
        display_values = {}

        for value in values:
            normalized_value = value.casefold()

            counts[normalized_value] = (
                counts.get(normalized_value, 0)
                + 1
            )

            display_values.setdefault(
                normalized_value,
                value,
            )

        return sorted(
            display_values[normalized_value]
            for normalized_value, count
            in counts.items()
            if count > 1
        )
    
#Printing
class JobEnvelopePrintView(LoginRequiredMixin, generic.DetailView):
    model = Job
    template_name = "jobs/print_envelopes.html"
    context_object_name = "job"

    def get_queryset(self):
        return (
            Job.objects
            .select_related("style", "customer")
            .prefetch_related(
                "job_stones__stone_type",
                "job_stones__stone_shape",
                "job_metals__part",
                "job_metals__metal_type",
                "job_findings__finding",
                "job_findings__finding__metal_type",
                "job_findings__finding__finding_type",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["jobs"] = [self.object]
        context["auto_print"] = True
        return context


class JobEnvelopePrintFormView(
    LoginRequiredMixin,
    generic.TemplateView
):
    template_name = "jobs/print_envelope_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["recent_jobs"] = (
            Job.objects
            .select_related(
                "style",
                "customer",
            )
            .order_by(
                "-created",
                "-pk",
            )[:25]
        )

        return context

class JobEnvelopePrintBatchView(
    LoginRequiredMixin,
    generic.TemplateView
):
    template_name = "jobs/print_envelopes.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        mode = self.request.GET.get("mode")
        jobs = Job.objects.none()
        error_message = None

        if mode == "range":
            start_stock_num = (
                self.request.GET
                .get("start", "")
                .strip()
            )

            end_stock_num = (
                self.request.GET
                .get("end", "")
                .strip()
            )

            if not start_stock_num:
                error_message = "Enter a stock number."

            else:
                try:
                    start_job = Job.objects.get(
                        stock_num=start_stock_num
                    )

                except Job.DoesNotExist:
                    error_message = (
                        f'No job was found with stock number '
                        f'"{start_stock_num}".'
                    )

                except Job.MultipleObjectsReturned:
                    error_message = (
                        f'More than one job uses stock number '
                        f'"{start_stock_num}".'
                    )

                else:
                    if not end_stock_num:
                        jobs = Job.objects.filter(
                            pk=start_job.pk
                        )

                    else:
                        try:
                            end_job = Job.objects.get(
                                stock_num=end_stock_num
                            )

                        except Job.DoesNotExist:
                            error_message = (
                                f'No job was found with stock number '
                                f'"{end_stock_num}".'
                            )

                        except Job.MultipleObjectsReturned:
                            error_message = (
                                f'More than one job uses stock number '
                                f'"{end_stock_num}".'
                            )

                        else:
                            lower_barcode, upper_barcode = sorted(
                                [
                                    start_job.barcode,
                                    end_job.barcode,
                                ]
                            )

                            jobs = (
                                Job.objects
                                .filter(
                                    barcode__gte=lower_barcode,
                                    barcode__lte=upper_barcode,
                                )
                                .order_by("barcode")
                            )

        elif mode == "today":
            today = timezone.localdate()

            jobs = (
                Job.objects
                .filter(
                    created__date=today,
                )
                .order_by("barcode")
            )

        elif mode == "selected":
            selected_job_ids = self.request.GET.getlist(
                "job_ids"
            )

            valid_job_ids = []

            for job_id in selected_job_ids:
                try:
                    valid_job_ids.append(int(job_id))
                except (TypeError, ValueError):
                    continue

            if not valid_job_ids:
                error_message = (
                    "Select at least one job to print."
                )

            else:
                jobs = (
                    Job.objects
                    .filter(pk__in=valid_job_ids)
                    .order_by("barcode")
                )

                if not jobs.exists():
                    error_message = (
                        "None of the selected jobs could be found."
                    )

        else:
            error_message = (
                "Choose a method for printing job envelopes."
            )

        jobs = (
            jobs
            .select_related(
                "style",
                "customer",
            )
            .prefetch_related(
                "job_stones__stone_type",
                "job_stones__stone_shape",
                "job_metals__part",
                "job_metals__metal_type",
                "job_findings__finding",
                "job_findings__finding__metal_type",
                "job_findings__finding__finding_type",
            )
        )

        context["jobs"] = jobs
        context["error_message"] = error_message
        context["auto_print"] = (
            jobs.exists()
            and error_message is None
        )

        return context
    
class JobTransferMemoCreateView(
    LoginRequiredMixin,
    generic.FormView,
):
    template_name = "jobs/job_transfer_memo_form.html"
    form_class = JobTransferMemoForm

    def get_employee(self):
        return get_object_or_404(
            Employee,
            user=self.request.user,
        )

    def form_valid(self, form):
        created_by = self.get_employee()
        assigned_to = form.cleaned_data["assigned_to"]

        raw_scanned_values = [
            value.strip()
            for value in (
                form.cleaned_data["scanned_jobs"]
                .splitlines()
            )
            if value.strip()
        ]

        if not raw_scanned_values:
            form.add_error(
                "scanned_jobs",
                "Please scan at least one job.",
            )
            return self.form_invalid(form)

        invalid_scans = [
            value
            for value in raw_scanned_values
            if not value.isdigit()
        ]

        if invalid_scans:
            form.add_error(
                "scanned_jobs",
                (
                    "These values are not valid numeric barcodes: "
                    + ", ".join(invalid_scans)
                ),
            )
            return self.form_invalid(form)

        scanned_values = [
            int(value)
            for value in raw_scanned_values
        ]

        duplicate_scans = sorted({
            value
            for value in scanned_values
            if scanned_values.count(value) > 1
        })

        if duplicate_scans:
            form.add_error(
                "scanned_jobs",
                (
                    "The following barcodes were scanned "
                    "more than once: "
                    + ", ".join(
                        str(value)
                        for value in duplicate_scans
                    )
                ),
            )
            return self.form_invalid(form)

        jobs = list(
            Job.objects
            .filter(
                barcode__in=scanned_values,
                shipped=False,
            )
            .select_related(
                "assigned_to",
                "holder",
            )
        )

        jobs_by_barcode = {
            job.barcode: job
            for job in jobs
        }

        missing_jobs = [
            scanned_value
            for scanned_value in scanned_values
            if scanned_value not in jobs_by_barcode
        ]

        if missing_jobs:
            form.add_error(
                "scanned_jobs",
                (
                    "These scanned jobs were not found "
                    "or are already shipped: "
                    + ", ".join(
                        str(value)
                        for value in missing_jobs
                    )
                ),
            )
            return self.form_invalid(form)

        assigned_count = 0

        with transaction.atomic():
            memo = form.save(
                commit=False,
            )

            memo.created_by = created_by
            memo.save()

            for scanned_value in scanned_values:
                job = jobs_by_barcode[scanned_value]

                JobTransferMemoLine.objects.create(
                    memo=memo,
                    job=job,
                )

                job, movement = move_job(
                    job=job,
                    movement_type="assigned",
                    to_employee=assigned_to,
                    performed_by=created_by,
                )

                if movement is not None:
                    assigned_count += 1

        job_count = len(scanned_values)
        job_word = "job" if job_count == 1 else "jobs"

        messages.success(
            self.request,
            (
                f"{memo.memo_num} created with "
                f"{job_count} {job_word} assigned to "
                f"{assigned_to}. "
                f"{assigned_count} assignment "
                f"{'was' if assigned_count == 1 else 'changes were'} "
                f"recorded."
            ),
        )

        return render(
            self.request,
            "memos/create_redirect.html",
            {
                "print_url": reverse(
                    "culet:job_transfer_memo_print",
                    kwargs={
                        "pk": memo.pk,
                    },
                ),
                "home_url": reverse(
                    "culet:home",
                ),
            },
        )


class JobTransferMemoPrintView(
    LoginRequiredMixin,
    generic.DetailView,
):
    model = JobTransferMemo
    template_name = "memos/memo_print.html"
    context_object_name = "memo"

    def get_queryset(self):
        return (
            JobTransferMemo.objects
            .select_related(
                "created_by",
                "created_by__user",
                "assigned_to",
                "assigned_to__user",
            )
            .prefetch_related(
                "lines__job",
                "lines__job__style",
                "lines__job__customer",
            )
        )
    
class PieceworkPrintView(LoginRequiredMixin, generic.DetailView):
    model = PieceworkMemo
    template_name = "memos/memo_print.html"
    context_object_name = "memo"

    def get_queryset(self):
        return (
            PieceworkMemo.objects
            .select_related(
                "created_by",
                "created_by__user",
                "assigned_to",
                "assigned_to__user",
                "from_location",
                "to_location",
                "returned_by",
                "returned_by__user",
            )
            .prefetch_related(
                "lines__job",
                "lines__job__style",
                "lines__job__customer",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["memo_type"] = "piecework"
        context["memo_title"] = "Piecework Memo"
        return context
    
class JobShippedReportView(
    LoginRequiredMixin,
    generic.ListView,
):
    model = JobShip
    template_name = "reports/job_shipped_report.html"
    context_object_name = "shipments"
    paginate_by = 100

    SORT_FIELDS = {
        "shipped_at": "shipped_at",
        "barcode": "job__barcode",
        "stock_num": "job__stock_num",
        "style": "job__style__name",
        "customer": "job__customer__name",
        "due": "job__due",
        "shipped_by": "shipped_by__user__last_name",
    }

    DEFAULT_SORT = "shipped_at"
    DEFAULT_DIRECTION = "desc"

    def get_filter_data(self):
        filter_data = self.request.GET.copy()

        # Apply the current-month date range only when the page
        # is opened without any query-string filters.
        if not filter_data:
            today = timezone.localdate()
            first_day = today.replace(day=1)

            filter_data["shipped_after"] = (
                first_day.isoformat()
            )

            filter_data["shipped_before"] = (
                today.isoformat()
            )

        return filter_data

    def get_base_queryset(self):
        return (
            JobShip.objects
            .select_related(
                "job",
                "job__style",
                "job__customer",
                "shipped_by",
                "shipped_by__user",
            )
        )

    def get_queryset(self):
        self.filter_data = self.get_filter_data()

        self.filter = JobShipFilter(
            self.filter_data,
            queryset=self.get_base_queryset(),
        )

        queryset = self.filter.qs

        sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )

        direction = self.request.GET.get(
            "direction",
            self.DEFAULT_DIRECTION,
        )

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in ("asc", "desc"):
            direction = self.DEFAULT_DIRECTION

        self.current_sort = sort
        self.current_direction = direction

        order_field = self.SORT_FIELDS[sort]

        if direction == "desc":
            order_field = f"-{order_field}"

        return queryset.order_by(
            order_field,
            "-pk",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Includes every filtered shipment, not only the current
        # pagination page.
        filtered_shipments = self.filter.qs

        total_shipped = filtered_shipments.count()

        # Calculate the average schedule difference and on-time rate
        # in one pass through the filtered shipments.
        schedule_values = []
        total_with_due_date = 0
        on_time_count = 0

        for shipment in filtered_shipments:
            days = shipment.schedule_difference_days

            if days is None:
                continue

            schedule_values.append(days)
            total_with_due_date += 1

            # Early and exactly on the due date both count as on time.
            if days <= 0:
                on_time_count += 1

        if schedule_values:
            average_schedule = (
                sum(schedule_values)
                / len(schedule_values)
            )
        else:
            average_schedule = None

        if average_schedule is None:
            average_schedule_display = "—"

        elif abs(average_schedule) < 0.05:
            average_schedule_display = "On schedule"

        elif average_schedule > 0:
            average_schedule_display = (
                f"{average_schedule:.1f} days late"
            )

        else:
            average_schedule_display = (
                f"{abs(average_schedule):.1f} days early"
            )

        if total_with_due_date:
            on_time_percentage = (
                on_time_count
                / total_with_due_date
            ) * 100

            on_time_display = (
                f"{on_time_percentage:.1f}% "
                f"({on_time_count}/{total_with_due_date})"
            )
        else:
            on_time_display = "—"

        filter_form_is_valid = (
            self.filter.form.is_valid()
        )

        shipped_after = (
            self.filter.form.cleaned_data.get(
                "shipped_after",
            )
            if filter_form_is_valid
            else None
        )

        shipped_before = (
            self.filter.form.cleaned_data.get(
                "shipped_before",
            )
            if filter_form_is_valid
            else None
        )

        if shipped_after and shipped_before:
            days_count = (
                shipped_before - shipped_after
            ).days + 1
        else:
            days_count = (
                filtered_shipments
                .annotate(
                    shipped_day=TruncDate(
                        "shipped_at",
                    )
                )
                .values("shipped_day")
                .distinct()
                .count()
            )

        average_per_day = (
            total_shipped / days_count
            if days_count
            else 0
        )

        daily_rows = (
            filtered_shipments
            .annotate(
                shipped_day=TruncDate(
                    "shipped_at",
                )
            )
            .values("shipped_day")
            .annotate(
                total=Count("id"),
            )
            .order_by("-shipped_day")
        )

        context["filter"] = self.filter
        context["total_shipped"] = total_shipped
        context["days_count"] = days_count
        context["average_per_day"] = average_per_day
        context["average_schedule_display"] = (
            average_schedule_display
        )
        context["on_time_display"] = (
            on_time_display
        )
        context["daily_rows"] = daily_rows

        context["current_sort"] = (
            self.current_sort
        )
        context["current_direction"] = (
            self.current_direction
        )

        query_params = self.request.GET.copy()
        query_params.pop("page", None)

        # The initial current-month dates are added internally rather
        # than appearing in request.GET, so add them to pagination links.
        if not self.request.GET:
            for key in (
                "shipped_after",
                "shipped_before",
            ):
                if key in self.filter_data:
                    query_params[key] = (
                        self.filter_data[key]
                    )

        context["query_params"] = (
            query_params.urlencode()
        )

        sort_links = {}

        for key in self.SORT_FIELDS:
            params = self.request.GET.copy()
            params.pop("page", None)

            if not self.request.GET:
                params["shipped_after"] = (
                    self.filter_data[
                        "shipped_after"
                    ]
                )
                params["shipped_before"] = (
                    self.filter_data[
                        "shipped_before"
                    ]
                )

            next_direction = "asc"

            if (
                self.current_sort == key
                and self.current_direction == "asc"
            ):
                next_direction = "desc"

            params["sort"] = key
            params["direction"] = next_direction

            sort_links[key] = params.urlencode()

        context["sort_links"] = sort_links

        return context

class StyleStepTimeReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/style_step_time_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = StyleStepTimeReportForm(self.request.GET or None)

        activities = (
            Activity.objects
            .filter(
                end__isnull=False,
                duration__isnull=False,
            )
            .select_related("job", "job__style", "step")
        )

        if form.is_valid():
            style = form.cleaned_data.get("style")

            if style:
                activities = activities.filter(job__style=style)

        rows = (
            activities
            .values("job__style__name", "step__name")
            .annotate(
                avg_duration=Avg("duration"),
                total_duration=Sum("duration"),
                activity_count=Count("id"),
            )
            .order_by("job__style__name", "step__name")
        )

        context["form"] = form
        context["rows"] = rows

        return context
    
class PieceworkCreateView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "piecework/create.html"

    def get_employees(self):
        return (
            Employee.objects
            .filter(
                active=True,
                user__is_active=True,
            )
            .select_related(
                "user",
                "department",
                "role",
            )
            .order_by(
                "user__last_name",
                "user__first_name",
            )
        )

    def get_departments(self):
        return (
            Department.objects
            .order_by("name")
        )

    def get_context(
        self,
        *,
        memo_form=None,
        scan_form=None,
    ):
        if memo_form is None:
            memo_form = PieceworkMemoCreateForm()

        if scan_form is None:
            scan_form = PieceworkScanForm()

        return {
            "memo_form": memo_form,
            "scan_form": scan_form,
            "employees": self.get_employees(),
            "departments": self.get_departments(),
        }

    def get(self, request, *args, **kwargs):
        return render(
            request,
            self.template_name,
            self.get_context(),
        )

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        memo_form = PieceworkMemoCreateForm(
            request.POST,
        )

        scan_form = PieceworkScanForm(
            request.POST,
        )

        if (
            not memo_form.is_valid()
            or not scan_form.is_valid()
        ):
            return render(
                request,
                self.template_name,
                self.get_context(
                    memo_form=memo_form,
                    scan_form=scan_form,
                ),
            )

        creator = get_employee(
            request.user,
        )

        scans = [
            line.strip()
            for line in (
                scan_form
                .cleaned_data["scans"]
                .splitlines()
            )
            if line.strip()
        ]

        if not scans:
            messages.error(
                request,
                "Please scan at least one job.",
            )

            return render(
                request,
                self.template_name,
                self.get_context(
                    memo_form=memo_form,
                    scan_form=scan_form,
                ),
            )

        validation_errors = []
        duplicate_scans = []
        seen_job_ids = set()

        # Store the scan used for each resolved job so
        # error messages can reference what was entered.
        resolved_jobs = []

        for scan in scans:
            job = find_job_by_scan(scan)

            if not job:
                validation_errors.append(
                    f"{scan} - job not found",
                )
                continue

            if job.pk in seen_job_ids:
                duplicate_scans.append(
                    f"{scan} - duplicate scan",
                )
                continue

            seen_job_ids.add(job.pk)

            resolved_jobs.append(
                {
                    "job_id": job.pk,
                    "scan": scan,
                }
            )

        if not resolved_jobs:
            messages.error(
                request,
                "No valid jobs were found.",
            )

            return redirect(
                "culet:piecework_create",
            )

        job_ids = [
            item["job_id"]
            for item in resolved_jobs
        ]

        # Lock the jobs before validating piecework status.
        # Every piecework creation path should lock Job rows
        # in the same way.
        locked_jobs = {
            job.pk: job
            for job in (
                Job.objects
                .select_for_update()
                .filter(pk__in=job_ids)
            )
        }

        # Fetch all open piecework records for the locked jobs.
        open_piecework_lines = {
            line.job_id: line
            for line in (
                PieceworkMemoLine.objects
                .filter(
                    job_id__in=job_ids,
                    memo__returned_at__isnull=True,
                )
                .select_related(
                    "memo",
                    "memo__assigned_to",
                    "memo__assigned_to__user",
                )
                .order_by(
                    "memo__created_at",
                )
            )
        }

        found_jobs = []

        for item in resolved_jobs:
            job_id = item["job_id"]
            scan = item["scan"]

            job = locked_jobs.get(job_id)

            if job is None:
                validation_errors.append(
                    f"{scan} - job no longer exists",
                )
                continue

            if not job.active:
                validation_errors.append(
                    f"{scan} - inactive",
                )
                continue

            if job.shipped:
                validation_errors.append(
                    f"{scan} - already shipped",
                )
                continue

            open_line = open_piecework_lines.get(
                job.pk,
            )

            if open_line:
                validation_errors.append(
                    (
                        f"{scan} - already on open "
                        f"piecework memo "
                        f"{open_line.memo.memo_num}"
                    ),
                )
                continue

            # No open memo exists, but the Boolean still says
            # piecework. Do not silently overwrite inconsistent
            # data.
            if job.is_piecework:
                validation_errors.append(
                    (
                        f"{scan} - marked as piecework, "
                        "but no open piecework memo was found; "
                        "status must be corrected"
                    ),
                )
                continue

            has_open_activity = (
                Activity.objects
                .filter(
                    job=job,
                    active=True,
                    end__isnull=True,
                )
                .exists()
            )

            if has_open_activity:
                validation_errors.append(
                    (
                        f"{scan} - currently being "
                        "worked on"
                    ),
                )
                continue

            found_jobs.append(job)

        if validation_errors or not found_jobs:
            error_message = "No piecework memo was created."

            if validation_errors:
                error_message += " " + ", ".join(validation_errors)

            messages.error(
                request,
                error_message,
            )

            return redirect(
                "culet:piecework_create",
            )

        # Reference rows are resolved only after every requested job has
        # passed validation. They remain part of this atomic transaction.
        piecework_location, _ = Location.objects.get_or_create(
            name="Piecework",
            defaults={"active": True},
        )
        from_location, _ = Location.objects.get_or_create(
            name="Office",
            defaults={"active": True},
        )

        memo = memo_form.save(
            commit=False,
        )

        memo.created_by = creator
        memo.from_location = from_location
        memo.to_location = piecework_location
        memo.save()

        assigned_at = timezone.now()

        for job in found_jobs:
            PieceworkMemoLine.objects.create(
                memo=memo,
                job=job,
            )

            # Piecework assignment changes the employee
            # responsible for the job.
            job, assignment_movement = move_job(
                job=job,
                movement_type="assigned",
                to_employee=memo.assigned_to,
                performed_by=creator,
            )

            # The pieceworker also physically receives
            # possession of the job.
            job, holder_movement = move_job(
                job=job,
                movement_type="received",
                to_employee=memo.assigned_to,
                performed_by=creator,
            )

            job.location = piecework_location
            job.in_work = False
            job.is_piecework = True
            job.piecework_assigned_at = assigned_at

            job.save(
                update_fields=[
                    "location",
                    "in_work",
                    "is_piecework",
                    "piecework_assigned_at",
                    "last_updated",
                ],
            )

        if duplicate_scans:
            messages.warning(
                request,
                (
                    "Some scans were skipped: "
                    + ", ".join(duplicate_scans)
                ),
            )

        job_count = len(found_jobs)

        job_word = (
            "job"
            if job_count == 1
            else "jobs"
        )

        messages.success(
            request,
            (
                "Piecework memo created with "
                f"{job_count} {job_word}."
            ),
        )

        return render(
            request,
            "memos/create_redirect.html",
            {
                "print_url": reverse(
                    "culet:piecework_print",
                    kwargs={
                        "pk": memo.pk,
                    },
                ),
                "home_url": reverse(
                    "culet:home",
                ),
            },
        )


class PieceworkOpenListView(
    LoginRequiredMixin,
    generic.ListView,
):
    model = PieceworkMemoLine
    template_name = "piecework/open.html"
    context_object_name = "piecework_lines"
    paginate_by = 50

    SORT_FIELDS = {
        "memo": "memo__memo_num",
        "assigned_to": "memo__assigned_to__user__last_name",
        "stock_num": "job__stock_num",
        "style": "job__style__name",
        "customer": "job__customer__name",
        "due_back": "memo__due_back",
    }

    DEFAULT_SORT = "due_back"

    def get_queryset(self):
        queryset = (
            PieceworkMemoLine.objects
            .filter(
                memo__returned_at__isnull=True,
            )
            .select_related(
                "memo",
                "memo__assigned_to",
                "memo__assigned_to__user",
                "memo__created_by",
                "memo__created_by__user",
                "job",
                "job__customer",
                "job__style",
                "job__location",
            )
        )

        self.filter = OpenPieceworkFilter(
            self.request.GET or None,
            queryset=queryset,
        )

        sort = self.request.GET.get(
            "sort",
            self.DEFAULT_SORT,
        )

        direction = self.request.GET.get(
            "direction",
            "asc",
        )

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in ("asc", "desc"):
            direction = "asc"

        self.current_sort = sort
        self.current_direction = direction

        order_field = self.SORT_FIELDS[sort]

        if direction == "desc":
            order_field = f"-{order_field}"

        return self.filter.qs.order_by(
            order_field,
            "memo__memo_num",
            "job__stock_num",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["filter"] = self.filter
        context["current_sort"] = self.current_sort
        context["current_direction"] = self.current_direction

        query_params = self.request.GET.copy()
        query_params.pop("page", None)

        context["query_params"] = query_params.urlencode()

        sort_links = {}

        for key in self.SORT_FIELDS:
            params = self.request.GET.copy()
            params.pop("page", None)

            next_direction = "asc"

            if (
                self.current_sort == key
                and self.current_direction == "asc"
            ):
                next_direction = "desc"

            params["sort"] = key
            params["direction"] = next_direction

            sort_links[key] = params.urlencode()

        context["sort_links"] = sort_links

        return context


class PieceworkReturnView(
    LoginRequiredMixin,
    generic.DetailView,
):
    model = PieceworkMemo
    template_name = "piecework/return.html"
    context_object_name = "memo"

    def get_queryset(self):
        return (
            PieceworkMemo.objects
            .select_related(
                "assigned_to",
                "created_by",
                "returned_by",
                "from_location",
                "to_location",
            )
            .prefetch_related(
                "lines__job",
            )
        )

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        # Lock only the memo row.
        # Do not use select_related() on this locked queryset.
        memo = (
            PieceworkMemo.objects
            .select_for_update()
            .get(pk=self.kwargs["pk"])
        )

        if memo.returned_at:
            messages.info(
                request,
                (
                    f"Piecework memo {memo.memo_num} "
                    "has already been returned."
                ),
            )

            return redirect(
                "culet:piecework_open",
            )

        returned_by = get_employee(
            request.user,
        )

        piecework_step = get_object_or_404(
            ActivityStep,
            code="piecework",
        )

        lines = list(
            memo.lines
            .select_related(
                "job",
            )
            .all()
        )

        if not lines:
            messages.error(
                request,
                (
                    f"Piecework memo {memo.memo_num} "
                    "does not contain any jobs."
                ),
            )

            return redirect(
                "culet:piecework_open",
            )

        job_ids = [
            line.job_id
            for line in lines
        ]

        # Lock only the Job rows.
        # No select_related() here because assigned_to
        # and holder are nullable foreign keys.
        locked_jobs = {
            job.pk: job
            for job in (
                Job.objects
                .select_for_update()
                .filter(pk__in=job_ids)
            )
        }

        # A job should never be on another open memo.
        # If old inconsistent data exists, stop before
        # changing anything.
        duplicate_open_lines = list(
            PieceworkMemoLine.objects
            .filter(
                job_id__in=job_ids,
                memo__returned_at__isnull=True,
            )
            .exclude(
                memo_id=memo.pk,
            )
            .select_related(
                "job",
                "memo",
            )
        )

        if duplicate_open_lines:
            details = ", ".join(
                (
                    f"{line.job.stock_num} "
                    f"on {line.memo.memo_num}"
                )
                for line in duplicate_open_lines
            )

            messages.error(
                request,
                (
                    f"Memo {memo.memo_num} cannot be returned "
                    "because these jobs are also on another "
                    f"open piecework memo: {details}."
                ),
            )

            return redirect(
                "culet:piecework_open",
            )

        returned_at = timezone.now()
        returned_count = 0

        for line in lines:
            job = locked_jobs[line.job_id]

            # This activity belongs to this memo, so use
            # this memo's creation time.
            piecework_start = memo.created_at

            Activity.objects.create(
                job=job,
                employee=memo.assigned_to,
                step=piecework_step,
                start=piecework_start,
                end=returned_at,
                duration=(
                    returned_at
                    - piecework_start
                ),
                is_piecework=True,
                active=False,
            )

            # Responsibility returns to the employee
            # processing the piecework return.
            job, assignment_movement = move_job(
                job=job,
                movement_type="returned-to-manager",
                to_employee=returned_by,
                performed_by=returned_by,
            )

            # Physical possession also returns to that
            # employee.
            job, holder_movement = move_job(
                job=job,
                movement_type="returned",
                to_employee=returned_by,
                performed_by=returned_by,
            )

            job.is_piecework = False
            job.in_work = False
            job.piecework_assigned_at = None

            job.save(
                update_fields=[
                    "is_piecework",
                    "in_work",
                    "piecework_assigned_at",
                    "last_updated",
                ],
            )

            returned_count += 1

        memo.returned_at = returned_at
        memo.returned_by = returned_by

        memo.save(
            update_fields=[
                "returned_at",
                "returned_by",
            ],
        )

        job_word = (
            "job"
            if returned_count == 1
            else "jobs"
        )

        messages.success(
            request,
            (
                f"Piecework memo {memo.memo_num} "
                f"was returned with "
                f"{returned_count} {job_word}."
            ),
        )

        return redirect(
            "culet:piecework_open",
        )
    
class MemoListView(LoginRequiredMixin, generic.TemplateView):
    template_name = "memos/memo_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = MemoFilterForm(self.request.GET or None)

        transfer_memos = JobTransferMemo.objects.select_related(
            "created_by",
            "created_by__user",
            "assigned_to",
            "assigned_to__user",
        )

        piecework_memos = PieceworkMemo.objects.select_related(
            "created_by",
            "assigned_to",
        )

        if form.is_valid():
            memo_type = form.cleaned_data.get("memo_type")
            created_start = form.cleaned_data.get("created_start")
            created_end = form.cleaned_data.get("created_end")

            if created_start:
                transfer_memos = transfer_memos.filter(created_at__date__gte=created_start)
                piecework_memos = piecework_memos.filter(created_at__date__gte=created_start)

            if created_end:
                transfer_memos = transfer_memos.filter(created_at__date__lte=created_end)
                piecework_memos = piecework_memos.filter(created_at__date__lte=created_end)

            if memo_type == "transfer":
                piecework_memos = PieceworkMemo.objects.none()

            if memo_type == "piecework":
                transfer_memos = JobTransferMemo.objects.none()

        memo_rows = []

        for memo in transfer_memos:
            memo_rows.append({
                "type": "Transfer",
                "memo_num": memo.memo_num,
                "created_at": memo.created_at,
                "created_by": memo.created_by,
                "detail_url": reverse("culet:job_transfer_memo_print", kwargs={"pk": memo.pk}),
            })

        for memo in piecework_memos:
            memo_rows.append({
                "type": "Piecework",
                "memo_num": memo.memo_num,
                "created_at": memo.created_at,
                "created_by": memo.created_by,
                "assigned_to": memo.assigned_to,
                "detail_url": reverse("culet:piecework_print", kwargs={"pk": memo.pk}),
            })

        memo_rows = sorted(
            memo_rows,
            key=itemgetter("created_at"),
            reverse=True,
        )

        context["form"] = form
        context["memo_rows"] = memo_rows
        return context
    
class QualityInspectionCreateView(
    CuletPermissionRequiredMixin,
    generic.TemplateView,
):
    permission_function = can_perform_quality_inspection
    permission_denied_message = (
        "You do not have permission to perform quality inspections."
    )
    template_name = "jobs/quality_inspection.html"
    success_url = reverse_lazy("culet:quality_inspection")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        initial = {}

        barcode = self.request.GET.get("barcode", "").strip()

        if barcode:
            initial["barcode"] = barcode

        context["form"] = kwargs.get("form") or QualityInspectionForm(
            initial=initial
        )

        return context

    def post(self, request, *args, **kwargs):
        form = QualityInspectionForm(request.POST)

        if not form.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form)
            )

        employee = get_object_or_404(
            Employee,
            user=request.user,
        )

        barcode = form.cleaned_data["barcode"]
        inspection_duration_minutes = form.cleaned_data[
            "inspection_duration_minutes"
        ]

        job = (
            Job.objects
            .filter(barcode=barcode)
            .first()
        )

        if not job:
            messages.error(
                request,
                f"No job found with barcode {barcode}.",
            )
            return self.render_to_response(
                self.get_context_data(form=form)
            )

        qc_step = ActivityStep.objects.filter(
            code="qc"
        ).first()

        if not qc_step:
            messages.error(
                request,
                'The QC activity step with code "qc" was not found.',
            )
            return self.render_to_response(
                self.get_context_data(form=form)
            )

        inspection_time = timezone.now()

        activity_start = inspection_time - timedelta(
            minutes=inspection_duration_minutes
        )

        with transaction.atomic():
            inspection = QualityInspection.objects.create(
                job=job,
                inspected_by=employee,
                result=form.cleaned_data["result"],
                notes=form.cleaned_data.get("notes", ""),
                inspection_duration_minutes=inspection_duration_minutes,
            )

            if inspection.result == QualityInspection.RESULT_FAIL:
                QualityInspectionFailure.objects.bulk_create(
                    [
                        QualityInspectionFailure(
                            inspection=inspection,
                            failure_type=failure_type,
                        )
                        for failure_type in form.cleaned_data[
                            "failure_types"
                        ]
                    ]
                )

            Activity.objects.create(
                job=job,
                employee=employee,
                step=qc_step,
                start=activity_start,
                end=inspection_time,
                active=False,
                is_piecework=False,
            )

        if inspection.result == QualityInspection.RESULT_PASS:
            messages.success(
                request,
                (
                    f"Job {job.barcode} passed QC. "
                    f"{inspection_duration_minutes} minutes recorded."
                ),
            )
        else:
            messages.error(
                request,
                (
                    f"Job {job.barcode} failed QC. "
                    f"{inspection_duration_minutes} minutes recorded."
                ),
            )

        return redirect(self.success_url)


class QualityFailureReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/quality_failures.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = QualityFailureReportForm(self.request.GET or None)

        failures = (
            QualityInspectionFailure.objects
            .select_related(
                "inspection",
                "inspection__job",
                "inspection__job__style",
                "inspection__job__customer",
                "inspection__inspected_by",
                "inspection__inspected_by__user",
                "failure_type",
            )
            .order_by("-inspection__inspected_at")
        )

        if form.is_valid():
            start_date = form.cleaned_data.get("start_date")
            end_date = form.cleaned_data.get("end_date")
            failure_type = form.cleaned_data.get("failure_type")
            style = form.cleaned_data.get("style")
            customer = form.cleaned_data.get("customer")

            if start_date:
                failures = failures.filter(inspection__inspected_at__date__gte=start_date)

            if end_date:
                failures = failures.filter(inspection__inspected_at__date__lte=end_date)

            if failure_type:
                failures = failures.filter(failure_type=failure_type)

            if style:
                failures = failures.filter(inspection__job__style=style)

            if customer:
                failures = failures.filter(inspection__job__customer=customer)

        reason_rows = (
            failures
            .values("failure_type__name")
            .annotate(total=Count("id"))
            .order_by("-total", "failure_type__name")
        )

        context["form"] = form
        context["failures"] = failures
        context["reason_rows"] = reason_rows
        context["total_failures"] = failures.count()

        return context
    
class RepairCreateView(LoginRequiredMixin, generic.TemplateView):
    template_name = "jobs/create_repair.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = RepairCreateForm()
        return context

    def get_next_barcode(self):
        max_barcode = Job.objects.aggregate(Max("barcode"))["barcode__max"] or 0
        return max_barcode + 1

    def get_base_stock_num(self, stock_num):
        return re.sub(r"-R\d+$", "", stock_num)

    def get_next_repair_stock_num(self, original_job):
        base_stock_num = self.get_base_stock_num(original_job.stock_num)

        repair_number = 1

        while Job.objects.filter(stock_num=f"{base_stock_num}-R{repair_number}").exists():
            repair_number += 1

        return f"{base_stock_num}-R{repair_number}"

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = RepairCreateForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        scanned_stock_num = form.cleaned_data["stock_num"].strip()

        original_job = Job.objects.filter(stock_num=scanned_stock_num).first()

        if not original_job:
            messages.error(request, f"No job found with stock number {scanned_stock_num}.")
            return redirect("culet:create_repair")

        repair_stock_num = self.get_next_repair_stock_num(original_job)

        repair_job = Job.objects.create(
            customer=original_job.customer,
            barcode=self.get_next_barcode(),
            customer_ref_num=None,
            active=True,
            shipped=False,
            in_work=False,
            style=original_job.style,
            due=original_job.due,
            assigned_to=original_job.assigned_to,
            location=original_job.location,
            notes=original_job.notes,
            stock_num=repair_stock_num,
            is_repair=True,
        )

        for metal in original_job.job_metals.all():
            new_metal = JobMetal.objects.create(
                job=repair_job,
                part=metal.part,
                qty_req=metal.qty_req,
                weight_req=metal.weight_req,
                metal_type=metal.metal_type,
            )

            for lot_assignment in metal.lot_assignments.all():
                JobMetalLot.objects.create(
                    job_metal=new_metal,
                    metal_lot=lot_assignment.metal_lot,
                    qty_used=lot_assignment.qty_used,
                    weight_used=lot_assignment.weight_used,
                )

        for stone in original_job.job_stones.all():
            JobStone.objects.create(
                job=repair_job,
                stone_type=stone.stone_type,
                stone_shape=stone.stone_shape,
                stone_size=stone.stone_size,
                qty_req=stone.qty_req,
            )

        messages.success(
            request,
            f"Repair {repair_job.stock_num} was created from {original_job.stock_num}."
        )

        return redirect("culet:job_detail", pk=repair_job.pk)
    
class RepairLookupView(LoginRequiredMixin, generic.FormView):
    template_name = "jobs/create_repair_lookup.html"
    form_class = RepairLookupForm

    def form_valid(self, form):
        scanned_value = form.cleaned_data["stock_num"].strip()

        original_job = (
            Job.objects.filter(stock_num=scanned_value).first()
            or Job.objects.filter(barcode=scanned_value).first()
        )

        if not original_job:
            messages.error(self.request, f"No job found for {scanned_value}.")
            return redirect("culet:create_repair")

        return redirect(
            f"{reverse('culet:job_create')}?repair_from={original_job.pk}"
        )
    
class RequiredPasswordChangeView(LoginRequiredMixin, generic.FormView):
    template_name = "registration/required_password_change.html"
    form_class = SetPasswordForm
    success_url = reverse_lazy("culet:home")

    def dispatch(self, request, *args, **kwargs):
        employee = getattr(request.user, "employee", None)

        if employee is None:
            return redirect("culet:home")

        if not employee.must_change_password:
            return redirect("culet:home")

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        user = form.save()

        update_session_auth_hash(
            self.request,
            user,
        )

        employee = self.request.user.employee
        employee.must_change_password = False
        employee.save(update_fields=["must_change_password"])

        messages.success(
            self.request,
            "Your password has been changed successfully.",
        )

        return super().form_valid(form)

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import redirect
from django.views import generic

from .models import Job, JobStatus


class ChangeJobStatusView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "jobs/change_status.html"

    def get_statuses(self):
        return (
            JobStatus.objects
            .filter(active=True)
            .order_by(
                "sort_order",
                "name",
            )
        )

    def get_selected_job(self):
        job_id = self.request.GET.get("job_id", "").strip()

        if not job_id:
            return None

        return (
            Job.objects
            .select_related(
                "style",
                "customer",
                "status",
            )
            .filter(pk=job_id)
            .first()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["statuses"] = self.get_statuses()
        context["selected_job"] = self.get_selected_job()

        return context

    def post(self, request, *args, **kwargs):
        status_id = request.POST.get(
            "status",
            "",
        ).strip()

        barcodes = [
            barcode.strip()
            for barcode in request.POST.getlist("barcodes")
            if barcode.strip()
        ]

        context = self.get_context_data()

        # Preserve submitted data when displaying errors.
        context["submitted_barcodes"] = barcodes
        context["selected_status_id"] = status_id

        if not status_id:
            messages.error(
                request,
                "Please select a new job status.",
            )
            return self.render_to_response(context)

        try:
            new_status = JobStatus.objects.get(
                pk=status_id,
                active=True,
            )
        except JobStatus.DoesNotExist:
            messages.error(
                request,
                "The selected job status is not available.",
            )
            return self.render_to_response(context)

        if not barcodes:
            messages.error(
                request,
                "Please enter at least one job barcode.",
            )
            return self.render_to_response(context)

        # Do not process the same scanned barcode twice.
        unique_barcodes = list(dict.fromkeys(barcodes))

        matching_jobs = list(
            Job.objects
            .filter(
                barcode__in=unique_barcodes,
            )
            .select_related(
                "style",
                "customer",
                "status",
            )
        )

        jobs_by_barcode = {
            str(job.barcode): job
            for job in matching_jobs
        }

        missing_barcodes = [
            barcode
            for barcode in unique_barcodes
            if barcode not in jobs_by_barcode
        ]

        if missing_barcodes:
            messages.error(
                request,
                "No job was found for the following barcode(s): "
                + ", ".join(missing_barcodes),
            )
            return self.render_to_response(context)

        changed_jobs = []
        unchanged_jobs = []

        with transaction.atomic():
            for barcode in unique_barcodes:
                job = jobs_by_barcode[barcode]

                if job.status_id == new_status.pk:
                    unchanged_jobs.append(job)
                    continue

                job.status = new_status
                changed_jobs.append(job)

            if changed_jobs:
                Job.objects.bulk_update(
                    changed_jobs,
                    ["status"],
                )

        if changed_jobs:
            messages.success(
                request,
                (
                    f"Updated {len(changed_jobs)} "
                    f"{'job' if len(changed_jobs) == 1 else 'jobs'} "
                    f"to “{new_status.name}”."
                ),
            )

        if unchanged_jobs:
            stock_numbers = ", ".join(
                job.stock_num
                for job in unchanged_jobs
            )

            messages.info(
                request,
                (
                    "Already set to this status: "
                    f"{stock_numbers}."
                ),
            )

        return redirect("culet:change_status")

class PayrollReportView(
    LoginRequiredMixin,
    generic.TemplateView,
):
    template_name = "reports/payroll_report.html"




class PayrollReportView(LoginRequiredMixin, generic.TemplateView):
    """
    Payroll-oriented time clock report.

    Unlike the downtime report, this view intentionally ignores Activity
    records and reports strictly from TimeClock data.

    The report hierarchy is:

        Employee
            Week
                Day
                    Clock Entries
                Week Total
            Employee Total

    The report uses the @property helpers on TimeClock:

        rounded_clock_in
        rounded_clock_out
        rounded_hours
    """

    template_name = "reports/payroll_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = TimeClockReportForm(self.request.GET or None)

        employee_rows = []

        report_totals = {
            "raw_hours": 0,
            "rounded_hours": 0,
        }

        if form.is_valid():
            selected_employee = form.cleaned_data.get("employee")
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]

            start_dt = timezone.make_aware(
                datetime.combine(start_date, time.min)
            )

            end_dt = timezone.make_aware(
                datetime.combine(end_date, time.max)
            )

            employees = (
                Employee.objects
                .select_related(
                    "user",
                    "department",
                    "role",
                )
                .filter(
                    role__requires_clock_in=True,
                )
                .order_by(
                    "user__last_name",
                    "user__first_name",
                )
            )

            if selected_employee:
                employees = employees.filter(
                    pk=selected_employee.pk,
                )

            for employee in employees:

                entries = (
                    TimeClock.objects
                    .filter(
                        employee=employee,
                        clock_in__lte=end_dt,
                    )
                    .filter(
                        Q(clock_out__gte=start_dt)
                        | Q(clock_out__isnull=True)
                    )
                    .order_by(
                        "clock_in",
                    )
                )

                weeks = OrderedDict()

                employee_raw_hours = 0
                employee_rounded_hours = 0

                for entry in entries:

                    work_date = timezone.localtime(
                        entry.clock_in
                    ).date()

                    if work_date < start_date or work_date > end_date:
                        continue

                    week_start = (
                        work_date
                        - timedelta(days=work_date.weekday())
                    )

                    week = weeks.setdefault(
                        week_start,
                        {
                            "week_start": week_start,
                            "week_end": week_start + timedelta(days=6),
                            "days": OrderedDict(),
                            "raw_hours": 0,
                            "rounded_hours": 0,
                        },
                    )

                    day = week["days"].setdefault(
                        work_date,
                        {
                            "date": work_date,
                            "entries": [],
                            "raw_hours": 0,
                            "rounded_hours": 0,
                        },
                    )

                    raw_hours = entry.raw_hours
                    rounded_hours = entry.rounded_hours

                    day["entries"].append(
                        {
                            "timeclock": entry,
                            "raw_clock_in": entry.clock_in,
                            "rounded_clock_in": entry.rounded_clock_in,
                            "raw_clock_out": entry.clock_out,
                            "rounded_clock_out": entry.rounded_clock_out,
                            "raw_hours": raw_hours,
                            "rounded_hours": rounded_hours,
                        }
                    )

                    day["raw_hours"] += raw_hours
                    day["rounded_hours"] += rounded_hours

                    week["raw_hours"] += raw_hours
                    week["rounded_hours"] += rounded_hours

                    employee_raw_hours += raw_hours
                    employee_rounded_hours += rounded_hours

                if weeks:
                    employee_rows.append(
                        {
                            "employee": employee,
                            "weeks": list(weeks.values()),
                            "raw_hours": employee_raw_hours,
                            "rounded_hours": employee_rounded_hours,
                        }
                    )

                    report_totals["raw_hours"] += employee_raw_hours
                    report_totals["rounded_hours"] += employee_rounded_hours

        context["form"] = form
        context["employee_rows"] = employee_rows
        context["report_totals"] = report_totals

        return context
