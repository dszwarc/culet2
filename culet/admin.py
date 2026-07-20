from django.contrib import admin

from .models import (
    MovementType,
    JobMovement,
    Activity,
    ActivityStep,
    Customer,
    Department,
    Employee,
    FailureType,
    FindingStock,
    FindingType,
    Job,
    JobFinding,
    JobMetal,
    JobMetalLot,
    JobShip,
    JobStatus,
    JobStone,
    JobTransferMemo,
    JobTransferMemoLine,
    JobWeight,
    LegacyImportRun,
    LegacyRecordMap,
    Location,
    Metal,
    MetalLot,
    MetalPart,
    MetalReceipt,
    MetalReceiptLine,
    MetalType,
    MetalVendorLot,
    PieceworkMemo,
    PieceworkMemoLine,
    QualityInspection,
    QualityInspectionFailure,
    Role,
    Step,
    Stone,
    StoneShape,
    StoneType,
    Style,
    StyleFinding,
    StyleMetal,
    StyleStone,
    TimeClock,
    Vendor,
)


# -----------------------------------------------------------------------------
# General / people / setup
# -----------------------------------------------------------------------------


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
    search_fields = ("name",)
    list_filter = ("active",)
    ordering = ("name",)


@admin.register(JobStatus)
class JobStatusAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "active")
    search_fields = ("name",)
    list_filter = ("active",)
    ordering = ("sort_order", "name")


@admin.register(Step)
class StepAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "order", "active")
    search_fields = ("name", "code")
    list_filter = ("active",)
    ordering = ("order", "name")


@admin.register(ActivityStep)
class ActivityStepAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "department_list")
    search_fields = ("name", "code", "departments__name")
    list_filter = ("departments",)
    filter_horizontal = ("departments",)
    ordering = ("name",)

    @admin.display(description="Departments")
    def department_list(self, obj):
        return ", ".join(obj.departments.values_list("name", flat=True))


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
    search_fields = ("name",)
    list_filter = ("active",)
    ordering = ("name",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "level",
        "requires_clock_in",
        "can_start_activities",
        "can_receive_all_jobs",
        "active",
    )
    search_fields = ("name",)
    list_filter = (
        "active",
        "requires_clock_in",
        "can_start_activities",
        "can_receive_all_jobs",
    )
    ordering = ("level", "name")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "employee_name",
        "username",
        "department",
        "role",
        "can_qc",
        "clocked_in",
        "must_change_password",
    )
    search_fields = (
        "user__first_name",
        "user__last_name",
        "user__username",
        "user__email",
        "department__name",
        "role__name",
    )
    list_filter = (
        "department",
        "role",
        "can_qc",
        "clocked_in",
        "must_change_password",
    )
    autocomplete_fields = ("user", "department", "role")
    ordering = ("user__last_name", "user__first_name", "user__username")
    list_select_related = ("user", "department", "role")

    @admin.display(description="Employee", ordering="user__last_name")
    def employee_name(self, obj):
        full_name = obj.user.get_full_name().strip()
        return full_name or obj.user.username

    @admin.display(description="Username", ordering="user__username")
    def username(self, obj):
        return obj.user.username


@admin.register(TimeClock)
class TimeClockAdmin(admin.ModelAdmin):
    list_display = ("employee", "clock_in", "clock_out", "is_open")
    search_fields = (
        "employee__user__first_name",
        "employee__user__last_name",
        "employee__user__username",
    )
    list_filter = ("clock_in", "clock_out", "employee__department")
    autocomplete_fields = ("employee",)
    date_hierarchy = "clock_in"
    ordering = ("-clock_in",)
    list_select_related = ("employee__user", "employee__department")

    @admin.display(boolean=True, description="Open")
    def is_open(self, obj):
        return obj.clock_out is None


# -----------------------------------------------------------------------------
# Customers, vendors, styles, and catalog data
# -----------------------------------------------------------------------------


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "number", "email", "phone")
    search_fields = ("name", "=number", "email", "phone", "address")
    ordering = ("name",)


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "number", "email", "phone")
    search_fields = ("name", "=number", "email", "phone", "address")
    ordering = ("name",)


