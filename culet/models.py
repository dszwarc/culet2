from django.db import models, transaction
from django.utils import timezone
from django.urls import reverse
from datetime import date, timedelta
from django.contrib.auth.models import User
from decimal import Decimal
from django.db.models import Max
# Create your models here.

class Location(models.Model):
    name = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name
    
class JobStatus(models.Model):
    name = models.CharField(max_length=80,unique=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

class Step(models.Model):
    name = models.CharField(max_length=100,unique=True)
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    code = models.SlugField(max_length=80, unique=True, blank=True, null=True)


    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return f"{self.name} "

class StoneType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self):
        return self.name
    
class StoneShape(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self):
        return self.name

class MetalType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self):
        return self.name

class Customer(models.Model):
    name = models.CharField(max_length=80,unique=True)
    address = models.CharField(max_length=150)
    email = models.EmailField(max_length=200)
    phone = models.CharField(max_length=12)
    number = models.IntegerField(blank=True, null=True)

    def __str__(self):
        return self.name

class Vendor(models.Model):
    name = models.CharField(max_length=80,unique=True)
    address = models.CharField(max_length=150)
    email = models.EmailField(max_length=200)
    phone = models.CharField(max_length=12)
    number = models.IntegerField(blank=True, null=True)

    def __str__(self):
        return self.name

class Department(models.Model):
    name = models.CharField(max_length=80, default="Production")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class Role(models.Model):
    name = models.CharField(max_length=80, unique=True, blank=True, null=True)
    requires_clock_in = models.BooleanField(default=True)
    can_start_activities = models.BooleanField(default=True)
    can_receive_all_jobs = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    level = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name

class Employee(models.Model):

    employee_choices = [
        ("PD", "Production"),
        ("MR", "Manager"),
        ("OF", "Office"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    active = models.BooleanField(default=True)
    department = models.ForeignKey(
    Department,
    on_delete=models.PROTECT,
    null=True,
    blank=True,
    related_name="employees"
    )

    role = models.ForeignKey(
    Role,
    on_delete=models.PROTECT,
    null=True,
    blank=True,
    related_name="employees"
    )
    can_qc = models.BooleanField(default=False)

    clocked_in = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=True)

    @property
    def active_activities(self):
        return self.activity_set.filter(active=True,end__isnull=True)
    
    @property
    def role_level(self):
        return self.role.level if self.role else 0
    
    @property
    def can_receive_all_jobs(self):
        return bool(self.role and (self.role.can_receive_all_jobs or self.role.level >= 30))

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name}"

class Style(models.Model):
    name = models.CharField(max_length=50, unique=True)

    customer = models.ForeignKey(
        Customer,
        blank=True,
        on_delete=models.PROTECT,
        null=True,
    )

    stamp = models.CharField(
        blank=True,
        max_length=80,
    )

    description = models.TextField(
        max_length=500,
        blank=True,
        null=True,
    )

    product = models.CharField(
        max_length=20,
        blank=True,
        null=True,
    )

    photo = models.ImageField(
        upload_to="styles/photos/",
        blank=True,
        null=True,
        help_text="Upload a photograph of the finished style.",
    )

    spec_sheet = models.FileField(
        upload_to="styles/spec-sheets/",
        blank=True,
        null=True,
        help_text="Upload the style specification sheet as a PDF.",
    )

    def __str__(self):
        return self.name

class FailureType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

