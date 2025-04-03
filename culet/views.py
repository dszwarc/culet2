from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect
from .models import Job, Style, Activity, Employee
from django.views import generic
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from .filters import JobFilter
from .forms import JobForm, StyleForm

class JobListView(LoginRequiredMixin,generic.ListView):
    model = Job
    template_name = "jobs/index.html"
    paginate_by = 2

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
        return Job.objects.filter(assigned_to=Employee.objects.get(user=self.request.user))

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
    template_name = "jobs/update.html"
    fields = '__all__'
    success_url=reverse_lazy('culet:index_job')

def results(request, job_id):
    response = "You're looking at the results of the job %s."
    return HttpResponse(response % job_id)

def edit(request, job_id):
    return HttpResponse("You're editing job number %s." % job_id)

class StyleListView(LoginRequiredMixin,generic.ListView):
    model = Style
    template_name = "styles/index.html"
    context_object_name = "style_list"
    # def get_queryset(self):
    #     return Job.objects.order_by("-name")[:5]

class StyleCreateView(LoginRequiredMixin,generic.CreateView):
    model = Style
    form_class = StyleForm
    template_name = "styles/create.html"
    # fields = '__all__'
    success_url=reverse_lazy('culet:index_style')

class AssignJobView(LoginRequiredMixin,generic.TemplateView):
    def get(self, request, *args, **kwargs):
        context = {'employees':Employee.objects.all()}
        return render(request, 'jobs/assign.html', context)
    def post(self, request, *args, **kwargs):
        job = Job.objects.get(job_num=request.POST["job"])
        job.assigned_to = Employee.objects.get(id=request.POST["employee"])
        job.save()
        messages.success(request,f"Job {job.job_num} has been assigned.")
        return render(request, 'jobs/assign.html')
def startWork(request):
    job_query = Job.objects.get(job_num=request.POST["job"])

    # if job is not active, creates an acitivty for it with start time now
    if job_query.active == False:
        activity = Activity(
            name = request.POST["name"],
            start = timezone.now(),
            job = job_query,
        )
        activity.save()
        
        #makes job object active if it was not
        job_query.active = True
        
        # 
        job_query.assigned_to = Employee.objects.get(user=request.user)
        
        job_query.save()
        messages.success(request,f"Job {job_query.job_num} has been started. ({activity.name})")
    else:
        messages.error(request,f"Job {job_query.job_num} could not be started. Activity already started.")
    return HttpResponseRedirect(reverse('culet:job_detail', kwargs={'pk' : job_query.id }))

def stopWork(request, pk, job_id):
    activ = Activity.objects.get(id=pk)
    if not activ.end:
        activ.end = timezone.now()
        activ.active = False
        activ.save()
    else:
        pass
    job = Job.objects.get(id=job_id)
    job.active = False
    job.save()
    # return HttpResponseRedirect(reverse('culet:index_job'))
    messages.success(request,f"Job {job.job_num} has been stopped. ({activ.name})")
    return HttpResponseRedirect(reverse('culet:job_detail', kwargs={'pk' : job_id }))