@admin.register(Style)
class StyleAdmin(admin.ModelAdmin):
    list_display = ("name", "customer", "product", "stamp", "has_photo", "has_spec_sheet")
    search_fields = (
        "name",
        "customer__name",
        "product",
        "stamp",
        "description",
    )
    list_filter = ("customer", "product")
    autocomplete_fields = ("customer",)
    ordering = ("name",)
    list_select_related = ("customer",)

    @admin.display(boolean=True, description="Photo")
    def has_photo(self, obj):
        return bool(obj.photo)

    @admin.display(boolean=True, description="Spec sheet")
    def has_spec_sheet(self, obj):
        return bool(obj.spec_sheet)


@admin.register(FailureType)
class FailureTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "active")
    search_fields = ("name",)
    list_filter = ("active",)
    ordering = ("sort_order", "name")


@admin.register(StoneType)
class StoneTypeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(StoneShape)
class StoneShapeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(MetalType)
class MetalTypeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(MetalPart)
class MetalPartAdmin(admin.ModelAdmin):
    list_display = ("sku", "customer", "short_description")
    search_fields = ("sku", "description", "customer__name")
    list_filter = ("customer",)
    autocomplete_fields = ("customer",)
    ordering = ("sku",)
    list_select_related = ("customer",)

    @admin.display(description="Description")
    def short_description(self, obj):
        if not obj.description:
            return ""
        return obj.description[:80]


@admin.register(FindingType)
class FindingTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "unit")
    search_fields = ("name", "unit")
    ordering = ("name",)


@admin.register(FindingStock)
class FindingStockAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "sku",
        "finding_type",
        "metal_type",
        "qty_on_hand",
        "active",
    )
    search_fields = (
        "name",
        "sku",
        "finding_type__name",
        "metal_type__name",
    )
    list_filter = ("active", "finding_type", "metal_type")
    autocomplete_fields = ("finding_type", "metal_type")
    ordering = ("name",)
    list_select_related = ("finding_type", "metal_type")


# -----------------------------------------------------------------------------
# Jobs, activities, shipping, QC, and weights
# -----------------------------------------------------------------------------


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "barcode",
        "stock_num",
        "style",
        "customer",
        "status",
        "holder",
        "assigned_to",
        "location",
        "due",
        "in_work",
        "shipped",
        "active",
        "is_piecework",
        "is_repair",
    )
    search_fields = (
        "=barcode",
        "stock_num",
        "name",
        "style__name",
        "customer__name",
        "notes",
        "stamp",
        "size",
        "holder__user__first_name",
        "holder__user__last_name",
        "assigned_to__user__first_name",
        "assigned_to__user__last_name",
        "location__name",
        "status__name",
    )
    list_filter = (
        "active",
        "shipped",
        "in_work",
        "is_piecework",
        "is_repair",
        "status",
        "customer",
        "location",
        "holder__department",
        "due",
        "created",
    )
    autocomplete_fields = (
        "customer",
        "style",
        "assigned_to",
        "holder",
        "location",
        "status",
        "repair_reasons",
        "repair_of",
    )
    date_hierarchy = "created"
    ordering = ("-created", "-id")
    list_per_page = 50
    list_select_related = (
        "style",
        "customer",
        "status",
        "holder__user",
        "assigned_to__user",
        "location",
    )
    readonly_fields = ("created", "last_updated")

@admin.register(MovementType)
class MovementTypeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
    )

    search_fields = (
        "name",
        "code",
    )
    prepopulated_fields = {
        "code": ("name",),
    }
    ordering = (
        "name",
    )
