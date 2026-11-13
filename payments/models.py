from decimal import Decimal
import json
from typing import Optional
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def _is_finite_decimal(value):
    return isinstance(value, Decimal) and value.is_finite()


class PaymentMethod(models.TextChoices):
    COD = 'COD', 'Thanh toán khi nhận hàng (COD)'
    QR = 'QR', 'Chuyển khoản ngân hàng qua mã QR'


class PaymentStatus(models.TextChoices):
    UNPAID = 'UNPAID', 'Chưa thanh toán'
    PENDING_VERIFY = 'PENDING_VERIFY', 'Chờ xác minh'
    PAID = 'PAID', 'Đã thanh toán'

    FAILED = 'FAILED', 'Thanh toán thất bại'
    EXPIRED = 'EXPIRED', 'Hết hạn thanh toán'
    PENDING_REFUND = 'PENDING_REFUND', 'Chờ hoàn tiền'
    REFUNDED = 'REFUNDED', 'Đã hoàn tiền'
    PARTIALLY_REFUNDED = 'PARTIALLY_REFUNDED', 'Hoàn tiền một phần'


class Payment(models.Model):
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='payment',
        verbose_name='Đơn hàng',
        help_text='Đơn hàng gắn liền với bản ghi thanh toán (OneToOne, on_delete=PROTECT bảo toàn lịch sử tài chính).',
    )
    method = models.CharField(
        max_length=10,
        choices=PaymentMethod.choices,
        verbose_name='Phương thức thanh toán',
        help_text='Phương thức thanh toán (COD hoặc QR, không gán mặc định ngầm định).',
    )
    status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        verbose_name='Trạng thái thanh toán',
        help_text='Trạng thái thanh toán độc lập (yêu cầu gán tường minh, không gán mặc định ngầm định).',
    )
    expected_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Số tiền dự kiến',
        help_text='Số tiền cần thanh toán snapshot tại thời điểm khởi tạo (>= 0, không tự động tính lại).',
    )
    qr_reference = models.CharField(
        max_length=60,
        null=True,
        blank=True,
        verbose_name='Mã tham chiếu QR',
        help_text='Mã tham chiếu chuyển khoản hoặc mã đơn hàng phục vụ đối soát QR (tối đa 60 ký tự, cho phép NULL).',
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời hạn thanh toán',
        help_text='Thời điểm hết hạn thanh toán đối với đơn hàng QR (NULL cho COD hoặc khi chưa kích hoạt thời hạn).',
    )
    customer_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm khách thông báo',
        help_text='Thời điểm khách hàng bấm thông báo đã chuyển khoản (NULL khi khách chưa thông báo).',
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_payments',
        verbose_name='Nhân sự xác minh',
        help_text='Tài khoản nhân viên hoặc admin thực hiện xác minh giao dịch thực tế (on_delete=SET_NULL bảo toàn lịch sử thanh toán).',
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm xác minh',
        help_text='Thời điểm giao dịch được xác nhận tiền vào thực tế (NULL khi chưa xác minh).',
    )
    notes = models.TextField(
        null=True,
        blank=True,
        verbose_name='Ghi chú thanh toán',
        help_text='Ghi chú đối soát, lý do từ chối hoặc thông tin bổ sung.',
    )

    class Meta:
        verbose_name = 'Thanh toán'
        verbose_name_plural = 'Thanh toán'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expected_amount__gte=0),
                name='payment_expected_amount_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(method__in=PaymentMethod.values),
                name='payment_method_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=PaymentStatus.values),
                name='payment_status_valid',
            ),
        ]

    def __str__(self):
        try:
            order_ref = self.order.order_code if self.order_id else 'chưa gán'
        except Exception:
            order_ref = self.order_id or 'chưa gán'
        return f"Thanh toán {self.get_method_display()} - Đơn {order_ref} ({self.get_status_display()}: {self.expected_amount} VNĐ)"

    def clean_fields(self, exclude=None):
        if exclude is None:
            exclude_set = set()
        else:
            exclude_set = set(exclude)

        errors = {}

        if 'expected_amount' not in exclude_set:
            raw_amount = getattr(self, 'expected_amount', None)
            if isinstance(raw_amount, bool):
                errors['expected_amount'] = [
                    ValidationError('Số tiền dự kiến không được sử dụng kiểu logic (boolean).', code='invalid')
                ]
            elif isinstance(raw_amount, float):
                errors['expected_amount'] = [
                    ValidationError('Số tiền dự kiến không được sử dụng kiểu số thực (float).', code='invalid')
                ]

        exclude_for_super = exclude_set | {'expected_amount'} if 'expected_amount' in errors else exclude_set
        try:
            super().clean_fields(exclude=exclude_for_super)
        except ValidationError as e:
            errors = e.update_error_dict(errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        errors = {}

        if self.order_id is None:
            errors['order'] = 'Đơn hàng không được để trống.'

        if not self.method:
            errors['method'] = 'Phương thức thanh toán không được để trống.'
        elif self.method not in PaymentMethod.values:
            errors['method'] = f'Phương thức thanh toán không hợp lệ: {self.method}.'

        if not self.status:
            errors['status'] = 'Trạng thái thanh toán không được để trống.'
        elif self.status not in PaymentStatus.values:
            errors['status'] = f'Trạng thái thanh toán không hợp lệ: {self.status}.'

        if self.expected_amount is None:
            errors['expected_amount'] = 'Số tiền dự kiến không được để trống.'
        elif isinstance(self.expected_amount, bool):
            errors['expected_amount'] = 'Số tiền dự kiến không được sử dụng kiểu logic (boolean).'
        elif isinstance(self.expected_amount, float):
            errors['expected_amount'] = 'Số tiền dự kiến không được sử dụng kiểu số thực (float).'
        elif not _is_finite_decimal(self.expected_amount):
            errors['expected_amount'] = 'Số tiền dự kiến không hợp lệ (phải là số Decimal hữu hạn).'
        elif self.expected_amount < Decimal('0.00'):
            errors['expected_amount'] = 'Số tiền dự kiến không được âm.'
        elif self.expected_amount.as_tuple().exponent < -2:
            errors['expected_amount'] = 'Số tiền dự kiến không được vượt quá 2 chữ số thập phân.'

        if self.qr_reference and len(str(self.qr_reference).strip()) > 60:
            errors['qr_reference'] = 'Mã tham chiếu QR không được vượt quá 60 ký tự.'

        if errors:
            raise ValidationError(errors)

    def set_recipient_snapshot(self, recipient_dict: dict, additional_note: str = "") -> None:
        if not isinstance(recipient_dict, dict):
            return
        snapshot = {
            'bank_name': str(recipient_dict.get('bank_name') or recipient_dict.get('BANK_NAME') or '').strip(),
            'bank_bin': str(recipient_dict.get('bank_bin') or recipient_dict.get('BANK_BIN') or '').strip(),
            'account_number': str(recipient_dict.get('account_number') or recipient_dict.get('ACCOUNT_NUMBER') or '').strip(),
            'account_holder': str(recipient_dict.get('account_holder') or recipient_dict.get('ACCOUNT_HOLDER') or '').strip(),
        }
        data = {'recipient': snapshot}
        if additional_note:
            data['note'] = str(additional_note).strip()
        self.notes = json.dumps(data, ensure_ascii=False)

    def get_recipient_snapshot(self) -> Optional[dict]:
        if not self.notes:
            return None
        try:
            data = json.loads(self.notes)
            if isinstance(data, dict) and 'recipient' in data:
                rec = data['recipient']
                if isinstance(rec, dict) and rec.get('bank_bin') and rec.get('account_number'):
                    return rec
        except Exception:
            pass
        return None

    @property
    def has_recipient_snapshot(self) -> bool:
        return self.get_recipient_snapshot() is not None

    @property
    def recipient_bank_name(self) -> Optional[str]:
        snap = self.get_recipient_snapshot()
        return snap.get('bank_name') if snap else None

    @property
    def recipient_bank_bin(self) -> Optional[str]:
        snap = self.get_recipient_snapshot()
        return snap.get('bank_bin') if snap else None

    @property
    def recipient_account_number(self) -> Optional[str]:
        snap = self.get_recipient_snapshot()
        return snap.get('account_number') if snap else None

    @property
    def recipient_account_holder(self) -> Optional[str]:
        snap = self.get_recipient_snapshot()
        return snap.get('account_holder') if snap else None


class PaymentTransaction(models.Model):
    payment = models.ForeignKey(
        'payments.Payment',
        on_delete=models.PROTECT,
        related_name='transactions',
        verbose_name='Bản ghi thanh toán',
        help_text='Bản ghi thanh toán gắn liền (OneToMany, on_delete=PROTECT bảo toàn chứng từ tài chính).',
    )
    bank_transaction_ref = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        verbose_name='Mã giao dịch ngân hàng',
        help_text='Mã giao dịch từ sao kê/sổ phụ ngân hàng (UNIQUE khi có mã; không tái sử dụng cho nhiều đơn).',
    )
    received_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Số tiền thực nhận',
        help_text='Số tiền thực tế ghi có vào tài khoản ngân hàng (NOT NULL, >= 0, không tự động làm tròn).',
    )
    received_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm nhận tiền',
        help_text='Thời điểm giao dịch ngân hàng phát sinh thực tế (cho phép NULL).',
    )
    recipient_account_last4 = models.CharField(
        max_length=4,
        null=True,
        blank=True,
        verbose_name='4 số cuối tài khoản nhận',
        help_text='4 số cuối tài khoản thụ hưởng phục vụ kiểm tra đối soát (cho phép NULL).',
    )
    transfer_content = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name='Nội dung chuyển khoản',
        help_text='Nội dung chuyển khoản thực tế ghi nhận trên sao kê/sổ phụ ngân hàng (cho phép NULL).',
    )
    verification_status = models.CharField(
        max_length=30,
        verbose_name='Trạng thái xác minh',
        help_text='Trạng thái xác minh đối soát (NOT NULL, ví dụ: VERIFIED).',
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_payment_transactions',
        verbose_name='Nhân sự xác minh',
        help_text='Tài khoản nhân viên bán hàng hoặc admin thực hiện đối chiếu xác minh (on_delete=SET_NULL bảo toàn lịch sử).',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời điểm tạo bản ghi',
        help_text='Thời điểm bản ghi giao dịch được lưu trữ vào hệ thống.',
    )

    class Meta:
        verbose_name = 'Giao dịch thanh toán'
        verbose_name_plural = 'Giao dịch thanh toán'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(received_amount__gte=0),
                name='payment_transaction_received_amount_gte_0',
            ),
            models.UniqueConstraint(
                models.functions.Upper('bank_transaction_ref'),
                name='unique_payment_tx_bank_ref_upper',
            ),
        ]

    def __str__(self):
        ref = self.bank_transaction_ref or 'chưa có mã'
        return f"Giao dịch NH #{ref} - Thanh toán #{self.payment_id} ({self.received_amount:,.0f} VNĐ - {self.verification_status})"

    def clean_fields(self, exclude=None):
        if exclude is None:
            exclude_set = set()
        else:
            exclude_set = set(exclude)

        errors = {}

        if 'received_amount' not in exclude_set:
            raw_amount = getattr(self, 'received_amount', None)
            if isinstance(raw_amount, bool):
                errors['received_amount'] = [
                    ValidationError('Số tiền thực nhận không được sử dụng kiểu logic (boolean).', code='invalid')
                ]
            elif isinstance(raw_amount, float):
                errors['received_amount'] = [
                    ValidationError('Số tiền thực nhận không được sử dụng kiểu số thực (float).', code='invalid')
                ]

        exclude_for_super = exclude_set | {'received_amount'} if 'received_amount' in errors else exclude_set
        try:
            super().clean_fields(exclude=exclude_for_super)
        except ValidationError as e:
            errors = e.update_error_dict(errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        errors = {}

        if self.payment_id is None:
            errors['payment'] = 'Bản ghi thanh toán không được để trống.'

        if self.received_amount is None:
            errors['received_amount'] = 'Số tiền thực nhận không được để trống.'
        elif isinstance(self.received_amount, bool):
            errors['received_amount'] = 'Số tiền thực nhận không được sử dụng kiểu logic (boolean).'
        elif isinstance(self.received_amount, float):
            errors['received_amount'] = 'Số tiền thực nhận không được sử dụng kiểu số thực (float).'
        elif not _is_finite_decimal(self.received_amount):
            errors['received_amount'] = 'Số tiền thực nhận không hợp lệ (phải là số Decimal hữu hạn).'
        elif self.received_amount < Decimal('0.00'):
            errors['received_amount'] = 'Số tiền thực nhận không được âm.'
        elif self.received_amount.as_tuple().exponent < -2:
            errors['received_amount'] = 'Số tiền thực nhận không được vượt quá 2 chữ số thập phân.'

        if self.received_at is None:
            errors['received_at'] = 'Thời điểm nhận tiền thực tế không được để trống.'

        if self.bank_transaction_ref:
            self.bank_transaction_ref = str(self.bank_transaction_ref).strip().upper()
            if len(self.bank_transaction_ref) > 100:
                errors['bank_transaction_ref'] = 'Mã giao dịch ngân hàng không được vượt quá 100 ký tự.'

        if self.recipient_account_last4:
            acc4 = str(self.recipient_account_last4).strip()
            if len(acc4) > 4:
                errors['recipient_account_last4'] = '4 số cuối tài khoản nhận không được vượt quá 4 ký tự.'

        if not self.verification_status:
            errors['verification_status'] = 'Trạng thái xác minh không được để trống.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.bank_transaction_ref:
            self.bank_transaction_ref = str(self.bank_transaction_ref).strip().upper()
        super().save(*args, **kwargs)


class ReconciliationReason(models.TextChoices):
    UNDERPAYMENT = 'UNDERPAYMENT', 'Chuyển khoản thiếu tiền'
    OVERPAYMENT = 'OVERPAYMENT', 'Chuyển khoản thừa tiền'
    WRONG_REFERENCE = 'WRONG_REFERENCE', 'Sai nội dung / mã tham chiếu'
    UNCLEAR_OR_EXPIRED = 'UNCLEAR_OR_EXPIRED', 'Giao dịch chưa rõ hoặc đơn hết hạn chưa xác nhận'
    TRANSFER_AFTER_CANCELLATION = 'TRANSFER_AFTER_CANCELLATION', 'Phát hiện tiền chuyển sau khi đơn đã hủy'
    OTHER = 'OTHER', 'Ngoại lệ khác cần đối soát'


class ReconciliationStatus(models.TextChoices):
    OPEN = 'OPEN', 'Chờ xử lý đối soát'
    RESOLVED = 'RESOLVED', 'Đã đối soát giải quyết'
    ESCALATED = 'ESCALATED', 'Đã chuyển cấp quản trị'


class PaymentReconciliation(models.Model):
    payment = models.ForeignKey(
        'payments.Payment',
        on_delete=models.PROTECT,
        related_name='reconciliations',
        verbose_name='Bản ghi thanh toán',
        help_text='Bản ghi thanh toán phát sinh ngoại lệ cần đối soát (NOT NULL, on_delete=PROTECT bảo toàn hồ sơ tài chính).',
    )
    transaction = models.ForeignKey(
        'payments.PaymentTransaction',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reconciliations',
        verbose_name='Giao dịch liên quan',
        help_text='Giao dịch ngân hàng liên quan (cho phép NULL; chỉ liên kết nếu thuộc cùng Payment, không chế tạo giao dịch giả).',
    )
    reason_code = models.CharField(
        max_length=40,
        choices=ReconciliationReason.choices,
        verbose_name='Mã lý do đối soát',
        help_text='Mã lý do phát sinh đối soát (NOT NULL, tối đa 40 ký tự).',
    )
    status = models.CharField(
        max_length=30,
        choices=ReconciliationStatus.choices,
        default=ReconciliationStatus.OPEN,
        verbose_name='Trạng thái đối soát',
        help_text='Trạng thái xử lý hồ sơ đối soát (NOT NULL, mặc định OPEN).',
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='handled_reconciliations',
        verbose_name='Nhân sự phụ trách / mở hồ sơ',
        help_text='Tài khoản nhân viên bán hàng hoặc admin mở hoặc xử lý hồ sơ đối soát (on_delete=SET_NULL).',
    )
    notes = models.TextField(
        null=True,
        blank=True,
        verbose_name='Ghi chú đối soát',
        help_text='Ghi chú chi tiết, mô tả ngoại lệ hoặc thông tin liên lạc đối soát.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời điểm tạo hồ sơ',
        help_text='Thời điểm tạo hồ sơ đối soát trong hệ thống.',
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm giải quyết',
        help_text='Thời điểm giải quyết xong hồ sơ đối soát (NULL khi chưa giải quyết).',
    )
    sla_due_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Hạn chót SLA',
        help_text='Hạn 12 giờ làm việc từ created_at dựa vào lịch vận hành cấu hình (NULL khi chưa kích hoạt).',
    )
    escalated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm chuyển cấp quản trị',
        help_text='Thời điểm chuyển cấp quản trị khi quá SLA chưa xử lý (cho phép NULL, không auto cancel).',
    )

    class Meta:
        verbose_name = 'Hồ sơ đối soát thanh toán'
        verbose_name_plural = 'Hồ sơ đối soát thanh toán'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason_code__in=ReconciliationReason.values),
                name='reconciliation_reason_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ReconciliationStatus.values),
                name='reconciliation_status_valid',
            ),
            models.UniqueConstraint(
                fields=['payment'],
                condition=models.Q(status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]),
                name='unique_unresolved_reconciliation_per_payment',
            ),
        ]

    def __str__(self):
        return f"Đối soát #{self.pk} - Thanh toán #{self.payment_id} ({self.get_reason_code_display()} - {self.get_status_display()})"

    def clean(self):
        super().clean()
        errors = {}

        if self.payment_id is None:
            errors['payment'] = 'Bản ghi thanh toán không được để trống.'

        if not self.reason_code:
            errors['reason_code'] = 'Lý do đối soát không được để trống.'
        elif self.reason_code not in ReconciliationReason.values:
            errors['reason_code'] = f'Lý do đối soát không hợp lệ: {self.reason_code}.'

        if not self.status:
            errors['status'] = 'Trạng thái đối soát không được để trống.'
        elif self.status not in ReconciliationStatus.values:
            errors['status'] = f'Trạng thái đối soát không hợp lệ: {self.status}.'

        if self.transaction_id is not None and self.payment_id is not None:
            tx = getattr(self, 'transaction', None)
            if tx and tx.payment_id != self.payment_id:
                errors['transaction'] = 'Giao dịch ngân hàng liên kết phải thuộc về cùng bản ghi thanh toán.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        custom_created_at = None
        if self._state.adding and self.created_at is not None:
            custom_created_at = self.created_at
        super().save(*args, **kwargs)
        if custom_created_at is not None and self.created_at != custom_created_at:
            PaymentReconciliation.objects.filter(pk=self.pk).update(created_at=custom_created_at)
            self.created_at = custom_created_at


