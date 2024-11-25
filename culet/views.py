from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect
from .models import Job, Style, Activity, Department
from django.views import generic
from django.urls import reverse_lazy, reverse
from django.utils import timezone
# Create your views here.


# def index(request):
#     latest_job_list = Job.objects.order_by("-customer_name")[:5]
#     template = loader.get_template("jobs/index.html")
#     context = {
#         "latest_job_list": latest_job_list,
#     }
#     return HttpResponse(template.render(context,request))

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

# def detail(request, job_id):
#     return HttpResponse("You're looking at job number %s." % job_id)

class JobDetailView(generic.DetailView):
    model = Job
    template_name = "jobs/detail.html"
    
    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        print(data, "  <--- this is data")
        related_activities = Activity.objects.filter(job=data['job'])
        data['activity'] = related_activities
        print(data, "  <---------- data with related activites")
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
    print(request.POST)
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
    else:
        print(job_query)
    return HttpResponseRedirect(reverse('culet:index_job'))

def stopWork(request, pk, job_id):
    print(pk)
    print(job_id)
    print("stop work test")
    activ = Activity.objects.get(id=pk)
    print(activ, " <---- this is stop work activ")
    print(activ.end, " <--- this is activ.end")
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
    return HttpResponseRedirect(reverse('culet:job_detail', kwargs={'pk' : '7'}))