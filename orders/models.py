import re
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def validate_delivery_phone(phone_raw):
    if not phone_raw:
        raise ValidationError('Số điện thoại người nhận không được để trống.')
    raw_str = str(phone_raw).strip()
    if len(raw_str) > 15:
        raise ValidationError('Số điện thoại người nhận không được vượt quá 15 ký tự.')
    cleaned = re.sub(r'[\s.\-]', '', raw_str)
    if cleaned.startswith('+84'):
        cleaned = '0' + cleaned[3:]
    if not re.match(r'^0[35789]\d{8}$', cleaned):
        raise ValidationError(
            'Số điện thoại người nhận không hợp lệ. Vui lòng nhập số điện thoại Việt Nam gồm 10 chữ số (ví dụ: 0982805704).'
        )
    return cleaned


def _is_finite_decimal(value):
    return isinstance(value, Decimal) and value.is_finite()


def _is_valid_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


class OrderStatus(models.TextChoices):
    PENDING_PAYMENT = 'PENDING_PAYMENT', 'Chờ thanh toán'
    PENDING_RECONCILIATION = 'PENDING_RECONCILIATION', 'Chờ đối soát'
    PENDING_CONFIRMATION = 'PENDING_CONFIRMATION', 'Chờ xác nhận'
    CONFIRMED = 'CONFIRMED', 'Đã xác nhận'
    SHIPPING = 'SHIPPING', 'Đang giao hàng'
    DELIVERED = 'DELIVERED', 'Giao hàng thành công'
    CANCELLED = 'CANCELLED', 'Đã hủy'
    EXPIRED = 'EXPIRED', 'Hết hạn thanh toán'
    DELIVERY_FAILED = 'DELIVERY_FAILED', 'Giao hàng thất bại'
    RETURNING_TO_WAREHOUSE = 'RETURNING_TO_WAREHOUSE', 'Đang chuyển hoàn về kho'
    FAILED_CLOSED = 'FAILED_CLOSED', 'Đóng đơn giao thất bại'


