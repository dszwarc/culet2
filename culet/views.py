from django.shortcuts import render
from django.http import HttpResponse
from django.template import loader
from .models import Job
# Create your views here.


def index(request):
    latest_job_list = Job.objects.order_by("-customer_name")[:5]
    template = loader.get_template("jobs/index.html")
    context = {
        "latest_job_list": latest_job_list,
    }
    return HttpResponse(template.render(context,request))

def detail(request, job_id):
    return HttpResponse("You're looking at job number %s." % job_id)

def results(request, job_id):
    response = "You're looking at the results of the job %s."
    return HttpResponse(response % job_id)

def edit(request, job_id):
    return HttpResponse("You're editing job number %s." % job_id)

