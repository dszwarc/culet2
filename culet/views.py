from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from django.template.loader import render_to_string
import copy
from django.db import transaction
from django.db.models import F, Q, Max, OuterRef, Subquery
from django.views import generic
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from .filters import JobFilter, ActivityFilter
from datetime import timedelta, datetime, time, date
from collections import defaultdict
from decimal import Decimal

from .models import (
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
)

from .forms import (
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

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


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

        role = employee.role_fk
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

class HomeView(LoginRequiredMixin, generic.TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        employee = self.request.user.employee
        role = employee.role_fk

        context["employee_level"] = role.level if role else 0
        context["is_manager_level"] = context["employee_level"] >= 30

        return context


def index(request):
    return render(request, 'authentication/login.html')

class JobListView(LoginRequiredMixin, generic.ListView):
    model = Job
    template_name = "jobs/index.html"
    context_object_name = "latest_job_list"
    paginate_by = 25

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
        jobs = Job.objects.select_related(
            "style",
            "assigned_to__user",
            "holder__user",
            "location",
            "customer",
        )

        self.filter = JobFilter(self.request.GET, queryset=jobs)

        sort = self.request.GET.get("sort", self.DEFAULT_SORT)
        direction = self.request.GET.get("direction", "asc")

        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT

        if direction not in ["asc", "desc"]:
            direction = "asc"

        self.current_sort = sort
        self.current_direction = direction

        order_field = self.SORT_FIELDS[sort]

        if direction == "desc":
            order_field = f"-{order_field}"

        return self.filter.qs.order_by(order_field, "barcode")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter"] = self.filter
        context["current_sort"] = self.current_sort
        context["current_direction"] = self.current_direction

        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["query_params"] = query_params.urlencode()

        sort_links = {}
        for key in self.SORT_FIELDS.keys():
            params = self.request.GET.copy()
            params.pop("page", None)

            next_direction = "asc"
            if self.current_sort == key and self.current_direction == "asc":
                next_direction = "desc"

            params["sort"] = key
            params["direction"] = next_direction

            sort_links[key] = params.urlencode()

        context["sort_links"] = sort_links

        return context

    
class MyJobListView(LoginRequiredMixin, generic.ListView):
    model = Job
    template_name = "jobs/my_jobs.html"
    context_object_name = "latest_job_list"

    def get_queryset(self):
        employee = Employee.objects.get(user=self.request.user)
        return Job.objects.filter(
            assigned_to=employee,
            holder=employee
        ).prefetch_related("activity_set")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = Employee.objects.get(user=self.request.user)
        context["activity_start_form"] = ActivityStartForm(employee=employee)
        return context

def get_receivable_jobs_for_employee(employee):
    return (
        Job.objects
        .filter(assigned_to=employee)
        .exclude(holder=employee)
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
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        employee = request.user.employee

        job = get_object_or_404(
            get_receivable_jobs_for_employee(employee),
            pk=request.POST.get("job_id"),
        )

        job.holder = employee
        job.save()

        messages.success(request, f"Job {job.barcode} received.")
        return redirect("culet:my_jobs")


class ReceiveAllJobsView(LoginRequiredMixin, generic.View):
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        employee = request.user.employee

        jobs = get_receivable_jobs_for_employee(employee)
        count = jobs.update(holder=employee)

        messages.success(request, f"{count} job(s) received.")
        return redirect("culet:my_jobs")

class ReceiveAndAssignJobsView(LoginRequiredMixin, generic.TemplateView):
    template_name = "jobs/receive_and_assign.html"

    def get_assignable_employees(self):
        current_employee = self.request.user.employee

        employees = (
            Employee.objects
            .select_related("user", "department_fk", "role_fk")
            .order_by(
                "role_fk__name",
                "user__last_name",
                "user__first_name",
            )
            .exclude(id=current_employee.id)
        )

        role_name = current_employee.role_fk.name if current_employee.role_fk else ""

        if role_name == "Super":
            return employees

        if role_name == "Manager":
            return employees.filter(
                department_fk=current_employee.department_fk
            )

        return Employee.objects.none()

    def get(self, request, *args, **kwargs):
        employees = self.get_assignable_employees()

        return render(request, self.template_name, {
            "managers": employees.filter(role_fk__name="Manager") | employees.filter(role_fk__name="Super"),
            "employees": employees.exclude(role_fk__name="Manager").exclude(role_fk__name="Super"),
        })

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        current_employee = request.user.employee
        employees = self.get_assignable_employees()

        receiving_employee = get_object_or_404(
            employees,
            id=request.POST.get("employee")
        )

        scanned_jobs = [
            barcode.strip()
            for barcode in request.POST.getlist("job")
            if barcode.strip()
        ]

        if not scanned_jobs:
            messages.error(request, "Please scan at least one job.")
            return redirect("culet:receive_and_assign_jobs")

        assigned_count = 0
        missing_jobs = []

        for barcode in scanned_jobs:
            try:
                job = Job.objects.get(barcode=barcode)

                job.holder = receiving_employee
                job.assigned_to = receiving_employee
                job.save()

                assigned_count += 1

            except Job.DoesNotExist:
                missing_jobs.append(barcode)

        if assigned_count:
            messages.success(
                request,
                f"{assigned_count} job(s) received and assigned to {receiving_employee}."
            )

        if missing_jobs:
            messages.error(
                request,
                f"These jobs were not found: {', '.join(missing_jobs)}"
            )

        return redirect("culet:receive_and_assign_jobs")

class ReportingListView(LoginRequiredMixin, generic.ListView):
    model = Activity
    template_name = "reporting/index.html"
    context_object_name = "activities"
    # def get_queryset(self):
    #     return Activity.objects.order_by("-start")
    def get_context_data(self, **kwargs):
        activities = Activity.objects.all()
        myFilter = ActivityFilter(self.request.GET,queryset=activities)
        filt_activities = myFilter.qs
        total_time=0
        for act in filt_activities:
            if act.duration:
                total_time += act.duration
        if total_time > 0:
            avg_time = total_time/len(filt_activities)
        else:
            avg_time = 0
        context = {'activities':filt_activities, 'filter':myFilter, 'total_time':total_time, 'avg_time':avg_time}
        return context

class ActivityListView(LoginRequiredMixin,generic.ListView):
    model = Activity
    template_name = "activities/index.html"
    context_object_name = "activities"
    def get_queryset(self):
        return Activity.objects.order_by("-start")
    
class JobDetailView(LoginRequiredMixin, generic.DetailView):
    model = Job
    template_name = "jobs/detail.html"
    context_object_name = "job"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job_metals"] = JobMetal.objects.filter(job=context['job'])
        context["job_stones"] = JobStone.objects.filter(job=context['job'])
        context["job_findings"] = JobFinding.objects.filter(job=context["job"])
        context["activity"] = Activity.objects.filter(job=context['job'])
        context["job_weights"] = context["job"].weights.order_by("-created_at","-id")
        return context

class JobCreateView(LoginRequiredMixin, generic.CreateView):
    model = Job
    form_class = JobForm
    template_name = "jobs/create.html"
    success_url = reverse_lazy("culet:index_job")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.POST:
            context["metal_formset"] = JobMetalFormSet(self.request.POST, prefix="metals")
            context["stone_formset"] = JobStoneFormSet(self.request.POST, prefix="stones")
            context["finding_formset"] = JobFindingFormSet(self.request.POST, prefix="findings")
        else:
            context["metal_formset"] = JobMetalFormSet(prefix="metals")
            context["stone_formset"] = JobStoneFormSet(prefix="stones")
            context["finding_formset"] = JobFindingFormSet(prefix="findings")

        return context

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]
        finding_formset = context["finding_formset"]

        if not metal_formset.is_valid():
            messages.error(self.request, f"Metal form errors: {metal_formset.errors}")
            messages.error(self.request, f"Metal non-form errors: {metal_formset.non_form_errors()}")

        if not stone_formset.is_valid():
            messages.error(self.request, f"Stone form errors: {stone_formset.errors}")
            messages.error(self.request, f"Stone non-form errors: {stone_formset.non_form_errors()}")

        if not finding_formset.is_valid():
            messages.error(self.request, f"Finding form errors: {finding_formset.errors}")
            messages.error(self.request, f"Finding non-form errors: {finding_formset.non_form_errors()}")

        if not (
            metal_formset.is_valid()
            and stone_formset.is_valid()
            and finding_formset.is_valid()
        ):
            return self.render_to_response(self.get_context_data(form=form))

        self.object = form.save(commit=False)

        self.object.assigned_to = None
        self.object.holder = None
        self.object.location = Location.objects.get(name="Office")
        self.object.status = JobStatus.objects.get(name="Waiting on Metal")

        if self.object.style:
            if not self.object.stamp:
                self.object.stamp = self.object.style.stamp or ""

            if not self.object.notes:
                self.object.notes = self.object.style.description or ""

        self.object.save()
        form.save_m2m()

        metal_formset.instance = self.object
        metal_formset.save()

        stone_formset.instance = self.object
        stone_formset.save()

        finding_formset.instance = self.object
        finding_formset.save()

        return redirect(self.object.get_absolute_url())

    def form_invalid(self, form):
        return self.render_to_response(
            self.get_context_data(form=form)
        )

class JobStyleDefaultsHTMXView(LoginRequiredMixin, generic.View):
    template_name = "jobs/partials/job_style_defaults.html"

    def get(self, request, *args, **kwargs):
        style_id = request.GET.get("style_id")

        if not style_id:
            return HttpResponseBadRequest("Missing style_id")

        style = get_object_or_404(Style, pk=style_id)

        job_initial = {
            "name": style.name,
            "customer": style.customer_id,
            "style": style.pk,
            "stamp": style.stamp or "",
            "notes": style.description or "",
        }

        job_form = JobForm(initial=job_initial)

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
                "weight_req": getattr(ss, "weight_req", None),
            }
            for ss in style.stylestone_set.all()
        ]

        finding_initial = [
            {
                "finding": sf.finding_id,
                "qty_req": sf.qty_req,
                "qty_used": 0,
            }
            for sf in style.style_findings.all()
        ]

        JobMetalCreateFormSet = get_job_metal_formset(
            extra=len(metal_initial)
        )
        JobStoneCreateFormSet = get_job_stone_formset(
            extra=len(stone_initial)
        )

        JobFindingCreateFormSet = get_job_finding_formset(
            extra=len(finding_initial)
        )

        metal_formset = JobMetalCreateFormSet(
            prefix="metals",
            initial=metal_initial,
        )
        stone_formset = JobStoneCreateFormSet(
            prefix="stones",
            initial=stone_initial,
        )


        finding_formset = JobFindingCreateFormSet(
            prefix="findings",
            initial=finding_initial,
        )

        return render(
            request,
            self.template_name,
            {
                "job_form": job_form,
                "metal_formset": metal_formset,
                "stone_formset": stone_formset,
                "finding_formset": finding_formset,
                "style": style,
            },
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

        # restore previous allocations before recalculating
        existing_allocations = list(job_metal.lot_assignments.select_related("metal_lot"))
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

        # Restore existing assignments before checking availability
        existing_assignments = list(
            job_metal.lot_assignments.select_related("metal_lot")
        )

        for existing in existing_assignments:
            MetalLot.objects.filter(pk=existing.metal_lot_id).update(
                qty_on_hand=F("qty_on_hand") + existing.qty_used,
                weight_on_hand=F("weight_on_hand") + existing.weight_used,
            )

        # Delete old assignments so the submitted formset becomes the source of truth
        job_metal.lot_assignments.all().delete()

        assignments = formset.save(commit=False)

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

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]
        finding_formset = context["finding_formset"]

        if not (
            metal_formset.is_valid()
            and stone_formset.is_valid()
            and finding_formset.is_valid()
        ):
            return self.render_to_response(self.get_context_data(form=form))

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