class Order(models.Model):
    order_code = models.CharField(
        max_length=30,
        unique=True,
        verbose_name='Mã đơn hàng',
        help_text='Mã đơn hàng duy nhất hệ thống (NOT NULL, UNIQUE; tối đa 30 ký tự; dùng cho QR thanh toán).',
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='orders',
        verbose_name='Khách hàng',
        help_text='Tài khoản người dùng đặt hàng (on_delete=PROTECT bảo toàn lịch sử giao dịch).',
    )
    recipient_name = models.CharField(
        max_length=120,
        verbose_name='Tên người nhận',
        help_text='Họ và tên người nhận hàng (tối đa 120 ký tự).',
    )
    recipient_phone = models.CharField(
        max_length=15,
        verbose_name='Số điện thoại người nhận',
        help_text='Số điện thoại liên hệ giao hàng (tối đa 15 ký tự).',
    )
    shipping_address = models.TextField(
        verbose_name='Địa chỉ giao hàng',
        help_text='Địa chỉ chi tiết nhận hàng.',
    )
    order_status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        verbose_name='Trạng thái đơn hàng',
        help_text='Trạng thái đơn hàng theo quy trình nghiệp vụ (không gán giá trị mặc định để tránh đồng nhất QR và COD).',
    )
    subtotal = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Tiền hàng trước giảm',
        help_text='Tổng tiền hàng trước khi áp dụng chiết khấu (>= 0).',
    )
    discount_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Tổng giảm giá',
        help_text='Tổng số tiền giảm giá từ voucher/khuyến mãi (>= 0, mặc định 0.00).',
    )
    grand_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Tổng thanh toán',
        help_text='Số tiền thực tế khách hàng phải thanh toán (>= 0).',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời gian tạo',
        help_text='Thời điểm tạo đơn hàng.',
    )
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời gian giao thành công',
        help_text='Thời điểm giao hàng thành công (NULL đến khi hoàn tất giao hàng).',
    )
    failed_delivery_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời gian giao thất bại',
        help_text='Thời điểm ghi nhận giao hàng thất bại sau khi đã xuất kho (NULL đến khi phát sinh sự cố).',
    )

    class Meta:
        verbose_name = 'Đơn hàng'
        verbose_name_plural = 'Đơn hàng'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0),
                name='order_subtotal_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(discount_total__gte=0),
                name='order_discount_total_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(grand_total__gte=0),
                name='order_grand_total_gte_0',
            ),
        ]

    def __str__(self):
        return f"Đơn hàng {self.order_code} ({self.get_order_status_display()})"

    def clean(self):
        super().clean()
        errors = {}

        if not self.order_code or not str(self.order_code).strip():
            errors['order_code'] = 'Mã đơn hàng không được để trống.'
        elif len(str(self.order_code).strip()) > 30:
            errors['order_code'] = 'Mã đơn hàng không được vượt quá 30 ký tự.'

        if not self.recipient_name or not str(self.recipient_name).strip():
            errors['recipient_name'] = 'Tên người nhận không được để trống.'
        elif len(str(self.recipient_name).strip()) > 120:
            errors['recipient_name'] = 'Tên người nhận không được vượt quá 120 ký tự.'

        if not self.recipient_phone or not str(self.recipient_phone).strip():
            errors['recipient_phone'] = 'Số điện thoại người nhận không được để trống.'
        else:
            try:
                validate_delivery_phone(self.recipient_phone)
            except ValidationError as e:
                errors['recipient_phone'] = e.message

        if not self.shipping_address or not str(self.shipping_address).strip():
            errors['shipping_address'] = 'Địa chỉ giao hàng không được để trống.'

        if not self.order_status:
            errors['order_status'] = 'Trạng thái đơn hàng không được để trống.'
        elif self.order_status not in OrderStatus.values:
            errors['order_status'] = f'Trạng thái đơn hàng không hợp lệ: {self.order_status}.'

        if _is_finite_decimal(self.subtotal) and self.subtotal < Decimal('0.00'):
            errors['subtotal'] = 'Tiền hàng trước giảm không được âm.'
        if _is_finite_decimal(self.discount_total) and self.discount_total < Decimal('0.00'):
            errors['discount_total'] = 'Tổng giảm giá không được âm.'
        if _is_finite_decimal(self.grand_total) and self.grand_total < Decimal('0.00'):
            errors['grand_total'] = 'Tổng thanh toán không được âm.'

        if errors:
            raise ValidationError(errors)

    @property
    def is_eligible_for_customer_cancellation(self) -> bool:
        if self.order_status not in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED):
            return False
        if self.delivered_at is not None or self.failed_delivery_at is not None:
            return False
        try:
            payment = getattr(self, 'payment', None)
            if not payment or payment.method != 'COD' or payment.status != 'UNPAID':
                return False
        except Exception:
            return False
        try:
            if hasattr(self, 'cancellation') and self.cancellation is not None:
                return False
        except Exception:
            pass
        return True