class Job(models.Model):

    name = models.CharField(max_length=80,default="N/A")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True)
    barcode = models.IntegerField(blank=True,null=True, unique=True)
    stock_num = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
    )
    size = models.CharField(default="",blank=True,max_length=80)
    stamp = models.CharField(default="", blank=True, max_length=80)
    notes = models.TextField(default="", blank=True)
    quantity = models.PositiveIntegerField(default=1)

    active = models.BooleanField(default=True)
    shipped = models.BooleanField(default=False)
    in_work = models.BooleanField(default=False)
    style = models.ForeignKey(Style, on_delete=models.PROTECT)
    created = models.DateTimeField(default=timezone.now, editable = False)
    due = models.DateField()
    last_updated = models.DateTimeField(auto_now=True)
    assigned_to = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_assignments",
    )

    holder = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="held_jobs",
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )

    status = models.ForeignKey(
        JobStatus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )    
    is_piecework = models.BooleanField(default=False)
    is_repair = models.BooleanField(default=False)
    repair_reasons = models.ManyToManyField(
        FailureType,
        blank=True,
        related_name="repair_jobs",
    )
    repair_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="repairs",
    )
    piecework_assigned_at = models.DateTimeField(null=True, blank=True)

    def next_repair_stock_num(self):
        base_stock_num = self.stock_num

        existing_repairs = Job.objects.filter(
            repair_of=self
        ).count()

        next_number = existing_repairs + 1
        return f"{base_stock_num}-R{next_number}"

    @property
    def current_piecework_memo(self):
        line = (
            self.pieceworkmemoline_set
            .filter(memo__returned_at__isnull=True)
            .select_related("memo")
            .order_by("-memo__created_at")
            .first()
        )
        return line.memo if line else None

    @property
    def piecework_due_back(self):
        memo = self.current_piecework_memo
        return memo.due_back if memo else None

    def save(self,*args, **kwargs):
        if self.barcode is None:
            with transaction.atomic():
                latest_barcode = Job.objects.aggregate(Max("barcode"))["barcode__max"] or 0
                self.barcode = latest_barcode + 1
        super().save(*args,**kwargs)

    @property
    def active_activity(self):
        return self.activity_set.filter(active=True, end__isnull=True).first()

    @property
    def is_past_due(self):
        return date.today() > self.due

    @property
    def is_near_due(self):
        return date.today() > self.due - timedelta(days=30)

    @property
    def initial_job_weight(self):
        first_weight = self.weights.order_by("created_at", "id").first()
        return first_weight.total_weight if first_weight else None

    @property
    def latest_job_weight(self):
        latest_weight = self.weights.order_by("-created_at", "-id").first()
        return latest_weight.total_weight if latest_weight else None

    @property
    def weight_loss_percent(self):
        initial = self.initial_job_weight
        latest = self.latest_job_weight
        
        if initial in (None, Decimal("0")) or latest is None:
            return None
        
        return ((initial - latest)/initial) * Decimal("100")

    def __str__(self):
        return str(self.barcode).zfill(5)
    
    def get_absolute_url(self):
        return reverse('culet:job_detail', kwargs={'pk': self.pk})
    
class JobShip(models.Model):
    job = models.OneToOneField(
        Job,
        on_delete=models.CASCADE,
        related_name="shipment",
    )
    shipped_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="job_shipments",
    )
    shipped_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-shipped_at"]

    def __str__(self):
        return f"{self.job} shipped at {self.shipped_at}"
    



class QualityInspection(models.Model):
    RESULT_PASS = "pass"
    RESULT_FAIL = "fail"

    RESULT_CHOICES = [
        (RESULT_PASS, "Passed"),
        (RESULT_FAIL, "Failed"),
    ]

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name="quality_inspections",
    )
    inspected_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="quality_inspections",
    )
    inspected_at = models.DateTimeField(default=timezone.now)
    inspection_duration_minutes = models.PositiveIntegerField(null=True,blank=True,verbose_name="Inspection duration",help_text="Approximate inspection time in minutes.")
    result = models.CharField(max_length=10, choices=RESULT_CHOICES)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-inspected_at"]

    def __str__(self):
        return f"{self.job} - {self.get_result_display()} - {self.inspected_at:%Y-%m-%d}"