@admin.register(JobMovement)
class JobMovementAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "movement_type",
        "from_employee",
        "to_employee",
        "performed_by",
        "created_at",
    )
    list_filter = (
        "movement_type",
        "created_at",
    )
    search_fields = (
        "job__stock_num",
        "job__barcode",
        "movement_type__code"
        "movement_type__nane",
        "from_employee__user__first_name",
        "from_employee__user__last_name",
        "to_employee__user__first_name",
        "to_employee__user__last_name",
        "performed_by__user__first_name",
        "performed_by__user__last_name",
    )
    autocomplete_fields = (
        "job",
        "from_employee",
        "to_employee",
        "performed_by",
    )
    list_select_related = (
        "job",
        "movement_type",
        "from_employee__user",
        "to_employee__user",
        "performed_by__user",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "employee",
        "step",
        "start",
        "end",
        "duration",
        "active",
        "is_piecework",
    )
    search_fields = (
        "job__stock_num",
        "=job__barcode",
        "employee__user__first_name",
        "employee__user__last_name",
        "employee__user__username",
        "step__name",
        "step__code",
        "name",
    )
    list_filter = (
        "active",
        "is_piecework",
        "step",
        "employee__department",
        "start",
        "end",
    )
    autocomplete_fields = ("step", "employee", "job")
    date_hierarchy = "start"
    ordering = ("-start", "-id")
    list_per_page = 50
    list_select_related = ("job", "employee__user", "employee__department", "step")
    readonly_fields = ("duration",)


@admin.register(JobShip)
class JobShipAdmin(admin.ModelAdmin):
    list_display = ("job", "shipped_by", "shipped_at", "short_notes")
    search_fields = (
        "job__stock_num",
        "=job__barcode",
        "job__style__name",
        "job__customer__name",
        "shipped_by__user__first_name",
        "shipped_by__user__last_name",
        "notes",
    )
    list_filter = ("shipped_at", "shipped_by", "job__customer")
    autocomplete_fields = ("job", "shipped_by")
    date_hierarchy = "shipped_at"
    ordering = ("-shipped_at",)
    list_select_related = ("job__style", "job__customer", "shipped_by__user")

    @admin.display(description="Notes")
    def short_notes(self, obj):
        return obj.notes[:80] if obj.notes else ""


@admin.register(QualityInspection)
class QualityInspectionAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "inspected_by",
        "result",
        "inspection_duration_minutes",
        "inspected_at",
    )
    search_fields = (
        "job__stock_num",
        "=job__barcode",
        "job__style__name",
        "job__customer__name",
        "inspected_by__user__first_name",
        "inspected_by__user__last_name",
        "inspected_by__user__username",
        "notes",
        "failures__failure_type__name",
    )
    list_filter = (
        "result",
        "inspected_by",
        "inspected_by__department",
        "job__customer",
        "inspected_at",
    )
    autocomplete_fields = ("job", "inspected_by")
    date_hierarchy = "inspected_at"
    ordering = ("-inspected_at", "-id")
    list_per_page = 50
    list_select_related = ("job__style", "job__customer", "inspected_by__user")


@admin.register(QualityInspectionFailure)
class QualityInspectionFailureAdmin(admin.ModelAdmin):
    list_display = ("inspection", "failure_type", "notes")
    search_fields = (
        "inspection__job__stock_num",
        "=inspection__job__barcode",
        "inspection__inspected_by__user__first_name",
        "inspection__inspected_by__user__last_name",
        "failure_type__name",
        "notes",
    )
    list_filter = (
        "failure_type",
        "inspection__result",
        "inspection__inspected_at",
    )
    autocomplete_fields = ("inspection", "failure_type")
    ordering = ("-inspection__inspected_at", "failure_type__sort_order")
    list_select_related = (
        "inspection__job",
        "inspection__inspected_by__user",
        "failure_type",
    )


@admin.register(JobWeight)
class JobWeightAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "step",
        "weight",
        "sprue_weight",
        "dust_weight",
        "total_weight_display",
        "recorded_by",
        "created_at",
    )
    search_fields = (
        "job__stock_num",
        "=job__barcode",
        "job__style__name",
        "step__name",
        "step__code",
        "recorded_by__first_name",
        "recorded_by__last_name",
        "recorded_by__username",
    )
    list_filter = ("step", "created_at", "recorded_by")
    autocomplete_fields = ("job", "step", "recorded_by")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    list_select_related = ("job", "step", "recorded_by")

    @admin.display(description="Total weight")
    def total_weight_display(self, obj):
        return obj.total_weight


