from django.http import HttpResponse
from .models import Job
from django.views import generic
from django.urls import reverse_lazy
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
        return Job.objects.order_by("-name")[:5]

# def detail(request, job_id):
#     return HttpResponse("You're looking at job number %s." % job_id)

class JobDetailView(generic.DetailView):
    model = Job
    template_name = "jobs/detail.html"

class JobCreateView(generic.CreateView):
    model = Job
    template_name = "jobs/create.html"
    fields = ['name','customer', 'job_num', 'style', 'due', 'last_updated']
    # exclude = ['created','last_updated']
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