class QualityInspectionFailure(models.Model):
    inspection = models.ForeignKey(
        QualityInspection,
        on_delete=models.CASCADE,
        related_name="failures",
    )
    failure_type = models.ForeignKey(
        FailureType,
        on_delete=models.PROTECT,
        related_name="inspection_failures",
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["failure_type__sort_order", "failure_type__name"]

    def __str__(self):
        return f"{self.inspection.job} - {self.failure_type}"

class JobWeight(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="weights")
    weight = models.DecimalField(max_digits=10, decimal_places=3, default=0, verbose_name="Piece Weight")
    sprue_weight = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    dust_weight = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    recorded_by = models.ForeignKey(User,on_delete=models.PROTECT, null=True, blank=True)
    step = models.ForeignKey(Step, on_delete=models.PROTECT,related_name="job_weights", null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    @property
    def total_weight(self):
        return (self.weight or Decimal("0")) + (self.sprue_weight or Decimal("0")) + (self.dust_weight or Decimal("0"))
    
    def __str__(self):
        return f"Weight of {self.job} @ {self.created_at:%Y-%m-%d %H:%M}"

class Metal(models.Model):
    lot_num = models.IntegerField(unique=True, blank=True)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, null=True, blank=True)
    metal_type = models.ForeignKey(MetalType, on_delete=models.PROTECT, null=True, blank=True)
    weight = models.CharField(max_length=10)


class MetalPart(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    description = models.TextField(max_length=200, blank=True, null=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True, blank=True)
    def __str__(self):
        return self.sku

class JobMetal(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="job_metals")
    part = models.ForeignKey(MetalPart, on_delete=models.PROTECT)
    qty_req = models.PositiveIntegerField(null=True, blank=True)
    weight_req = models.DecimalField(decimal_places=2, max_digits=10,null=True, blank=True)
    metal_type = models.ForeignKey(MetalType, on_delete=models.PROTECT, null=True, blank=True)

    def __str__(self):
        return f"{self.job} - {self.part}"

class MetalVendorLot(models.Model):
    lot_num = models.CharField(max_length=50, unique=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT)
    received_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.lot_num

class MetalLot(models.Model):
    vendor_lot = models.ForeignKey(
        MetalVendorLot,
        on_delete=models.PROTECT,
        related_name="part_lots"
    )
    part = models.ForeignKey(MetalPart, on_delete=models.PROTECT)
    qty_on_hand = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    weight_on_hand = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    cost = models.DecimalField(max_digits=15, decimal_places=2,default=0)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vendor_lot", "part"],
                name="uniq_part_per_vendor_lot"
            )
        ]

    def __str__(self):
        return f"{self.vendor_lot.lot_num} - {self.part}"
    
class JobMetalLot(models.Model):
    job_metal = models.ForeignKey(JobMetal, on_delete=models.CASCADE, related_name="lot_assignments")
    metal_lot = models.ForeignKey(MetalLot, on_delete=models.PROTECT)
    qty_used = models.PositiveIntegerField(default=0)
    weight_used = models.DecimalField(max_digits=10, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.job_metal} -> {self.metal_lot}"
    
class JobStone(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="job_stones")
    stone_type = models.ForeignKey(StoneType, on_delete=models.PROTECT, null=True, blank=True)
    stone_shape = models.ForeignKey(StoneShape, on_delete=models.PROTECT, null=True, blank=True)
    stone_size = models.CharField(max_length=10, blank=True, null=True)
    qty_req = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.job} - {self.stone_type}"
    
class MetalReceipt(models.Model):
    received_at = models.DateTimeField(default=timezone.now, editable=False)
    received_by = models.ForeignKey(User, on_delete=models.PROTECT)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Receipt #{self.pk} - {self.received_at:%Y-%m-%d}"
    