class StyleListView(LoginRequiredMixin,generic.ListView):
    model = Style
    template_name = "styles/index.html"
    context_object_name = "style_list"
    def get_queryset(self):
        return Style.objects.all()#.prefetch_related(
            #"style_components__component_type",
            #"style_components__attribute_contraints__attribute",
        #)

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

class AssignJobView(LoginRequiredMixin, ClockedInRequiredMixin, generic.TemplateView):
    template_name = "jobs/assign.html"

    def get_assignable_employees(self):
        current_employee = self.request.user.employee

        employees = Employee.objects.select_related(
            "user",
            "department_fk",
            "role_fk",
        ).order_by(
            "role_fk__name",
            "user__last_name",
            "user__first_name",
        ).exclude(
            id=current_employee.id
        )

        role_name = current_employee.role_fk.name if current_employee.role_fk else ""
        dept_name = current_employee.department_fk.name if current_employee.department_fk else ""

        if role_name == "Super":
            return employees

        if role_name == "Manager":
            return employees.filter(
                department_fk=current_employee.department_fk
            )

        return Employee.objects.none()

    def get(self, request, *args, **kwargs):
        employees = self.get_assignable_employees()

        context = {
            "managers": employees.filter(role_fk__name="Manager") | employees.filter(role_fk__name="Super"),
            "employees": employees.exclude(role_fk__name="Manager").exclude(role_fk__name="Super"),
        }

        return render(request, self.template_name, context)

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        employees = self.get_assignable_employees()

        employee = get_object_or_404(
            employees,
            id=request.POST.get("employee")
        )

        scanned_jobs = [
            barcode.strip()
            for barcode in request.POST.getlist("job")
            if barcode.strip()
        ]

        if not scanned_jobs:
            messages.error(request, "Please scan at least one job.")
            return redirect("culet:assign_job")

        assigned_count = 0
        missing_jobs = []

        for barcode in scanned_jobs:
            try:
                job = Job.objects.get(barcode=barcode)
                job.assigned_to = employee
                job.save()
                assigned_count += 1
            except Job.DoesNotExist:
                missing_jobs.append(barcode)

        if assigned_count:
            messages.success(
                request,
                f"{assigned_count} job(s) assigned to {employee}."
            )

        if missing_jobs:
            messages.error(
                request,
                f"These jobs were not found: {', '.join(missing_jobs)}"
            )

        return redirect("culet:assign_job")

