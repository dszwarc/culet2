from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from .models import (
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
    JobStatus
)

from django.views import generic
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from .filters import JobFilter, ActivityFilter
from .forms import (
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
    )
from django.template.loader import render_to_string
import copy
from django.db import transaction
from django.db.models import F

class HomeView(LoginRequiredMixin, generic.TemplateView):
    template_name = "home.html"

def index(request):
    return render(request, 'authentication/login.html')

class JobListView(LoginRequiredMixin,generic.ListView):
    model = Job
    template_name = "jobs/index.html"

    # context_object_name = "latest_job_list"
    # def get_queryset(self):
    #     return Job.objects.order_by("-name")
    def get_context_data(self,**kwargs):
        jobs = Job.objects.all()
        myFilter = JobFilter(self.request.GET,queryset=jobs)
        filt_jobs = myFilter.qs
        context = {'latest_job_list':filt_jobs, 'filter':myFilter}

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

class ReceiveListView(LoginRequiredMixin, generic.ListView):
    model = Job
    template_name = "jobs/receive_list.html"
    context_object_name = "receive_list"
    def get_queryset(self):
        employee = Employee.objects.get(user=self.request.user)
        job_query = Job.objects.filter(assigned_to=employee).exclude(holder=employee)
        return job_query
    
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

# class JobDetailView(LoginRequiredMixin,generic.DetailView):
#     model = Job
#     template_name = "jobs/detail.html"
    
#     def get_context_data(self, **kwargs):
#         data = super().get_context_data(**kwargs)
#         related_activities = Activity.objects.filter(job=data['job'])
#         data['activity'] = related_activities
#         return data
    
