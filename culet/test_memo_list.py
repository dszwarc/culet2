from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import JobTransferMemo, JobTransferMemoLine, PieceworkMemo, PieceworkMemoLine
from .test_piecework_integrity import CuletTestDataMixin


class MemoStockNumberFilterTests(CuletTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.job = self.make_job(91001)
        self.other_job = self.make_job(91002)
        self.expected = set()
        for job in (self.job, self.other_job):
            for _ in range(2):
                transfer = JobTransferMemo.objects.create(created_by=self.manager)
                JobTransferMemoLine.objects.create(memo=transfer, job=job)
                piecework = PieceworkMemo.objects.create(
                    created_by=self.manager, assigned_to=self.worker,
                    from_location=self.office, to_location=self.piecework,
                )
                # Include returned memos as well as the current open memo.
                returned_at = timezone.now() if _ == 0 else None
                PieceworkMemoLine.objects.create(
                    memo=piecework, job=job, returned_at=returned_at,
                )
                if returned_at:
                    PieceworkMemo.objects.filter(pk=piecework.pk).update(returned_at=returned_at)
                if job == self.job:
                    self.expected.update((transfer.memo_num, piecework.memo_num))

    def test_stock_number_returns_all_matching_memo_types_and_history(self):
        response = self.client.get(reverse("culet:memo_list"), {
            "stock_num": f"  {self.job.stock_num.lower()}  ",
        })
        self.assertContains(response, 'name="stock_num"')
        self.assertEqual(
            {row["memo_num"] for row in response.context["memo_rows"]}, self.expected,
        )
        self.assertEqual(len(response.context["memo_rows"]), 4)

    def test_filter_combines_with_type_and_handles_empty_or_unknown_stock(self):
        for stock_num, memo_type, count in (
            (self.job.stock_num, "piecework", 2),
            (self.job.stock_num, "transfer", 2),
            ("missing-stock", "", 0),
            ("STK-910", "", 0),
            ("", "", 8),
        ):
            with self.subTest(stock_num=stock_num, memo_type=memo_type):
                response = self.client.get(reverse("culet:memo_list"), {
                    "stock_num": stock_num, "memo_type": memo_type,
                })
                self.assertEqual(len(response.context["memo_rows"]), count)