class ReturnJobView(LoginRequiredMixin, generic.TemplateView):
    template_name = "jobs/return.html"

    def get(self, request, *args, **kwargs):
        employees = Employee.objects.exclude(role_fk__name="Hourly")

        context = {
            "managers": employees.filter(role_fk__name="Manager") | employees.filter(role_fk__name="Super"),
        }

        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        job = get_object_or_404(Job, barcode=request.POST.get("job"))

        employee = get_object_or_404(
            Employee,
            id=request.POST.get("employee")
        )

        job.assigned_to = employee
        job.save()

        messages.success(
            request,
            f"Job {job.stock_num} (Barcode {job.barcode}) has been returned to {employee}."
        )

        return redirect("culet:return_job")


@login_required
def startWork(request):
    if request.method != "POST":
        return redirect("culet:my_jobs")

    employee = request.user.employee

    if employee.clocked_in == False:
        messages.error(request, f"Activity could not be started, please clock in first")
        return redirect("culet:my_jobs")

    job = get_object_or_404(
        Job,
        id=request.POST.get("job_id"),
        assigned_to=employee,
        holder=employee,
    )

    if job.in_work:
        messages.error(request, f"Job {job.barcode} could not be started. Activity already started.")
        return redirect("culet:my_jobs")

    form = ActivityStartForm(request.POST, employee=employee)

    if not form.is_valid():
        messages.error(request, "Please choose a valid activity step.")
        return redirect("culet:my_jobs")

    activity = form.save(commit=False)
    activity.employee = employee
    activity.job = job
    activity.start = timezone.now()
    activity.name = activity.step.name
    activity.save()

    job.in_work = True
    job.assigned_to = employee
    job.holder = employee
    job.save()

    messages.success(request, f"Job {job.barcode} has been started. ({activity.step.name})")
    return redirect("culet:my_jobs")

