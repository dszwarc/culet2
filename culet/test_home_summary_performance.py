from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Activity, ActivityStep, Customer, Department, Employee, Job, JobMovement, MovementType, Role, Style, TimeClock
from .views import get_home_summary_context


class HomeSummaryPerformanceTests(TestCase):
    def test_counts_preserve_duplicates_exclusions_and_movement_cutoff(self):
        now = timezone.now()
        dept = Department.objects.create(name="Summary test department")
        clock_role = Role.objects.create(name="Summary clock role", requires_clock_in=True)
        exempt_role = Role.objects.create(name="Summary exempt role", requires_clock_in=False)
        workers = []
        for name, role in (("working", clock_role), ("idle", clock_role), ("exempt", exempt_role)):
            user = User.objects.create_user(username=f"summary-{name}")
            workers.append(Employee.objects.create(user=user, department=dept, role=role))
        working, idle, exempt = workers
        for employee in (working, working, idle, exempt):
            TimeClock.objects.create(employee=employee, clock_in=now - timedelta(hours=1))
        customer = Customer.objects.create(name="Summary customer")
        style = Style.objects.create(name="SUMMARY", customer=customer)
        step = ActivityStep.objects.create(name="Summary work", code="summary-work")
        old = now - timedelta(days=10)
        due = timezone.localdate() - timedelta(days=1)
        def job(number, **values):
            defaults = dict(barcode=number, stock_num=f"SUMMARY-{number}", style=style,
                            due=due, created=old)
            defaults.update(values)
            return Job.objects.create(**defaults)
        active = job(76001)
        idle_old = job(76002, due=timezone.localdate() + timedelta(days=1))
        recently_moved = job(76003)
        shipped = job(76004, shipped=True)
        inactive = job(76005, active=False)
        for _ in range(2):
            Activity.objects.create(employee=working, job=active, step=step, start=now)
        Activity.objects.create(employee=idle, job=shipped, step=step, start=now)
        Activity.objects.create(employee=idle, job=inactive, step=step, start=now)
        movement_type = MovementType.objects.create(name="Summary move", code="summary-move", job_field="holder")
        JobMovement.objects.create(job=recently_moved, movement_type=movement_type)
        # An old last movement still determines inactivity, even for an older job.
        movement = JobMovement.objects.create(job=active, movement_type=movement_type)
        JobMovement.objects.filter(pk=movement.pk).update(created_at=old)
        with self.assertNumQueries(5):
            summary = get_home_summary_context()
        self.assertEqual(summary, {
            "jobs_in_work_count": 1,
            "clocked_in_count": 2,
            "idle_employee_count": 1,
            "late_jobs_count": 2,
            "inactive_jobs_count": 2,
            "shipped_today_count": 0,
        })

    def test_home_only_computes_visible_manager_summary(self):
        from unittest.mock import patch
        from django.urls import reverse

        role = Role.objects.create(name="Summary ordinary role", level=0)
        user = User.objects.create_user(username="summary-home")
        Employee.objects.create(user=user, role=role, must_change_password=False)
        self.client.force_login(user)
        with patch("culet.views.get_home_summary_context", return_value={"jobs_in_work_count": 37}) as summary:
            response = self.client.get(reverse("culet:home"))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Operations Summary")
            summary.assert_not_called()
            role.level = 30
            role.save(update_fields=["level"])
            response = self.client.get(reverse("culet:home"))
            self.assertContains(response, "Operations Summary")
            self.assertContains(response, "37")
            summary.assert_called_once_with()
