from django.shortcuts import render
from django.http import HttpResponse
from django.template import loader
from .models import Job
from django.views import generic
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

class DetailView(generic.DetailView):
    model = Job
    template_name = "jobs/detail.html"

def results(request, job_id):
    response = "You're looking at the results of the job %s."
    return HttpResponse(response % job_id)

def edit(request, job_id):
    return HttpResponse("You're editing job number %s." % job_id)