# -----------------------------------------------------------------------------
# Job requirements and style recipes
# -----------------------------------------------------------------------------


@admin.register(JobMetal)
class JobMetalAdmin(admin.ModelAdmin):
    list_display = ("job", "part", "metal_type", "qty_req", "weight_req")
    search_fields = (
        "job__stock_num",
        "=job__barcode",
        "job__style__name",
        "part__sku",
        "part__description",
        "metal_type__name",
    )
    list_filter = ("metal_type", "part__customer")
    autocomplete_fields = ("job", "part", "metal_type")
    ordering = ("-job__created", "job__barcode", "part__sku")
    list_select_related = ("job__style", "part__customer", "metal_type")


@admin.register(JobStone)
class JobStoneAdmin(admin.ModelAdmin):
    list_display = ("job", "stone_type", "stone_shape", "stone_size", "qty_req")
    search_fields = (
        "job__stock_num",
        "=job__barcode",
        "job__style__name",
        "stone_type__name",
        "stone_shape__name",
        "stone_size",
    )
    list_filter = ("stone_type", "stone_shape")
    autocomplete_fields = ("job", "stone_type", "stone_shape")
    ordering = ("-job__created", "job__barcode")
    list_select_related = ("job__style", "stone_type", "stone_shape")


@admin.register(JobFinding)
class JobFindingAdmin(admin.ModelAdmin):
    list_display = ("job", "finding", "qty_req", "qty_used")
    search_fields = (
        "job__stock_num",
        "=job__barcode",
        "job__style__name",
        "finding__name",
        "finding__sku",
        "finding__finding_type__name",
    )
    list_filter = ("finding__finding_type", "finding__metal_type")
    autocomplete_fields = ("job", "finding")
    ordering = ("-job__created", "job__barcode", "finding__name")
    list_select_related = ("job__style", "finding__finding_type", "finding__metal_type")


@admin.register(StyleMetal)
class StyleMetalAdmin(admin.ModelAdmin):
    list_display = ("style", "part", "metal_type", "qty_req", "weight")
    search_fields = (
        "style__name",
        "style__customer__name",
        "part__sku",
        "part__description",
        "metal_type__name",
    )
    list_filter = ("metal_type", "style__customer", "part__customer")
    autocomplete_fields = ("style", "part", "metal_type")
    ordering = ("style__name", "part__sku")
    list_select_related = ("style__customer", "part__customer", "metal_type")


@admin.register(StyleStone)
class StyleStoneAdmin(admin.ModelAdmin):
    list_display = ("style", "stone_type", "stone_shape", "stone_size", "qty_req")
    search_fields = (
        "style__name",
        "style__customer__name",
        "stone_type__name",
        "stone_shape__name",
        "stone_size",
    )
    list_filter = ("stone_type", "stone_shape", "style__customer")
    autocomplete_fields = ("style", "stone_type", "stone_shape")
    ordering = ("style__name", "stone_type__name", "stone_shape__name")
    list_select_related = ("style__customer", "stone_type", "stone_shape")


@admin.register(StyleFinding)
class StyleFindingAdmin(admin.ModelAdmin):
    list_display = ("style", "finding", "qty_req")
    search_fields = (
        "style__name",
        "style__customer__name",
        "finding__name",
        "finding__sku",
        "finding__finding_type__name",
    )
    list_filter = (
        "style__customer",
        "finding__finding_type",
        "finding__metal_type",
    )
    autocomplete_fields = ("style", "finding")
    ordering = ("style__name", "finding__name")
    list_select_related = (
        "style__customer",
        "finding__finding_type",
        "finding__metal_type",
    )


# -----------------------------------------------------------------------------
# Metal inventory and receiving
# -----------------------------------------------------------------------------


@admin.register(MetalVendorLot)
class MetalVendorLotAdmin(admin.ModelAdmin):
    list_display = ("lot_num", "vendor", "received_at")
    search_fields = ("lot_num", "vendor__name", "vendor__number")
    list_filter = ("vendor", "received_at")
    autocomplete_fields = ("vendor",)
    date_hierarchy = "received_at"
    ordering = ("-received_at", "lot_num")
    list_select_related = ("vendor",)


