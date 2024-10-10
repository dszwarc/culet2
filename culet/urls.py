from django.urls import path
from . import views

app_name = "culet"
urlpatterns = [
    path("", views.JobListView.as_view(), name="index"),
    path("<int:pk>/", views.DetailView.as_view(), name="detail"),
    path("<int:job_id>/results/", views.results, name="results"),
    path("<int:job_id>/edit/", views.edit, name="edit"),
]