from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:job_id>/", views.detail, name="detail"),
    path("<int:job_id>/results/", views.results, name="results"),
    path("<int:job_id>/edit/", views.edit, name="edit"),
]