class JobDetailView(LoginRequiredMixin, generic.DetailView):
    model = Job
    template_name = "jobs/detail.html"
    context_object_name = "job"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["job_metals"] = JobMetal.objects.filter(job=context['job'])
        context["job_stones"] = JobStone.objects.filter(job=context['job'])
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
            context["metal_formset"] = JobMetalFormSet(
                self.request.POST,
                prefix="metals",
            )
            context["stone_formset"] = JobStoneFormSet(
                self.request.POST,
                prefix="stones",
            )
        else:
            context["metal_formset"] = JobMetalFormSet(prefix="metals")
            context["stone_formset"] = JobStoneFormSet(prefix="stones")

        return context

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]

        if not (metal_formset.is_valid() and stone_formset.is_valid()):
            return self.render_to_response(
                self.get_context_data(form=form)
            )

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

        JobMetalCreateFormSet = get_job_metal_formset(
            extra=len(metal_initial)
        )
        JobStoneCreateFormSet = get_job_stone_formset(
            extra=len(stone_initial)
        )

        metal_formset = JobMetalCreateFormSet(
            prefix="metals",
            initial=metal_initial,
        )
        stone_formset = JobStoneCreateFormSet(
            prefix="stones",
            initial=stone_initial,
        )

        return render(
            request,
            self.template_name,
            {
                "job_form": job_form,
                "metal_formset": metal_formset,
                "stone_formset": stone_formset,
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

        # Roll back existing assignments to inventory before resaving
        for existing in job_metal.lot_assignments.all():
            MetalLot.objects.filter(pk=existing.metal_lot_id).update(
                qty_on_hand=F("qty_on_hand") + existing.qty_used,
                weight_on_hand=F("weight_on_hand") + existing.weight_used,
            )

        existing_ids = list(job_metal.lot_assignments.values_list("id", flat=True))
        formset.save()

        # Re-fetch current assignments and decrement inventory
        for assignment in job_metal.lot_assignments.all():
            MetalLot.objects.filter(pk=assignment.metal_lot_id).update(
                qty_on_hand=F("qty_on_hand") - assignment.qty_used,
                weight_on_hand=F("weight_on_hand") - assignment.weight_used,
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
        else:
            context["metal_formset"] = JobMetalFormSet(instance=self.object, prefix="metals")
            context["stone_formset"] = JobStoneFormSet(instance=self.object, prefix="stones")

        return context

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]

        if not (metal_formset.is_valid() and stone_formset.is_valid()):
            return self.render_to_response(self.get_context_data(form=form))

        self.object = form.save()
        metal_formset.instance = self.object
        metal_formset.save()

        stone_formset.instance = self.object
        stone_formset.save()

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
        else:
            context["metal_formset"] = StyleMetalFormSet()
            context["stone_formset"] = StyleStoneFormSet()

        return context

    def form_valid(self, form):
        context = self.get_context_data()
        metal_formset = context["metal_formset"]
        stone_formset = context["stone_formset"]

        if metal_formset.is_valid() and stone_formset.is_valid():
            self.object = form.save()  # save Style first
            metal_formset.instance = self.object
            metal_formset.save()

            stone_formset.instance = self.object
            stone_formset.save()
            return redirect(self.get_success_url())

        # If formset invalid, re-render page with errors
        return self.render_to_response(self.get_context_data(form=form))

class AssignJobView(LoginRequiredMixin,generic.TemplateView):
    def get_assignable_employees(self):
        current_employee = self.request.user.employee

        base_qs = (
            Employee.objects.select_related("user","department_fk","role_fk")
            .order_by("role_fk__name", "user__last_name","user__first_name")
        )

        role_name = (current_employee.role_fk.name or "").lower() if current_employee.role_fk else ""
        dept_name = (current_employee.department_fk.name or "").lower() if current_employee.department_fk else ""

        #Super + Office can assign to everyone
        if role_name == "super" and dept_name == "office":
            return base_qs
        
        # Managers can assign to managers + employees in their department
        if role_name == "manager":
            return base_qs.filter(pk=current_employee.pk)

    def get(self, request, *args, **kwargs):
        employees = self.get_assignable_employees()
        
        managers = employees.filter(role_fk__name__iexact="Manager")
        non_managers = employees.exclude(role_fk__name__iexact="Manager")

        context = {
            'managers': managers,
            'employees':employees,
                   }
        
        return render(request, 'jobs/assign.html', context)
    
    def post(self, request, *args, **kwargs):
        assignable_employees = self.get_assignable_employees()

        job = get_object_or_404(Job, job_num=request.POST.get("job"))
        employee = get_object_or_404(
            assignable_employees,
            id=request.POST.get("employee")
        )

        job.assigned_to = employee
        job.location = employee
        job.save()

        messages.success(request, f"Job {job.job_num} has been assigned to {employee}.")
        return redirect("culet:assign_job")

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
        messages.error(request, f"Job {job.job_num} could not be started. Activity already started.")
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

    messages.success(request, f"Job {job.job_num} has been started. ({activity.step.name})")
    return redirect("culet:my_jobs")

# def startWork(request):
#     #NEED LOGIC TO PREVENT EMP FROM STARTING WORK THAT IS NOT ASSIGNED TO THEM OR IF THEY ARE NOT LOGGED IN
#     if request.user:
#         job_query = Job.objects.get(job_num=request.POST["job"])

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
#                 messages.success(request,f"Job {job_query.job_num} has been started. ({activity.name})")
#             else:
#                 messages.error(request,f"Job {job_query.job_num} could not be started. Activity already started.")
#             return HttpResponseRedirect(reverse('culet:my_jobs'))
#     else:
#         messages.error(request,f"Job {job_query.job_num} could not be started. User not logged in.")

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

    messages.success(request, f"Job {job.job_num} has been stopped. ({act.name})")
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
#     messages.success(request,f"Job {job.job_num} has been stopped. ({act.name})")
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

def receive(request):
    #NOT TESTED. NEEDS UPDATING FOR LIMITING WHEN THIS IS ALLOWED
    job = Job.objects.get(job_num=request.POST["job"])
    job.holder = request.user.employee
    job.save()
    messages.success(request, f"Job {job.job_num} Received")
    return HttpResponseRedirect(reverse('culet:my_jobs'))

class MetalVendorLotListView(LoginRequiredMixin, generic.ListView):
    model = MetalVendorLot
    template_name = "inventory/vendor_lot_list.html"
    context_object_name = "lots"
    ordering = ["-received_at"]

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
                defaults={"qty_on_hand": 0},
            )

            MetalLot.objects.filter(pk=metal_lot.pk).update(
                qty_on_hand=F("qty_on_hand") + line.qty_received,
                weight_on_hand = F("weight_on_hand") + line.weight_received,
            )

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
        messages.success(self.request, f"Weight recorded for job {self.job.job_num}.")
        return redirect(self.job.get_absolute_url())
    
class JobWeightLookupView(LoginRequiredMixin, generic.FormView):
    template_name = "jobs/weight_lookup.html"
    form_class = JobWeightLookupForm

    def form_valid(self, form):
        job_num = form.cleaned_data.get("job_num")
        customer_ref_num = form.cleaned_data.get("customer_ref_num")

        job = None

        if job_num:
            job = Job.objects.filter(job_num=job_num).first()

        if not job and customer_ref_num:
            job = Job.objects.filter(customer_ref_num=customer_ref_num).first()

        if not job:
            form.add_error(None, "No job found with the provided number.")
            return self.form_invalid(form)

        return redirect("culet:job_weight_create", pk=job.pk)