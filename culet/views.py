from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from .models import MetalVendorLot, MetalReceiptLine, Job, Style, Activity, Employee, TimeClock, StyleMetal, StyleStone, MetalLot, MetalReceipt
from django.views import generic
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from .filters import JobFilter, ActivityFilter
from .forms import JobForm, StyleForm, JobUpdateForm, StyleMetalFormSet, StyleStoneFormSet, MetalLotFormSet, MetalReceiptForm, MetalReceiptLineForm, MetalReceiptLineFormSet
from django.template.loader import render_to_string
import copy
from django.db import transaction
from django.db.models import F

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
    
class MyJobListView(LoginRequiredMixin,generic.ListView):
    model = Job
    template_name = "jobs/my_jobs.html"
    context_object_name = "latest_job_list"
    def get_queryset(self):
        employee = Employee.objects.get(user=self.request.user)
        job_query = Job.objects.filter(assigned_to=employee,location=employee)
        return job_query

class ReceiveListView(LoginRequiredMixin, generic.ListView):
    model = Job
    template_name = "jobs/receive_list.html"
    context_object_name = "receive_list"
    def get_queryset(self):
        employee = Employee.objects.get(user=self.request.user)
        job_query = Job.objects.filter(assigned_to=employee).exclude(location=employee)
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

class JobDetailView(LoginRequiredMixin,generic.DetailView):
    model = Job
    template_name = "jobs/detail.html"
    
    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        related_activities = Activity.objects.filter(job=data['job'])
        data['activity'] = related_activities
        return data

class JobCreateView(LoginRequiredMixin,generic.CreateView):
    model = Job
    form_class = JobForm
    template_name = "jobs/create.html"
    # fields = ['name','customer', 'job_num', 'style', 'due']
    # exclude = ['created','last_updated']*
    success_url=reverse_lazy('culet:index_job')

class JobUpdateView(LoginRequiredMixin,generic.UpdateView):
    model = Job
    form_class = JobUpdateForm
    template_name = "jobs/update.html"
    success_url=reverse_lazy('culet:index_job')

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
    def get(self, request, *args, **kwargs):
        context = {'employees':Employee.objects.all()}
        return render(request, 'jobs/assign.html', context)
    def post(self, request, *args, **kwargs):
        job = Job.objects.get(job_num=request.POST["job"])
        job.assigned_to = Employee.objects.get(id=request.POST["employee"])
        job.save()
        messages.success(request,f"Job {job.job_num} has been assigned.")
        #return render(request, 'jobs/assign.html')
        return HttpResponseRedirect(reverse('culet:assign_job'))

def startWork(request):
    #NEED LOGIC TO PREVENT EMP FROM STARTING WORK THAT IS NOT ASSIGNED TO THEM OR IF THEY ARE NOT LOGGED IN
    if request.user:
        job_query = Job.objects.get(job_num=request.POST["job"])

        #check to see if job that is being queried is assigned to the user before allowing user to start work.
        if job_query.assigned_to == Employee.objects.get(user=request.user):
        # if job is not active, creates an acitivty for it with start time now
            if job_query.in_work == False:
                activity = Activity(
                    name = request.POST["name"],
                    start = timezone.now(),
                    job = job_query,
                    employee = request.user.employee,
                )
                activity.save()
                
                #makes job object active if it was not
                job_query.in_work = True
                
                job_query.assigned_to = Employee.objects.get(user=request.user)
                
                job_query.save()
                messages.success(request,f"Job {job_query.job_num} has been started. ({activity.name})")
            else:
                messages.error(request,f"Job {job_query.job_num} could not be started. Activity already started.")
            return HttpResponseRedirect(reverse('culet:my_jobs'))
    else:
        messages.error(request,f"Job {job_query.job_num} could not be started. User not logged in.")
def stopWork(request, pk, job_id):
    #NEED LOGIC TO PREVENT EMP FROM STARTING WORK THAT IS NOT ASSIGNED TO THEM OR IF THEY ARE NOT LOGGED IN
    act = Activity.objects.get(id=pk)
    if not act.end:
        act.end = timezone.now()
        act.active = False
        act.save()
    else:
        pass
    job = Job.objects.get(id=job_id)
    job.in_work = False
    job.save()
    # return HttpResponseRedirect(reverse('culet:index_job'))
    messages.success(request,f"Job {job.job_num} has been stopped. ({act.name})")
    return HttpResponseRedirect(reverse('culet:my_jobs'))

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
        return HttpResponseRedirect(reverse('culet:index_job'))
    else:
        messages.success(request, "Already logged in.")
        return HttpResponseRedirect(reverse('culet:index_job'))

def clock_out(request):
    if request.user.employee.clocked_in == True:

        activities_in_progress = Activity.objects.filter(employee=request.user.employee,active=True)
        print(activities_in_progress)
        for act in activities_in_progress:
            stopWork(request,act.id,act.job.id)

        clocking_out = TimeClock.objects.filter(employee=request.user.employee,clock_out__isnull=True)
        for obj in clocking_out:
            obj.clock_out = timezone.now()
            obj.save()
        request.user.employee.clocked_in = False
        request.user.employee.save()

        return HttpResponseRedirect(reverse('culet:index_job'))
    else:
        messages.success(request, "Can't clock out because you are not clocked in.")
        return HttpResponseRedirect(reverse('culet:index_job'))

def receive(request):
    #NOT TESTED. NEEDS UPDATING FOR LIMITING WHEN THIS IS ALLOWED
    job = Job.objects.get(job_num=request.POST["job"])
    job.location = request.user.employee
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