@admin.register(MetalLot)
class MetalLotAdmin(admin.ModelAdmin):
    list_display = (
        "vendor_lot",
        "part",
        "qty_on_hand",
        "weight_on_hand",
        "cost",
    )
    search_fields = (
        "vendor_lot__lot_num",
        "vendor_lot__vendor__name",
        "part__sku",
        "part__description",
        "part__customer__name",
    )
    list_filter = ("vendor_lot__vendor", "part__customer")
    autocomplete_fields = ("vendor_lot", "part")
    ordering = ("-vendor_lot__received_at", "vendor_lot__lot_num", "part__sku")
    list_select_related = ("vendor_lot__vendor", "part__customer")


@admin.register(JobMetalLot)
class JobMetalLotAdmin(admin.ModelAdmin):
    list_display = ("job_metal", "metal_lot", "qty_used", "weight_used")
    search_fields = (
        "job_metal__job__stock_num",
        "=job_metal__job__barcode",
        "job_metal__part__sku",
        "metal_lot__vendor_lot__lot_num",
        "metal_lot__vendor_lot__vendor__name",
        "metal_lot__part__sku",
    )
    list_filter = ("metal_lot__vendor_lot__vendor", "job_metal__metal_type")
    autocomplete_fields = ("job_metal", "metal_lot")
    ordering = ("-job_metal__job__created", "job_metal__job__barcode")
    list_select_related = (
        "job_metal__job",
        "job_metal__part",
        "metal_lot__vendor_lot__vendor",
        "metal_lot__part",
    )


@admin.register(MetalReceipt)
class MetalReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "vendor", "reference", "received_by", "received_at")
    search_fields = (
        "=id",
        "reference",
        "vendor__name",
        "vendor__number",
        "received_by__first_name",
        "received_by__last_name",
        "received_by__username",
        "notes",
    )
    list_filter = ("vendor", "received_by", "received_at")
    autocomplete_fields = ("received_by", "vendor")
    date_hierarchy = "received_at"
    ordering = ("-received_at", "-id")
    list_select_related = ("vendor", "received_by")
    readonly_fields = ("received_at",)


@admin.register(MetalReceiptLine)
class MetalReceiptLineAdmin(admin.ModelAdmin):
    list_display = (
        "receipt",
        "vendor_lot",
        "part",
        "qty_received",
        "weight_received",
        "cost",
        "metal_lot",
    )
    search_fields = (
        "=receipt__id",
        "receipt__reference",
        "receipt__vendor__name",
        "vendor_lot__lot_num",
        "part__sku",
        "part__description",
        "metal_lot__vendor_lot__lot_num",
    )
    list_filter = ("receipt__vendor", "receipt__received_at")
    autocomplete_fields = ("receipt", "vendor_lot", "part", "metal_lot")
    ordering = ("-receipt__received_at", "-receipt__id", "part__sku")
    list_select_related = (
        "receipt__vendor",
        "vendor_lot__vendor",
        "part",
        "metal_lot__vendor_lot",
    )


# -----------------------------------------------------------------------------
# Older/simple inventory models retained in the project
# -----------------------------------------------------------------------------


@admin.register(Metal)
class MetalAdmin(admin.ModelAdmin):
    list_display = ("lot_num", "job", "metal_type", "weight")
    search_fields = (
        "=lot_num",
        "job__stock_num",
        "=job__barcode",
        "metal_type__name",
        "weight",
    )
    list_filter = ("metal_type",)
    autocomplete_fields = ("job", "metal_type")
    ordering = ("-lot_num",)
    list_select_related = ("job", "metal_type")


@admin.register(Stone)
class StoneAdmin(admin.ModelAdmin):
    list_display = ("lot_num", "job", "stone_type", "size", "weight")
    search_fields = (
        "=lot_num",
        "job__stock_num",
        "=job__barcode",
        "stone_type__name",
        "size",
    )
    list_filter = ("stone_type",)
    autocomplete_fields = ("job", "stone_type")
    ordering = ("-lot_num",)
    list_select_related = ("job", "stone_type")