# def startWork(request):
#     #NEED LOGIC TO PREVENT EMP FROM STARTING WORK THAT IS NOT ASSIGNED TO THEM OR IF THEY ARE NOT LOGGED IN
#     if request.user:
#         job_query = Job.objects.get(barcode=request.POST["job"])

#         #check to see if job that is being queried is assigned to the user before allowing user to start work.
#         if job_query.assigned_to == Employee.objects.get(user=request.user):
#         # if job is not active, creates an acitivty for it with start time now
#             if job_query.in_work == False:
#                 activity = Activity(
#                     name = request.POST["name"],
#                     start = timezone.now(),
#                     job = job_query,
#                     employee = request.user.employee,
#                 )
#                 activity.save()
                
#                 #makes job object active if it was not
#                 job_query.in_work = True
                
#                 job_query.assigned_to = Employee.objects.get(user=request.user)
                
#                 job_query.save()
#                 messages.success(request,f"Job {job_query.barcode} has been started. ({activity.name})")
#             else:
#                 messages.error(request,f"Job {job_query.barcode} could not be started. Activity already started.")
#             return HttpResponseRedirect(reverse('culet:my_jobs'))
#     else:
#         messages.error(request,f"Job {job_query.barcode} could not be started. User not logged in.")

#helper function for stop_work and clock_out
def stop_activity(activity):
    activity.end = timezone.now()
    activity.active = False
    activity.save()

    if activity.job:
        activity.job.in_work = False
        activity.job.save()


