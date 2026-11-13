from decimal import Decimal
from django.db import models
from django.db.models import F, Q
from django.core.exceptions import ValidationError
from django.utils import timezone


def format_vnd(amount):
    if amount is None:
        return ""
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    is_negative = amount < 0
    abs_amount = abs(amount)

    s = f"{abs_amount:f}"
    if "." in s:
        int_part_str, frac_part_str = s.split(".", 1)
        frac_part_str = frac_part_str.rstrip("0")
    else:
        int_part_str = s
        frac_part_str = ""

    int_val = int(int_part_str)
    formatted_int = f"{int_val:,}".replace(",", ".")

    sign = "-" if is_negative else ""
    if frac_part_str:
        return f"{sign}{formatted_int},{frac_part_str} đ"
    return f"{sign}{formatted_int} đ"


def _is_finite_decimal(value):
    return isinstance(value, Decimal) and value.is_finite()


class DiscountType(models.TextChoices):
    PERCENT = 'PERCENT', 'Phần trăm (%)'
    FIXED = 'FIXED', 'Số tiền cố định (VND)'


class Coupon(models.Model):
    code = models.CharField(
        max_length=40,
        unique=True,
        verbose_name="Mã Voucher Code",
        help_text="Tối đa 40 ký tự. Bắt buộc nhập, duy nhất, chuẩn hóa chữ hoa và cắt khoảng trắng viền."
    )
    discount_type = models.CharField(
        max_length=10,
        choices=DiscountType.choices,
        verbose_name="Loại hình giảm giá",
        help_text="PERCENT (Phần trăm) hoặc FIXED (Số tiền cố định)."
    )
    value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="Mức giá trị giảm",
        help_text="PERCENT: > 0 và <= 100; FIXED: > 0 (VND)."
    )
    max_discount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Trần giảm tối đa (VND)",
        help_text="Chỉ áp dụng khi giảm theo %, để trống (NULL) nếu không giới hạn trần. Giá trị >= 0."
    )
    min_order_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Giá trị đơn hàng tối thiểu (VND)",
        help_text="Mặc định 0 đ. Giá trị >= 0."
    )
    max_uses = models.PositiveIntegerField(
        verbose_name="Tổng số lượt phát hành",
        help_text="Tổng số lượt sử dụng tối đa của mã. Bắt buộc > 0."
    )
    used_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Số lượt đã dùng",
        help_text="Server-managed: Tổng số lượt đã tiêu thụ thành công. Khởi tạo 0, chỉ đọc."
    )
    reserved_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Số lượt đang tạm giữ",
        help_text="Server-managed: Số lượt đang tạm giữ cho đơn hàng chờ thanh toán. Khởi tạo 0, chỉ đọc."
    )
    starts_at = models.DateTimeField(
        verbose_name="Thời điểm bắt đầu",
        help_text="Thời điểm bắt đầu có hiệu lực (aware datetime)."
    )
    ends_at = models.DateTimeField(
        verbose_name="Thời điểm kết thúc",
        help_text="Thời điểm hết hiệu lực (aware datetime). Bắt buộc sau thời điểm bắt đầu."
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Kích hoạt hoạt động",
        help_text="Mặc định True. Nếu False, voucher bị tạm ngưng áp dụng."
    )
    applies_to_all = models.BooleanField(
        default=True,
        verbose_name="Áp dụng toàn bộ sản phẩm",
        help_text="Mặc định True: áp dụng cho tất cả sản phẩm. False: chỉ áp dụng cho sản phẩm trong danh sách CouponProduct."
    )

    class Meta:
        db_table = 'coupons_coupon'
        verbose_name = 'Mã giảm giá (Coupon)'
        verbose_name_plural = 'Danh sách mã giảm giá (Coupons)'
        ordering = ['-starts_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=Q(discount_type__in=['PERCENT', 'FIXED']),
                name='check_coupon_discount_type_valid'
            ),
            models.CheckConstraint(
                condition=(
                    (Q(discount_type='PERCENT') & Q(value__gt=0) & Q(value__lte=100)) |
                    (Q(discount_type='FIXED') & Q(value__gt=0))
                ),
                name='check_coupon_value_valid'
            ),
            models.CheckConstraint(
                condition=Q(max_discount__isnull=True) | Q(max_discount__gte=0),
                name='check_coupon_max_discount_nonnegative'
            ),
            models.CheckConstraint(
                condition=Q(min_order_amount__gte=0),
                name='check_coupon_min_order_amount_nonnegative'
            ),
            models.CheckConstraint(
                condition=Q(max_uses__gt=0),
                name='check_coupon_max_uses_positive'
            ),
            models.CheckConstraint(
                condition=Q(used_count__gte=0) & Q(reserved_count__gte=0) & Q(used_count__lte=F('max_uses') - F('reserved_count')),
                name='check_coupon_counter_quota'
            ),
            models.CheckConstraint(
                condition=Q(ends_at__gt=F('starts_at')),
                name='check_coupon_time_ordering'
            ),
            models.UniqueConstraint(
                fields=['code'],
                name='unique_coupon_code'
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.get_discount_type_display()} - {self.value_display})"

    @property
    def value_display(self):
        if self.discount_type == DiscountType.PERCENT:
            val_str = f"{self.value:f}".rstrip('0').rstrip('.') if '.' in f"{self.value:f}" else str(self.value)
            return f"{val_str}%"
        return format_vnd(self.value)

    @property
    def max_discount_display(self):
        if self.max_discount is None:
            return ""
        return format_vnd(self.max_discount)

    @property
    def min_order_amount_display(self):
        if self.min_order_amount is None or self.min_order_amount == 0:
            return "Không yêu cầu"
        return format_vnd(self.min_order_amount)

    @property
    def remaining_uses(self):
        remaining = self.max_uses - (self.used_count or 0) - (self.reserved_count or 0)
        return max(0, remaining)

    @property
    def is_exhausted(self):
        return self.remaining_uses <= 0

    def get_derived_status(self, now=None):
        if now is None:
            now = timezone.now()

        if not self.is_active:
            return 'INACTIVE'
        if now < self.starts_at:
            return 'NOT_STARTED'
        if now >= self.ends_at:
            return 'EXPIRED'
        if self.is_exhausted:
            return 'EXHAUSTED'
        return 'ACTIVE'

    @property
    def derived_status(self):
        return self.get_derived_status()

    @property
    def derived_status_display(self):
        mapping = {
            'INACTIVE': 'Tạm ngưng',
            'NOT_STARTED': 'Chưa bắt đầu',
            'EXPIRED': 'Hết hạn',
            'EXHAUSTED': 'Đã hết lượt',
            'ACTIVE': 'Đang diễn ra',
        }
        return mapping.get(self.derived_status, 'Không xác định')

    @property
    def derived_status_badge_class(self):
        mapping = {
            'INACTIVE': 'badge-secondary',
            'NOT_STARTED': 'badge-info',
            'EXPIRED': 'badge-gray',
            'EXHAUSTED': 'badge-danger',
            'ACTIVE': 'badge-success',
        }
        return mapping.get(self.derived_status, 'badge-secondary')

    def clean(self):
        super().clean()
        errors = {}

        if self.code:
            self.code = self.code.strip().upper()
        if not self.code:
            errors['code'] = "Mã voucher không được để trống hoặc chỉ chứa khoảng trắng."
        elif len(self.code) > 40:
            errors['code'] = "Mã voucher không được vượt quá 40 ký tự."
        else:
            qs = Coupon.objects.filter(code=self.code)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                errors['code'] = f"Mã voucher '{self.code}' đã tồn tại trong hệ thống."

        if self.discount_type not in (DiscountType.PERCENT, DiscountType.FIXED):
            errors['discount_type'] = "Loại hình giảm giá không hợp lệ (chỉ chấp nhận PERCENT hoặc FIXED)."
        elif self.value is not None:
            if self.discount_type == DiscountType.PERCENT:
                if self.value <= 0 or self.value > 100:
                    errors['value'] = "Giá trị giảm theo phần trăm phải lớn hơn 0% và không vượt quá 100%."
            elif self.discount_type == DiscountType.FIXED:
                if self.value <= 0:
                    errors['value'] = "Giá trị giảm số tiền cố định phải lớn hơn 0 đ."

        if self.max_discount is not None and self.max_discount < 0:
            errors['max_discount'] = "Trần giảm tối đa không được âm (>= 0 đ hoặc để trống nếu không giới hạn)."

        if self.min_order_amount is not None and self.min_order_amount < 0:
            errors['min_order_amount'] = "Giá trị đơn hàng tối thiểu không được âm (>= 0 đ)."

        if self.max_uses is not None:
            if self.max_uses <= 0:
                errors['max_uses'] = "Tổng số lượt phát hành phải lớn hơn 0."
            elif self.pk:
                current_total = (self.used_count or 0) + (self.reserved_count or 0)
                if self.max_uses < current_total:
                    errors['max_uses'] = (
                        f"Tổng lượt phát hành ({self.max_uses}) không thể nhỏ hơn tổng số lượt đã dùng và đang giữ ({current_total})."
                    )

        if self.used_count is not None and self.used_count < 0:
            errors['used_count'] = "Số lượt đã dùng không được âm."
        if self.reserved_count is not None and self.reserved_count < 0:
            errors['reserved_count'] = "Số lượt đang giữ không được âm."

        if (
            self.max_uses is not None and
            self.used_count is not None and
            self.reserved_count is not None
        ):
            if self.used_count + self.reserved_count > self.max_uses:
                errors['max_uses'] = "Tổng lượt đã dùng và đang giữ vượt quá tổng số lượt phát hành."

        if self.starts_at and self.ends_at:
            if timezone.is_naive(self.starts_at) or timezone.is_naive(self.ends_at):
                errors['ends_at'] = "Thời điểm bắt đầu và kết thúc phải là aware datetime (có múi giờ)."
            elif self.ends_at <= self.starts_at:
                errors['ends_at'] = "Thời điểm kết thúc phải sau thời điểm bắt đầu."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)