class MetalReceiptLine(models.Model):
    receipt = models.ForeignKey(
        MetalReceipt,
        on_delete=models.CASCADE,
        related_name="lines"
    )
    vendor_lot = models.ForeignKey(
        MetalVendorLot,
        on_delete=models.PROTECT,
        related_name="receipt_lines"
    )
    part = models.ForeignKey(MetalPart, on_delete=models.PROTECT)

    qty_received = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    weight_received = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True
    )
    cost = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0
    )

    metal_lot = models.ForeignKey(
        MetalLot,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="receipt_lines"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["receipt", "vendor_lot", "part"],
                name="uniq_receipt_vendorlot_part"
            )
        ]

    def __str__(self):
        return f"{self.vendor_lot.lot_num} / {self.part} x {self.qty_received}"

class StyleMetal(models.Model):
    style = models.ForeignKey(Style, on_delete=models.CASCADE)
    qty_req = models.PositiveIntegerField(null=True, blank=True)
    weight = models.DecimalField(decimal_places=2, max_digits=10,null=True, blank=True)
    part = models.ForeignKey(
        MetalPart,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    metal_type = models.ForeignKey(
        MetalType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.style} - {self.part}"

class Stone(models.Model):
    lot_num = models.IntegerField(unique=True)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, null=True)
    stone_type = models.ForeignKey(StoneType, on_delete=models.PROTECT, null=True)
    size = models.CharField(blank=True, default="")
    weight = models.DecimalField(decimal_places=2, max_digits=10,blank=True, default=1)

class StyleStone(models.Model):
    style = models.ForeignKey(Style, on_delete=models.CASCADE)
    stone_type = models.ForeignKey(StoneType, on_delete=models.PROTECT, null=True)
    stone_shape = models.ForeignKey(StoneShape, on_delete=models.PROTECT, null=True)
    stone_size = models.CharField(max_length=10, blank=True, null=True)
    qty_req = models.PositiveIntegerField()
    def __str__(self):
        return f"{self.stone_size} {self.stone_shape} {self.stone_type} - {self.style}"

class FindingType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    unit = models.CharField(max_length=10, default="pcs")

    def __str__(self):
        return self.name

class FindingStock(models.Model):
    finding_type = models.ForeignKey(FindingType, on_delete=models.PROTECT)
    name = models.CharField(max_length=80, unique=True)
    sku = models.CharField(max_length=50, blank=True)
    metal_type = models.ForeignKey(MetalType, on_delete=models.PROTECT, null=True, blank=True)
    qty_on_hand = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name
    
class StyleFinding(models.Model):
    style = models.ForeignKey(Style, on_delete=models.CASCADE, related_name="style_findings")
    finding = models.ForeignKey(FindingStock, on_delete=models.PROTECT)
    qty_req = models.DecimalField(max_digits=10, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.style} - {self.finding}"


class JobFinding(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="job_findings")
    finding = models.ForeignKey(FindingStock, on_delete=models.PROTECT)
    qty_req = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    qty_used = models.DecimalField(max_digits=10, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.job} - {self.finding}"
    
class ActivityStep(models.Model):
    name = models.CharField(max_length=80, unique=True)
    departments = models.ManyToManyField(
        Department,
        blank=True,
        related_name="activity_steps"
    )
    code = models.SlugField(max_length=80, unique=True, blank=True, null=True)

    def __str__(self):
        return f"{self.name}"