@login_required
def stopWork(request, pk, job_id):
    if request.method != "POST":
        return redirect("culet:my_jobs")

    employee = request.user.employee

    job = get_object_or_404(
        Job,
        id=job_id,
        assigned_to=employee,
        holder=employee,
    )

    act = get_object_or_404(
        Activity,
        id=pk,
        job=job,
        employee=employee,
        active=True,
        end__isnull=True,
    )

    stop_activity(act)

    messages.success(request, f"Job {job.barcode} has been stopped. ({act.name})")
    return redirect("culet:my_jobs")

# def stopWork(request, pk, job_id):
#     #NEED LOGIC TO PREVENT EMP FROM STARTING WORK THAT IS NOT ASSIGNED TO THEM OR IF THEY ARE NOT LOGGED IN
#     act = Activity.objects.get(id=pk)
#     if not act.end:
#         act.end = timezone.now()
#         act.active = False
#         act.save()
#     else:
#         pass
#     job = Job.objects.get(id=job_id)
#     job.in_work = False
#     job.save()
#     # return HttpResponseRedirect(reverse('culet:index_job'))
#     messages.success(request,f"Job {job.barcode} has been stopped. ({act.name})")
#     return HttpResponseRedirect(reverse('culet:my_jobs'))

def createStyle(request):
    new_style = Style()

def clock_in(request):
    if request.user.employee.clocked_in == False:
        clocking_in = TimeClock(
            clock_in = timezone.now(),
            employee = request.user.employee
            )
        clocking_in.save()
        request.user.employee.clocked_in = True
        request.user.employee.save()
        return HttpResponseRedirect(reverse('culet:my_jobs'))
    else:
        messages.success(request, "Already logged in.")
        return HttpResponseRedirect(reverse('culet:my_job'))

@login_required
def clock_out(request):
    if request.method != "POST":
        return redirect("culet:index")

    employee = request.user.employee

    stopped_count = 0

    for activity in employee.active_activities:
        stop_activity(activity)
        stopped_count += 1

    employee.clocked_in = False
    employee.save()

    if stopped_count:
        messages.success(
            request,
            f"You have been clocked out. {stopped_count} active job(s) were stopped."
        )
    else:
        messages.success(request, "You have been clocked out.")

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
            .all()
        )

        context["receipt_lines"] = (
            self.object.receipt_lines
            .select_related("receipt", "part")
            .order_by("-receipt__received_at")
        )

        return context

