from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect
from .models import Job, Style, Activity
from django.views import generic
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.contrib import messages

class JobListView(generic.ListView):
    model = Job
    template_name = "jobs/index.html"
    context_object_name = "latest_job_list"
    def get_queryset(self):
        return Job.objects.order_by("-name")

class ActivityListView(generic.ListView):
    model = Activity
    template_name = "activities/index.html"
    context_object_name = "activities"
    def get_queryset(self):
        return Activity.objects.order_by("-start")

class JobDetailView(generic.DetailView):
    model = Job
    template_name = "jobs/detail.html"
    
    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        related_activities = Activity.objects.filter(job=data['job'])
        data['activity'] = related_activities
        return data

class JobCreateView(generic.CreateView):
    model = Job
    template_name = "jobs/create.html"
    fields = ['name','customer', 'job_num', 'style', 'due']
    # exclude = ['created','last_updated']*
    success_url=reverse_lazy('culet:index_job')

class JobUpdateView(generic.UpdateView):
    model = Job
    template_name = "jobs/update.html"
    fields = '__all__'
    success_url=reverse_lazy('culet:index_job')

def results(request, job_id):
    response = "You're looking at the results of the job %s."
    return HttpResponse(response % job_id)

def edit(request, job_id):
    return HttpResponse("You're editing job number %s." % job_id)

class StyleListView(generic.ListView):
    model = Style
    template_name = "styles/index.html"
    context_object_name = "style_list"
    # def get_queryset(self):
    #     return Job.objects.order_by("-name")[:5]

class StyleCreateView(generic.CreateView):
    model = Style
    template_name = "styles/create.html"
    fields = '__all__'
    success_url=reverse_lazy('culet:index_style')

def startWork(request):
    job_query = Job.objects.get(job_num=request.POST["job"])
    if job_query.active == False:
        activity = Activity(
            name = request.POST["name"],
            start = timezone.now(),
            job = job_query,
        )
        activity.save()
        job_query.active = True
        job_query.save()
        messages.success(request,f"Job {job_query.job_num} has been started. ({activity.name})")
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