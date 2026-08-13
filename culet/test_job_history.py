from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Activity, Customer, Employee, Job, JobMovement, MovementType, Style
from .services import get_job_history, get_job_history_page


class JobHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="history-user", password="test")
        self.employee = Employee.objects.create(user=self.user, must_change_password=False)
        customer = Customer.objects.create(
            name="History Customer", address="1 Main", email="history@example.com", phone="555-0100"
        )
        style = Style.objects.create(name="HISTORY-STYLE", customer=customer)
        self.job = Job.objects.create(
            name="History Job", barcode=987654, stock_num="HISTORY-1", style=style,
            due=timezone.localdate() + timedelta(days=7),
        )
        self.movement_type = MovementType.objects.create(
            name="Received", code="received", job_field=MovementType.JobField.HOLDER
        )
        self.client.force_login(self.user)

    def activity(self, when, **kwargs):
        defaults = {"name": "Polish", "employee": self.employee, "job": self.job, "start": when}
        defaults.update(kwargs)
        return Activity.objects.create(**defaults)

    def movement(self, when, **kwargs):
        defaults = {"job": self.job, "movement_type": self.movement_type}
        defaults.update(kwargs)
        movement = JobMovement.objects.create(**defaults)
        JobMovement.objects.filter(pk=movement.pk).update(created_at=when)
        movement.refresh_from_db()
        return movement

    def test_only_activities_and_only_movements_are_normalized(self):
        activity = self.activity(timezone.now())
        self.assertEqual([(e.event_type, e.event_id) for e in get_job_history(self.job)], [("activity", activity.pk)])

        activity.delete()
        movement = self.movement(timezone.now())
        self.assertEqual([(e.event_type, e.event_id) for e in get_job_history(self.job)], [("movement", movement.pk)])

    def test_events_interleave_newest_first_using_activity_start(self):
        base = timezone.now()
        older = self.activity(base, end=base + timedelta(hours=3))
        middle = self.movement(base + timedelta(hours=1))
        newer = self.activity(base + timedelta(hours=2))

        events = get_job_history(self.job)
        self.assertEqual(
            [(event.event_type, event.event_id) for event in events],
            [("activity", newer.pk), ("movement", middle.pk), ("activity", older.pk)],
        )

    def test_equal_timestamp_puts_movement_first_then_pk_descending(self):
        when = timezone.now()
        first_activity = self.activity(when)
        second_activity = self.activity(when)
        movement = self.movement(when)

        events = get_job_history(self.job)
        self.assertEqual(events[0].event_type, "movement")
        self.assertEqual([event.event_id for event in events[1:]], [second_activity.pk, first_activity.pk])

    def test_nullable_values_render_without_errors_and_action_is_preserved(self):
        now = timezone.now()
        self.activity(now, end=None, active=True)
        self.movement(now - timedelta(minutes=1), from_employee=None, to_employee=None, performed_by=None)

        response = self.client.get(reverse("culet:job_detail", args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stop Work")
        self.assertContains(response, "Job History")
        self.assertNotContains(response, "Movement History")
        self.assertNotContains(response, "Activity History")

    def test_initial_page_is_limited_and_load_more_returns_only_next_batch(self):
        base = timezone.now()
        activities = [self.activity(base + timedelta(minutes=index)) for index in range(21)]

        response = self.client.get(reverse("culet:job_detail", args=[self.job.pk]))
        self.assertEqual(len(response.context["history_events"]), 10)
        self.assertContains(response, "Load 10 More")

        partial = self.client.get(reverse("culet:job_history_partial", args=[self.job.pk]), {"offset": 10})
        returned_ids = [event.event_id for event in partial.context["history_events"]]
        expected_ids = [activity.pk for activity in reversed(activities)][10:20]
        self.assertEqual(returned_ids, expected_ids)
        self.assertNotIn(activities[-1].pk, returned_ids)
        self.assertContains(partial, "Load 10 More")

        final = self.client.get(reverse("culet:job_history_partial", args=[self.job.pk]), {"offset": 20})
        self.assertEqual(len(final.context["history_events"]), 1)
        self.assertNotContains(final, "Load 10 More")

    def test_fewer_than_ten_events_has_no_load_more(self):
        self.activity(timezone.now())
        response = self.client.get(reverse("culet:job_detail", args=[self.job.pk]))
        self.assertNotContains(response, "Load 10 More")

    def test_page_helper_returns_stable_non_overlapping_slices(self):
        base = timezone.now()
        for index in range(15):
            self.activity(base + timedelta(minutes=index))
        first, has_more, next_offset = get_job_history_page(self.job)
        second, second_has_more, _ = get_job_history_page(self.job, offset=next_offset)
        self.assertTrue(has_more)
        self.assertFalse(second_has_more)
        self.assertEqual(len(first), 10)
        self.assertEqual(len(second), 5)
        self.assertTrue({event.event_id for event in first}.isdisjoint(event.event_id for event in second))