class OrderItem(models.Model):
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Đơn hàng',
        help_text='Đơn hàng cha chứa dòng sản phẩm này.',
    )
    variant = models.ForeignKey(
        'catalog.ProductVariant',
        on_delete=models.PROTECT,
        related_name='order_items',
        verbose_name='Biến thể sản phẩm',
        help_text='Biến thể sản phẩm được mua (on_delete=PROTECT chống xóa sản phẩm đã bán).',
    )
    product_name_snapshot = models.CharField(
        max_length=150,
        verbose_name='Tên sản phẩm (snapshot)',
        help_text='Snapshot tên sản phẩm tại thời điểm đặt hàng (tối đa 150 ký tự).',
    )
    variant_snapshot = models.CharField(
        max_length=255,
        verbose_name='Thuộc tính biến thể (snapshot)',
        help_text='Snapshot tổ hợp thuộc tính biến thể (ví dụ: Màu: Đen, Dung lượng: 128GB; tối đa 255 ký tự).',
    )
    sku_snapshot = models.CharField(
        max_length=60,
        verbose_name='Mã SKU (snapshot)',
        help_text='Snapshot mã SKU biến thể tại thời điểm đặt hàng (tối đa 60 ký tự).',
    )
    quantity = models.PositiveIntegerField(
        verbose_name='Số lượng',
        help_text='Số lượng đặt mua (> 0).',
    )
    unit_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Đơn giá (snapshot)',
        help_text='Snapshot đơn giá niêm yết/bán tại thời điểm đặt hàng (>= 0).',
    )
    discount_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Tiền giảm giá (snapshot)',
        help_text='Số tiền giảm giá phân bổ cho dòng này (>= 0, mặc định 0.00).',
    )
    line_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Thành tiền dòng',
        help_text='Tổng tiền thanh toán của dòng sản phẩm sau khi trừ giảm giá (>= 0).',
    )

    class Meta:
        verbose_name = 'Dòng đơn hàng'
        verbose_name_plural = 'Dòng đơn hàng'
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='order_item_quantity_gt_0',
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name='order_item_unit_price_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0),
                name='order_item_discount_amount_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(line_total__gte=0),
                name='order_item_line_total_gte_0',
            ),
        ]

    def __str__(self):
        return f"Dòng {self.id}: {self.product_name_snapshot} ({self.sku_snapshot}) x {self.quantity}"

    def clean(self):
        super().clean()
        errors = {}

        if not self.product_name_snapshot or not str(self.product_name_snapshot).strip():
            errors['product_name_snapshot'] = 'Snapshot tên sản phẩm không được để trống.'
        elif len(str(self.product_name_snapshot).strip()) > 150:
            errors['product_name_snapshot'] = 'Snapshot tên sản phẩm không được vượt quá 150 ký tự.'

        if not self.variant_snapshot or not str(self.variant_snapshot).strip():
            errors['variant_snapshot'] = 'Snapshot thuộc tính biến thể không được để trống.'
        elif len(str(self.variant_snapshot).strip()) > 255:
            errors['variant_snapshot'] = 'Snapshot thuộc tính biến thể không được vượt quá 255 ký tự.'

        if not self.sku_snapshot or not str(self.sku_snapshot).strip():
            errors['sku_snapshot'] = 'Snapshot SKU không được để trống.'
        elif len(str(self.sku_snapshot).strip()) > 60:
            errors['sku_snapshot'] = 'Snapshot SKU không được vượt quá 60 ký tự.'

        if _is_valid_integer(self.quantity):
            if self.quantity <= 0:
                errors['quantity'] = 'Số lượng đặt mua phải lớn hơn 0.'

        if _is_finite_decimal(self.unit_price) and self.unit_price < Decimal('0.00'):
            errors['unit_price'] = 'Đơn giá không được âm.'
        if _is_finite_decimal(self.discount_amount) and self.discount_amount < Decimal('0.00'):
            errors['discount_amount'] = 'Số tiền giảm giá không được âm.'
        if _is_finite_decimal(self.line_total) and self.line_total < Decimal('0.00'):
            errors['line_total'] = 'Thành tiền không được âm.'

        if errors:
            raise ValidationError(errors)


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.CASCADE,
        related_name='status_history',
        verbose_name='Đơn hàng',
        help_text='Đơn hàng phát sinh sự kiện chuyển trạng thái.',
    )
    old_status = models.CharField(
        max_length=30,
        blank=True,
        verbose_name='Trạng thái cũ',
        help_text='Trạng thái trước khi chuyển (đối với sự kiện tạo đơn ban đầu, giá trị là chuỗi rỗng biểu thị không có trạng thái trước đó).',
    )
    new_status = models.CharField(
        max_length=30,
        verbose_name='Trạng thái mới',
        help_text='Trạng thái sau khi chuyển (NOT NULL, tối đa 30 ký tự).',
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_status_history_entries',
        verbose_name='Người thực hiện',
        help_text='Tài khoản nhân viên/quản trị viên thực hiện chuyển trạng thái (NULL nếu do hệ thống tự động đổi).',
    )
    reason = models.TextField(
        null=True,
        blank=True,
        verbose_name='Lý do thay đổi',
        help_text='Lý do hoặc ghi chú giải trình cho việc chuyển trạng thái (tùy chọn).',
    )
    changed_at = models.DateTimeField(
        default=timezone.now,
        verbose_name='Thời điểm thay đổi',
        help_text='Thời điểm ghi nhận sự kiện chuyển trạng thái.',
    )

    class Meta:
        verbose_name = 'Lịch sử trạng thái đơn hàng'
        verbose_name_plural = 'Lịch sử trạng thái đơn hàng'
        ordering = ['changed_at', 'id']

    def __str__(self):
        old_display = self.old_status or '(Khởi tạo)'
        return f"Lịch sử đơn {self.order.order_code}: {old_display} -> {self.new_status} lúc {self.changed_at}"

    def clean(self):
        super().clean()
        errors = {}

        if self.old_status and len(str(self.old_status).strip()) > 30:
            errors['old_status'] = 'Trạng thái cũ không được vượt quá 30 ký tự.'

        if not self.new_status or not str(self.new_status).strip():
            errors['new_status'] = 'Trạng thái mới không được để trống.'
        elif len(str(self.new_status).strip()) > 30:
            errors['new_status'] = 'Trạng thái mới không được vượt quá 30 ký tự.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Lịch sử trạng thái đơn hàng là bản ghi bất biến (append-only), không thể chỉnh sửa.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Lịch sử trạng thái đơn hàng là bản ghi bất biến (append-only), không thể xóa.')