class Activity(models.Model):
    name = models.CharField(max_length=80)
    step = models.ForeignKey(ActivityStep, on_delete=models.PROTECT, null=True, blank=True, related_name="activities")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    start = models.DateTimeField(default=timezone.now)
    end = models.DateTimeField(blank=True, null=True)
    is_piecework = models.BooleanField(default=False)
    duration = models.DurationField(null=True, blank=True)
    job = models.ForeignKey(Job, blank=True, null=True, on_delete=models.CASCADE)
    active = models.BooleanField(default=True)
    @property
    def duration_decimal_hours(self):
        if self.duration:
            return self.duration.total_seconds() / 3600
        return 0

    @property
    def duration_hours(self):
        if self.duration:
            return int(self.duration.total_seconds() // 3600)
        return 0

    @property
    def duration_min(self):
        if self.duration:
            return int((self.duration.total_seconds() % 3600) // 60)
        return 0
    

    def save(self, *args, **kwargs):
        if self.step and not self.name:
            self.name = self.step.name

        if self.start and self.end:
            self.duration = self.end - self.start
        else:
            self.duration = None

        super().save(*args, **kwargs)

    def __str__(self):
        step_name = self.step.name if self.step else self.name
        return f"{self.job.barcode} {step_name}"
    
class MovementType(models.Model):
    class JobField(models.TextChoices):
        ASSIGNED_TO = "assigned_to", "Assigned To"
        HOLDER = "holder", "Holder"

    name = models.CharField(
        max_length=80,
        unique=True,
    )

    code = models.SlugField(
        max_length=50,
        unique=True,
    )

    job_field = models.CharField(
        max_length=20,
        choices=JobField.choices,
    )

    def __str__(self):
        return self.name
    
class JobMovement(models.Model):
    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name="movements",
    )

    from_employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        related_name="job_movements_from",
        null=True,
        blank=True,
    )

    to_employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        related_name="job_movements_to",
        null=True,
        blank=True,
    )

    performed_by = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        related_name="job_movements_performed",
        null=True,
        blank=True,
    )

    movement_type = models.ForeignKey(
        MovementType,
        on_delete=models.PROTECT,
        related_name="job_movements",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.job} — {self.movement_type}"

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def round_to_quarter_hour(value):
    """
    Round a datetime to the nearest quarter hour.

    Remainder:
    0 minutes: unchanged
    1–7 minutes: round down
    8–14 minutes: round up

    Examples:
    8:07 -> 8:00
    8:08 -> 8:15
    4:52 -> 4:45
    4:53 -> 5:00
    """
    if value is None:
        return None

    value = value.replace(second=0, microsecond=0)

    minute_remainder = value.minute % 15

    if minute_remainder == 0:
        return value

    if minute_remainder <= 7:
        return value - timedelta(minutes=minute_remainder)

    return value + timedelta(minutes=(15 - minute_remainder))


from datetime import timedelta

from django.db import models
from django.utils import timezone


class TimeClock(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="timeclocks",
    )

    clock_in = models.DateTimeField()

    clock_out = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = [
            "employee__user__last_name",
            "employee__user__first_name",
            "clock_in",
        ]

    def __str__(self):
        return (
            f"{self.employee} "
            f"{self.clock_in:%Y-%m-%d %H:%M}"
        )

    @staticmethod
    def round_to_quarter(dt):
        """
        Quarter-hour rounding.

        0 minutes -> same
        1-7  -> down
        8-14 -> up
        """

        if dt is None:
            return None

        dt = dt.replace(
            second=0,
            microsecond=0,
        )

        remainder = dt.minute % 15

        if remainder == 0:
            return dt

        if remainder <= 7:
            return dt - timedelta(
                minutes=remainder,
            )

        return dt + timedelta(
            minutes=15 - remainder,
        )

    @property
    def rounded_clock_in(self):
        return self.round_to_quarter(
            self.clock_in,
        )

    @property
    def rounded_clock_out(self):
        if self.clock_out is None:
            return None

        return self.round_to_quarter(
            self.clock_out,
        )

    @property
    def raw_duration(self):
        if self.clock_out is None:
            return timezone.now() - self.clock_in

        return self.clock_out - self.clock_in

    @property
    def rounded_duration(self):
        if self.rounded_clock_out is None:
            return None

        return (
            self.rounded_clock_out
            - self.rounded_clock_in
        )

    @property
    def raw_hours(self):
        return (
            self.raw_duration.total_seconds()
            / 3600
        )

    @property
    def rounded_hours(self):
        if self.rounded_duration is None:
            return 0

        return (
            self.rounded_duration.total_seconds()
            / 3600
        )
    