class CouponProduct(models.Model):
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.CASCADE,
        related_name='coupon_products',
        verbose_name="Mã giảm giá"
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.PROTECT,
        related_name='coupon_products',
        verbose_name="Sản phẩm áp dụng"
    )

    class Meta:
        db_table = 'coupons_coupon_product'
        verbose_name = 'Sản phẩm áp dụng mã giảm giá'
        verbose_name_plural = 'Các sản phẩm áp dụng mã giảm giá'
        constraints = [
            models.UniqueConstraint(
                fields=['coupon', 'product'],
                name='unique_coupon_product'
            )
        ]

    def __str__(self):
        return f"{self.coupon.code} → {self.product.name}"


class CouponUsageStatus(models.TextChoices):
    RESERVED = 'RESERVED', 'Giữ chỗ'
    USED = 'USED', 'Đã sử dụng'
    RELEASED = 'RELEASED', 'Đã giải phóng'


class CouponUsage(models.Model):
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.PROTECT,
        related_name='usages',
        verbose_name="Mã giảm giá",
        help_text="Mã voucher được sử dụng. on_delete=PROTECT bảo toàn lịch sử áp dụng voucher."
    )
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='coupon_usage',
        verbose_name="Đơn hàng áp dụng",
        help_text="Đơn hàng áp dụng mã voucher. Ràng buộc OneToOne: mỗi đơn hàng chỉ được áp dụng tối đa 01 voucher."
    )
    discount_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="Mức giảm giá snapshot (VND)",
        help_text="Số tiền giảm giá thực tế snapshot tại thời điểm áp dụng (>= 0 đ). Tuyệt đối không tính toán lại từ Coupon khi đọc hoặc lưu."
    )
    status = models.CharField(
        max_length=12,
        choices=CouponUsageStatus.choices,
        verbose_name="Trạng thái sử dụng",
        help_text="RESERVED (giữ chỗ QR), USED (đã áp dụng thành công), RELEASED (đã giải phóng do hủy/hết hạn). Không có giá trị mặc định để tránh đồng nhất QR và COD."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Thời điểm tạo",
        help_text="Thời điểm ghi nhận lượt sử dụng/giữ chỗ voucher."
    )
    finalized_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Thời điểm hoàn tất/giải phóng",
        help_text="Thời điểm hoàn tất sử dụng (USED) hoặc giải phóng (RELEASED). Bắt buộc là NULL khi còn RESERVED."
    )

    class Meta:
        db_table = 'coupons_coupon_usage'
        verbose_name = 'Lượt sử dụng mã giảm giá (CouponUsage)'
        verbose_name_plural = 'Danh sách lượt sử dụng mã giảm giá (CouponUsages)'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=Q(discount_amount__gte=0),
                name='coupon_usage_discount_amount_gte_0'
            ),
            models.CheckConstraint(
                condition=Q(status__in=CouponUsageStatus.values),
                name='coupon_usage_status_valid'
            ),
        ]

    def __str__(self):
        coupon_code = self.coupon.code if self.coupon_id else "None"
        order_code = self.order.order_code if self.order_id else "None"
        return f"CouponUsage #{self.pk or 'new'} ({coupon_code} -> {order_code}: {self.get_status_display()})"

    def clean(self):
        super().clean()
        errors = {}

        if self.discount_amount is None:
            errors['discount_amount'] = "Số tiền giảm giá snapshot (discount_amount) không được để trống."
        elif not _is_finite_decimal(self.discount_amount):
            errors['discount_amount'] = "Số tiền giảm giá snapshot (discount_amount) phải là số thập phân hữu hạn."
        elif self.discount_amount < Decimal('0.00'):
            errors['discount_amount'] = "Số tiền giảm giá snapshot (discount_amount) không được âm (>= 0 đ)."

        if not self.status:
            errors['status'] = "Trạng thái sử dụng (status) không được để trống."
        elif self.status not in CouponUsageStatus.values:
            errors['status'] = f"Trạng thái '{self.status}' không hợp lệ (chỉ chấp nhận RESERVED, USED, RELEASED)."

        if self.status == CouponUsageStatus.RESERVED and self.finalized_at is not None:
            errors['finalized_at'] = "Thời điểm hoàn tất (finalized_at) phải là NULL khi trạng thái còn đang giữ chỗ (RESERVED)."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

