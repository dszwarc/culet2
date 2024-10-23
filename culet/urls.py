from django.urls import path
from . import views

app_name = "culet"
urlpatterns = [
    path("job", views.JobListView.as_view(), name="index_job"),
    path("job/<int:pk>/", views.JobDetailView.as_view(), name="job_detail"),
    path("job/<int:job_id>/edit/", views.JobUpdateView.as_view(), name="job_update"),
    path("job/create", views.JobCreateView.as_view(), name="job_create"),
]