class RefundReasonType(models.TextChoices):
    CANCEL = 'CANCEL', 'Khách hàng hủy đơn hàng'
    RETURN = 'RETURN', 'Trả hàng hoàn tiền'
    EXCHANGE = 'EXCHANGE', 'Đổi hàng chênh lệch'
    LATE_PAYMENT = 'LATE_PAYMENT', 'Chuyển khoản muộn sau khi đơn hết hạn hoặc đã hủy'
    FAILED_DELIVERY = 'FAILED_DELIVERY', 'Giao hàng thất bại sau khi xuất kho'


class RefundStatus(models.TextChoices):
    PENDING = 'PENDING', 'Chờ xử lý'
    PROCESSING = 'PROCESSING', 'Đang xử lý'
    COMPLETED = 'COMPLETED', 'Đã hoàn tất'
    REJECTED = 'REJECTED', 'Từ chối'


class Refund(models.Model):
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='refunds',
        verbose_name='Đơn hàng',
        help_text='Đơn hàng phát sinh hoàn tiền (NOT NULL -> Order, on_delete=PROTECT bảo toàn chứng từ kế toán).',
    )
    reason_type = models.CharField(
        max_length=20,
        choices=RefundReasonType.choices,
        verbose_name='Lý do hoàn tiền',
        help_text='Phân loại lý do hoàn tiền theo Bảng 20 (CANCEL, RETURN, EXCHANGE, LATE_PAYMENT, FAILED_DELIVERY).',
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Số tiền hoàn',
        help_text='Số tiền hoàn thực tế (NOT NULL, > 0, tối đa 14 chữ số và 2 chữ số thập phân).',
    )
    status = models.CharField(
        max_length=25,
        choices=RefundStatus.choices,
        default=RefundStatus.PENDING,
        verbose_name='Trạng thái hoàn tiền',
        help_text='Trạng thái xử lý hoàn tiền theo Bảng 20 (PENDING, PROCESSING, COMPLETED, REJECTED).',
    )
    bank_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
        verbose_name='Mã tham chiếu ngân hàng',
        help_text='Mã giao dịch ngân hàng chuyển tiền hoàn thực tế (UNIQUE nếu có, cho phép NULL).',
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_refunds',
        verbose_name='Quản trị viên xác nhận',
        help_text='Tài khoản Admin xác nhận việc hoàn tiền (SRS v2.3 BR-PAY-09: chỉ Admin có thẩm quyền).',
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm hoàn tất',
        help_text='Thời điểm giao dịch chuyển tiền hoàn thành công theo xác nhận ngân hàng (NULL khi chưa hoàn).',
    )

    class Meta:
        verbose_name = 'Bản ghi hoàn tiền'
        verbose_name_plural = 'Danh sách hoàn tiền'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='refund_amount_gt_0',
            ),
            models.CheckConstraint(
                condition=models.Q(reason_type__in=RefundReasonType.values),
                name='refund_reason_type_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=RefundStatus.values),
                name='refund_status_valid',
            ),
            models.UniqueConstraint(
                models.functions.Upper('bank_reference'),
                condition=models.Q(bank_reference__isnull=False),
                name='unique_refund_bank_ref_upper',
            ),
        ]

    def __str__(self):
        ref = self.bank_reference or 'chưa có mã'
        return f"Hoàn tiền #{self.pk or 'mới'} [Mã GD: {ref}] - Đơn #{self.order_id} ({self.amount:,.0f} VNĐ - {self.get_reason_type_display()} - {self.get_status_display()})"

    def clean_fields(self, exclude=None):
        if exclude is None:
            exclude_set = set()
        else:
            exclude_set = set(exclude)

        errors = {}

        if 'amount' not in exclude_set:
            raw_amount = getattr(self, 'amount', None)
            if isinstance(raw_amount, bool):
                errors['amount'] = [
                    ValidationError('Số tiền hoàn không được sử dụng kiểu logic (boolean).', code='invalid')
                ]
            elif isinstance(raw_amount, float):
                errors['amount'] = [
                    ValidationError('Số tiền hoàn không được sử dụng kiểu số thực (float).', code='invalid')
                ]

        exclude_for_super = exclude_set | {'amount'} if 'amount' in errors else exclude_set
        try:
            super().clean_fields(exclude=exclude_for_super)
        except ValidationError as e:
            errors = e.update_error_dict(errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        errors = {}

        if self.order_id is None:
            errors['order'] = 'Đơn hàng không được để trống.'

        if not self.reason_type:
            errors['reason_type'] = 'Lý do hoàn tiền không được để trống.'
        elif self.reason_type not in RefundReasonType.values:
            errors['reason_type'] = f'Lý do hoàn tiền không hợp lệ: {self.reason_type}.'

        if self.amount is None:
            errors['amount'] = 'Số tiền hoàn không được để trống.'
        elif isinstance(self.amount, bool):
            errors['amount'] = 'Số tiền hoàn không được sử dụng kiểu logic (boolean).'
        elif isinstance(self.amount, float):
            errors['amount'] = 'Số tiền hoàn không được sử dụng kiểu số thực (float).'
        elif not _is_finite_decimal(self.amount):
            errors['amount'] = 'Số tiền hoàn không hợp lệ (phải là số Decimal hữu hạn).'
        elif self.amount <= Decimal('0.00'):
            errors['amount'] = 'Số tiền hoàn phải lớn hơn 0.'
        elif self.amount.as_tuple().exponent < -2:
            errors['amount'] = 'Số tiền hoàn không được vượt quá 2 chữ số thập phân.'

        if not self.status:
            errors['status'] = 'Trạng thái hoàn tiền không được để trống.'
        elif self.status not in RefundStatus.values:
            errors['status'] = f'Trạng thái hoàn tiền không hợp lệ: {self.status}.'

        if self.bank_reference is not None:
            cleaned_ref = str(self.bank_reference).strip()
            if cleaned_ref:
                self.bank_reference = cleaned_ref.upper()
                if len(self.bank_reference) > 100:
                    errors['bank_reference'] = 'Mã tham chiếu ngân hàng không được vượt quá 100 ký tự.'
            else:
                self.bank_reference = None

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.bank_reference is not None:
            cleaned_ref = str(self.bank_reference).strip()
            self.bank_reference = cleaned_ref.upper() if cleaned_ref else None
        super().save(*args, **kwargs)