class JobTransferMemo(models.Model):
    memo_num = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        editable=False,
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    created_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="created_transfer_memos",
    )

    assigned_to = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="assigned_transfer_memos",
        null=True,
    )

    notes = models.TextField(
        blank=True,
    )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if not self.memo_num:
            self.memo_num = f"TM-{self.pk:06d}"

            super().save(
                update_fields=["memo_num"],
            )

    def __str__(self):
        return self.memo_num or f"Transfer Memo #{self.pk}"


class JobTransferMemoLine(models.Model):
    memo = models.ForeignKey(
        JobTransferMemo,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    job = models.ForeignKey(
        Job,
        on_delete=models.PROTECT,
        related_name="transfer_memo_lines",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "memo",
                    "job",
                ],
                name="unique_job_per_transfer_memo",
            ),
        ]

    def __str__(self):
        return f"{self.memo} - {self.job}"

    
class PieceworkMemo(models.Model):
    memo_num = models.CharField(max_length=20, unique=True, blank=True, editable=False)
    created_by = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="piecework_memos_created")
    assigned_to = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="piecework_memos_assigned")
    created_at = models.DateTimeField(default=timezone.now)

    from_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    to_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")

    due_back = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    returned_at = models.DateTimeField(null=True, blank=True)
    returned_by = models.ForeignKey(Employee, on_delete=models.PROTECT, null=True, blank=True, related_name="piecework_memos_returned")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if not self.memo_num:
            self.memo_num = f"PW-{self.pk:06d}"
            super().save(update_fields=["memo_num"])

    def __str__(self):
        return self.memo_num or f"Piecework Memo {self.pk}"

class PieceworkMemoLine(models.Model):
    memo = models.ForeignKey(PieceworkMemo, on_delete=models.CASCADE, related_name="lines")
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    notes = models.CharField(max_length=255, blank=True)




#MAPPING MODELS FOR OLD DATABASE

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class LegacyImportRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    command = models.CharField(max_length=100)
    dry_run = models.BooleanField(default=False)

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
    )

    git_commit = models.CharField(max_length=40, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return (
            f"{self.command} — {self.started_at:%Y-%m-%d %H:%M} "
            f"({self.get_status_display()})"
        )


class LegacyRecordMap(models.Model):
    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"
    ACTION_UNCHANGED = "unchanged"
    ACTION_SKIPPED = "skipped"

    ACTION_CHOICES = [
        (ACTION_CREATED, "Created"),
        (ACTION_UPDATED, "Updated"),
        (ACTION_UNCHANGED, "Unchanged"),
        (ACTION_SKIPPED, "Skipped"),
    ]

    import_run = models.ForeignKey(
        LegacyImportRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="record_maps",
    )

    legacy_table = models.CharField(max_length=100)
    legacy_id = models.BigIntegerField()

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    object_id = models.PositiveBigIntegerField(null=True, blank=True)

    content_object = GenericForeignKey(
        "content_type",
        "object_id",
    )

    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
    )

    message = models.TextField(blank=True)
    imported_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "legacy_table",
                    "legacy_id",
                    "content_type",
                ],
                name="uniq_legacy_record_target_type",
            ),
        ]

        indexes = [
            models.Index(
                fields=["legacy_table", "legacy_id"],
                name="legacy_table_id_idx",
            ),
            models.Index(
                fields=["content_type", "object_id"],
                name="legacy_target_idx",
            ),
        ]

        ordering = [
            "legacy_table",
            "legacy_id",
            "content_type_id",
        ]

    def __str__(self):
        target = (
            f"{self.content_type} #{self.object_id}"
            if self.content_type_id and self.object_id
            else "No target"
        )

        return (
            f"{self.legacy_table} #{self.legacy_id} "
            f"→ {target} ({self.action})"
        )