class MetalLotListView(LoginRequiredMixin, generic.ListView):
    model = MetalLot
    template_name = "inventory/metal_lot_list.html"
    context_object_name = "metal_lots"

    def get_queryset(self):
        return (
            MetalLot.objects
            .select_related("vendor_lot", "vendor_lot__vendor", "part")
            .order_by("-vendor_lot__received_at", "part__sku")
        )

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
            .order_by("-receipt__received_at")
        )

        return context

class MetalReceiptCreateView(LoginRequiredMixin, generic.CreateView):
    model = MetalReceipt
    form_class = MetalReceiptForm
    template_name = "inventory/metal_receipt_create.html"
    success_url = reverse_lazy("culet:metal_vendor_lot_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.POST:
            context["line_formset"] = MetalReceiptLineFormSet(self.request.POST)
        else:
            context["line_formset"] = MetalReceiptLineFormSet()

        return context

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        line_formset = context["line_formset"]

        if not line_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        # Save receipt header
        self.object = form.save(commit=False)
        self.object.received_by = self.request.user
        self.object.save()

        lot_num = form.cleaned_data["lot_num"]
        vendor = form.cleaned_data["vendor"]

        # One vendor lot per vendor + lot number
        vendor_lot, _created = MetalVendorLot.objects.get_or_create(
            vendor=vendor,
            lot_num=lot_num,
        )

        # Save receipt lines and update inventory balances
        for line_form in line_formset:
            if not line_form.has_changed():
                continue

            if line_formset.can_delete and line_form.cleaned_data.get("DELETE"):
                continue

            line = line_form.save(commit=False)
            line.receipt = self.object
            line.vendor_lot = vendor_lot

            metal_lot, _created = MetalLot.objects.get_or_create(
                vendor_lot=vendor_lot,
                part=line.part,
                defaults={
                    "qty_on_hand": Decimal("0"),
                    "weight_on_hand": Decimal("0"),
                    "cost": Decimal("0"),
                },
            )

            update_kwargs = {
                "qty_on_hand": F("qty_on_hand") + (line.qty_received or Decimal("0")),
                "weight_on_hand": F("weight_on_hand") + (line.weight_received or Decimal("0")),
            }

            if line.cost is not None:
                update_kwargs["cost"] = line.cost

            MetalLot.objects.filter(pk=metal_lot.pk).update(**update_kwargs)

            line.metal_lot = metal_lot
            line.save()

        return redirect(self.get_success_url())

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form))
    
class MetalVendorLotListView(LoginRequiredMixin, generic.ListView):
    model = MetalVendorLot
    template_name = "inventory/vendor_lot_list.html"
    context_object_name = "vendor_lots"
    ordering = ["-received_at"]

    def get_queryset(self):
        return (
            MetalVendorLot.objects
            .select_related("vendor")
            .order_by("-received_at")
        )

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

        job = None

        if barcode:
            job = Job.objects.filter(barcode=barcode).first()

        if not job and stock_num:
            job = Job.objects.filter(stock_num=stock_num).first()

        if not job:
            form.add_error(None, "No job found with the provided number.")
            return self.form_invalid(form)

        return redirect("culet:job_weight_create", pk=job.pk)

# Reports Below This Line

