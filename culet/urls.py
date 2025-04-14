from django.urls import path
from . import views

app_name = "culet"
urlpatterns = [
    path("jobs/", views.JobListView.as_view(), name="index_job"),
    path("jobs/<int:pk>/", views.JobDetailView.as_view(), name="job_detail"),
    path("jobs/<int:pk>/edit/", views.JobUpdateView.as_view(), name="job_update"),
    path("jobs/create", views.JobCreateView.as_view(), name="job_create"),
    path("styles/", views.StyleListView.as_view(), name="index_style"),
    path("styles/create", views.StyleCreateView.as_view(), name="style_create"),
    path("activities", views.ActivityListView.as_view(),name="activities_index"),
    path("activities/start", views.startWork,name="start_work"),
    path("activities/<int:pk>/stop/<int:job_id>",views.stopWork,name="stop_work"),
    path("jobs/my_jobs/", views.MyJobListView.as_view(), name="my_jobs"),
    path("jobs/assign",views.AssignJobView.as_view(), name="assign_job"),
    path("reporting", views.ReportingListView.as_view(), name="reporting"),
    path("clock_in",views.clock_in,name="clock_in"),
    path("clock_out", views.clock_out,name="clock_out"),
    path("receive", views.receive,name='receive'),
]