class CancellationReasonCode(models.TextChoices):
    WRONG_ORDER = 'WRONG_ORDER', 'Đặt nhầm'
    CHANGE_VARIANT = 'CHANGE_VARIANT', 'Muốn đổi SP/biến thể'
    FOUND_BETTER = 'FOUND_BETTER', 'Tìm sản phẩm phù hợp hơn'
    NO_LONGER_NEEDED = 'NO_LONGER_NEEDED', 'Không còn nhu cầu'
    SLOW_PROCESSING = 'SLOW_PROCESSING', 'Xử lý lâu'
    OTHER = 'OTHER', 'Khác'


class OrderCancellation(models.Model):
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='cancellation',
        verbose_name='Đơn hàng',
        help_text='Đơn hàng bị hủy (OneToOne, on_delete=PROTECT bảo toàn lịch sử hủy đơn).',
    )
    reason_code = models.CharField(
        max_length=40,
        choices=CancellationReasonCode.choices,
        verbose_name='Lý do hủy',
        help_text='Mã lý do hủy đơn (NOT NULL, choices, có OTHER).',
    )
    description = models.TextField(
        null=True,
        blank=True,
        verbose_name='Mô tả chi tiết',
        help_text='Mô tả bổ sung (bắt buộc nếu reason_code=OTHER, tối đa 500 ký tự).',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_cancellations',
        verbose_name='Người yêu cầu hủy',
        help_text='Tài khoản người dùng yêu cầu hủy (NULL nếu do hệ thống tự động đổi).',
    )
    cancelled_at = models.DateTimeField(
        default=timezone.now,
        verbose_name='Thời điểm hủy',
        help_text='Thời điểm ghi nhận sự kiện hủy đơn.',
    )
    reservation_released_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm giải phóng giữ tồn',
        help_text='Thời điểm hoàn tất giải phóng giữ kho (ghi đúng 1 lần).',
    )

    class Meta:
        verbose_name = 'Bản ghi hủy đơn hàng'
        verbose_name_plural = 'Bản ghi hủy đơn hàng'
        ordering = ['-cancelled_at', '-id']

    def __str__(self):
        return f"Hủy đơn {self.order.order_code}: {self.get_reason_code_display()} lúc {self.cancelled_at}"

    def clean(self):
        super().clean()
        errors = {}

        if not self.reason_code or self.reason_code not in CancellationReasonCode.values:
            errors['reason_code'] = 'Lý do hủy đơn không hợp lệ.'

        desc_stripped = str(self.description or '').strip()
        if self.reason_code == CancellationReasonCode.OTHER:
            if not desc_stripped:
                errors['description'] = 'Vui lòng nhập mô tả chi tiết khi chọn lý do "Khác".'

        if desc_stripped and len(desc_stripped) > 500:
            errors['description'] = 'Mô tả chi tiết không được vượt quá 500 ký tự.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Bản ghi hủy đơn hàng là bất biến (append-only), không thể chỉnh sửa.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Bản ghi hủy đơn hàng là bất biến (append-only), không thể xóa.')