class InactiveJobsReportView(LoginRequiredMixin, generic.TemplateView):
    template_name = "reports/inactive_jobs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        form = InactiveJobsReportForm(self.request.GET or None)
        jobs = Job.objects.none()
        cutoff = None

        if form.is_valid():
            days = form.cleaned_data["days"]
            cutoff = timezone.now() - timedelta(days=days)

            jobs = (
                Job.objects
                .filter(active=True, shipped=False)
                .annotate(last_activity_start=Max("activity__start"))
                .filter(
                    Q(last_activity_start__lt=cutoff) |
                    Q(last_activity_start__isnull=True, created__lt=cutoff)
                )
                .select_related(
                    "customer",
                    "style",
                    "assigned_to__user",
                    "holder__user",
                    "status",
                    "location",
                )
                .order_by("last_activity_start", "created")
            )

        context["form"] = form
        context["jobs"] = jobs
        context["cutoff"] = cutoff
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
            .filter(clocked_in=True)
            .exclude(
                activity__active=True,
                activity__end__isnull=True,
            )
            .select_related("user", "department_fk", "role_fk")
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
                .select_related("user", "department_fk", "role_fk")
                .filter(role_fk__requires_clock_in=True)
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = date.today()

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
                "holder__user",
                "status",
                "location",
            )
            .order_by("due", "barcode")
        )

        job_rows = []

        for job in jobs:
            job_rows.append({
                "job": job,
                "days_late": (today - job.due).days,
            })

        context["today"] = today
        context["job_rows"] = job_rows
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
                "department_fk",
                "role_fk",
            )
            .distinct()
            .order_by(
                "department_fk__name",
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
                    department_fk=selected_department
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

            dept = employee.department_fk

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
    
class BulkJobShipView(LoginRequiredMixin, generic.TemplateView):
    template_name = "jobs/job_ship_bulk.html"
    success_url = reverse_lazy("culet:job_ship_bulk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = BulkJobShipForm()
        context["formset"] = JobShipLineFormSet(prefix="ship_lines")
        return context

    def post(self, request, *args, **kwargs):
        form = BulkJobShipForm(request.POST)
        formset = JobShipLineFormSet(request.POST, prefix="ship_lines")

        if not form.is_valid() or not formset.is_valid():
            return self.render_to_response({
                "form": form,
                "formset": formset,
            })

        employee = get_object_or_404(Employee, user=request.user)
        notes = form.cleaned_data.get("notes", "")

        barcodes = []

        for line_form in formset:
            barcode = line_form.cleaned_data.get("barcode")

            if barcode:
                barcode = (
                    barcode
                    .strip()
                    .replace("\n", "")
                    .replace("\r", "")
                    .replace("\t", "")
                )

            if barcode:
                barcodes.append(barcode)

        if not barcodes:
            messages.error(request, "Scan at least one barcode.")
            return self.render_to_response({
                "form": form,
                "formset": formset,
            })

        duplicate_barcodes = {
            barcode for barcode in barcodes
            if barcodes.count(barcode) > 1
        }

        if duplicate_barcodes:
            messages.error(
                request,
                f"Duplicate barcode(s): {', '.join(duplicate_barcodes)}"
            )
            return self.render_to_response({
                "form": form,
                "formset": formset,
            })

        jobs_by_barcode = {}
        missing_barcodes = []
        already_shipped_barcodes = []

        for barcode in barcodes:
            job = Job.objects.filter(
                barcode__iexact=barcode,
            ).first()

            if not job:
                missing_barcodes.append(barcode)
                continue

            if job.shipped:
                already_shipped_barcodes.append(barcode)
                continue

            jobs_by_barcode[barcode] = job

        if missing_barcodes:
            messages.error(
                request,
                f"No job found for barcode(s): {', '.join(missing_barcodes)}"
            )
            return self.render_to_response({
                "form": form,
                "formset": formset,
            })

        if already_shipped_barcodes:
            messages.error(
                request,
                f"Already shipped barcode(s): {', '.join(already_shipped_barcodes)}"
            )
            return self.render_to_response({
                "form": form,
                "formset": formset,
            })

        with transaction.atomic():
            for barcode in barcodes:
                job = jobs_by_barcode[barcode]

                job.shipped = True
                job.active = False
                job.in_work = False
                job.holder = employee
                job.save(update_fields=[
                    "shipped",
                    "active",
                    "in_work",
                    "holder",
                    "last_updated",
                ])

                JobShip.objects.create(
                    job=job,
                    shipped_by=employee,
                    notes=notes,
                )

        messages.success(
            request,
            f"Shipped {len(barcodes)} job(s)."
        )

        return redirect(self.success_url)