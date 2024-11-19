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
    path("activities", views.ActivityListView.as_view(),name="activites_index"),
    # path("activities/start", views.startWork.as_view(),name="start_work"),
]