# -----------------------------------------------------------------------------
# Transfer and piecework memos
# -----------------------------------------------------------------------------


@admin.register(JobTransferMemo)
class JobTransferMemoAdmin(admin.ModelAdmin):
    list_display = (
        "memo_num",
        "created_at",
        "created_by",
        "assigned_to",
    )

    list_filter = (
        "created_at",
    )

    search_fields = (
        "memo_num",
        "created_by__user__first_name",
        "created_by__user__last_name",
        "assigned_to__user__first_name",
        "assigned_to__user__last_name",
        "notes",
    )

    autocomplete_fields = (
        "created_by",
        "assigned_to",
    )

    readonly_fields = (
        "memo_num",
        "created_at",
    )

    ordering = (
        "-created_at",
    )


@admin.register(PieceworkMemo)
class PieceworkMemoAdmin(admin.ModelAdmin):
    list_display = (
        "memo_num",
        "assigned_to",
        "created_by",
        "created_at",
        "due_back",
        "returned_at",
        "returned_by",
    )
    search_fields = (
        "memo_num",
        "assigned_to__user__first_name",
        "assigned_to__user__last_name",
        "created_by__user__first_name",
        "created_by__user__last_name",
        "returned_by__user__first_name",
        "returned_by__user__last_name",
        "from_location__name",
        "to_location__name",
        "notes",
        "lines__job__stock_num",
        "lines__job__barcode",
    )
    list_filter = (
        "assigned_to",
        "from_location",
        "to_location",
        "created_at",
        "due_back",
        "returned_at",
    )
    autocomplete_fields = (
        "created_by",
        "assigned_to",
        "from_location",
        "to_location",
        "returned_by",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    list_select_related = (
        "created_by__user",
        "assigned_to__user",
        "returned_by__user",
        "from_location",
        "to_location",
    )
    readonly_fields = ("memo_num", "created_at")


@admin.register(PieceworkMemoLine)
class PieceworkMemoLineAdmin(admin.ModelAdmin):
    list_display = ("memo", "job", "notes")
    search_fields = (
        "memo__memo_num",
        "job__stock_num",
        "=job__barcode",
        "job__style__name",
        "job__customer__name",
        "notes",
    )
    list_filter = ("memo__assigned_to", "memo__created_at", "memo__returned_at")
    autocomplete_fields = ("memo", "job")
    ordering = ("-memo__created_at", "job__barcode")
    list_select_related = ("memo__assigned_to__user", "job__style", "job__customer")


# -----------------------------------------------------------------------------
# Legacy import audit models
# -----------------------------------------------------------------------------


@admin.register(LegacyImportRun)
class LegacyImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "command",
        "status",
        "dry_run",
        "started_at",
        "finished_at",
        "git_commit",
    )
    search_fields = ("command", "git_commit", "error_message")
    list_filter = ("status", "dry_run", "started_at", "finished_at")
    date_hierarchy = "started_at"
    ordering = ("-started_at", "-id")
    readonly_fields = ("started_at",)


@admin.register(LegacyRecordMap)
class LegacyRecordMapAdmin(admin.ModelAdmin):
    list_display = (
        "legacy_table",
        "legacy_id",
        "content_type",
        "object_id",
        "action",
        "import_run",
        "imported_at",
    )
    search_fields = (
        "legacy_table",
        "=legacy_id",
        "content_type__app_label",
        "content_type__model",
        "=object_id",
        "action",
        "message",
        "import_run__command",
        "import_run__git_commit",
    )
    list_filter = (
        "action",
        "content_type",
        "legacy_table",
        "import_run__status",
        "imported_at",
    )
    autocomplete_fields = ("import_run",)
    raw_id_fields = ("content_type",)
    date_hierarchy = "imported_at"
    ordering = ("-imported_at", "legacy_table", "legacy_id")
    list_per_page = 100
    list_select_related = ("content_type", "import_run")
    readonly_fields = ("imported_at", "updated_at")


# Admin-wide display preferences.
admin.site.site_header = "Culet Administration"
admin.site.site_title = "Culet Admin"
admin.site.index_title = "Culet Data Management"