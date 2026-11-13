from decimal import Decimal
import random
import re
import time
from datetime import datetime
from typing import Any, List, NamedTuple, Optional
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone

from accounts.permissions import is_admin, is_sales_staff
from coupons.models import Coupon, CouponUsage, CouponUsageStatus
from inventory.models import Inventory, InventoryReservation, InventoryReservationStatus
from orders.models import Order, OrderStatus, OrderStatusHistory
from payments.models import (
    Payment, PaymentMethod, PaymentStatus, PaymentTransaction,
    PaymentReconciliation, ReconciliationReason, ReconciliationStatus,
    Refund, RefundReasonType, RefundStatus,
    _is_finite_decimal
)
from payments.sla import calculate_sla_due_at


class VerificationResult(NamedTuple):
    payment: Payment
    order: Order
    transaction: PaymentTransaction
    already_verified: bool = False


def _is_sqlite_lock_error(exc: Exception) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    msg = str(exc).lower()
    return 'locked' in msg or 'busy' in msg


def _handle_already_paid_payment(payment: Payment, order: Order, cleaned_bank_ref: str) -> VerificationResult:
    matching_tx = PaymentTransaction.objects.filter(
        payment=payment,
        verification_status='VERIFIED'
    ).first()

    if matching_tx is not None and matching_tx.bank_transaction_ref.strip().upper() == cleaned_bank_ref:
        return VerificationResult(
            payment=payment,
            order=order,
            transaction=matching_tx,
            already_verified=True,
        )

    if matching_tx is None:
        err = ValidationError(
            f"Thanh toán #{payment.pk} đã ở trạng thái Đã thanh toán (PAID) nhưng không tìm thấy chứng từ giao dịch xác minh. "
            "Giao dịch cần chuyển sang quy trình đối soát riêng (reconciliation required).",
            code='reconciliation_required'
        )
        err.code = 'reconciliation_required'
        raise err

    err = ValidationError({
        'bank_transaction_ref': ValidationError(
            f"Thanh toán #{payment.pk} đã được xác nhận trước đó với mã giao dịch '{matching_tx.bank_transaction_ref}'. "
            f"Mã giao dịch gửi lên '{cleaned_bank_ref}' không khớp với chứng từ đã xác nhận. "
            "Giao dịch cần chuyển sang quy trình đối soát riêng (reconciliation required).",
            code='reconciliation_required'
        )
    })
    err.code = 'reconciliation_required'
    raise err


def verify_qr_payment(
    payment_id: int,
    staff_user,
    observed_recipient_account: str,
    observed_amount,
    observed_transfer_reference: str,
    bank_transaction_ref: str,
    received_at=None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> VerificationResult:
    from datetime import datetime

    if not is_sales_staff(staff_user):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có quyền xác minh thanh toán.")

    if not bank_transaction_ref or not str(bank_transaction_ref).strip():
        raise ValidationError({
            'bank_transaction_ref': ValidationError('Mã giao dịch ngân hàng không được để trống.', code='bank_transaction_ref_required')
        })

    cleaned_bank_ref = str(bank_transaction_ref).strip().upper()

    if isinstance(observed_amount, (bool, float)):
        raise ValidationError({
            'observed_amount': ValidationError('Số tiền thực nhận không được sử dụng kiểu boolean hoặc float.', code='invalid_amount')
        })

    try:
        amount_dec = Decimal(str(observed_amount))
    except Exception:
        raise ValidationError({
            'observed_amount': ValidationError('Số tiền thực nhận không đúng định dạng số tiền hợp lệ.', code='invalid_amount')
        })

    if not _is_finite_decimal(amount_dec) or amount_dec < Decimal('0.00'):
        raise ValidationError({
            'observed_amount': ValidationError('Số tiền thực nhận phải là số hữu hạn lớn hơn hoặc bằng 0.', code='invalid_amount')
        })

    if received_at is None:
        raise ValidationError({
            'received_at': ValidationError('Thời điểm nhận tiền thực tế từ ngân hàng không được để trống.', code='received_at_required')
        })

    if isinstance(received_at, str):
        from django.utils.dateparse import parse_datetime
        parsed_dt = parse_datetime(received_at)
        if parsed_dt is None:
            raise ValidationError({
                'received_at': ValidationError('Thời điểm nhận tiền thực tế không đúng định dạng ngày giờ hợp lệ.', code='invalid_received_at')
            })
        received_at = parsed_dt

    if not isinstance(received_at, datetime):
        raise ValidationError({
            'received_at': ValidationError('Thời điểm nhận tiền thực tế không đúng định dạng ngày giờ hợp lệ.', code='invalid_received_at')
        })

    if timezone.is_naive(received_at):
        received_at = timezone.make_aware(received_at)

    now = timezone.now()
    if received_at > now:
        err = ValidationError({
            'received_at': ValidationError('Thời điểm nhận tiền thực tế không thể ở tương lai.', code='future_received_at')
        })
        err.code = 'future_received_at'
        raise err

    payment = Payment.objects.select_related('order').filter(pk=payment_id).first()
    if payment is None:
        raise ValidationError('Không tìm thấy bản ghi thanh toán chỉ định.', code='payment_not_found')

    order = payment.order
    if order is None:
        raise ValidationError('Bản ghi thanh toán chưa được liên kết với đơn hàng.', code='order_missing')

    if payment.method != PaymentMethod.QR:
        raise ValidationError(
            'Chỉ hỗ trợ xác minh thủ công đối chiếu chuyển khoản cho đơn hàng thanh toán qua mã QR.',
            code='invalid_payment_method'
        )

    if payment.status == PaymentStatus.PAID:
        return _handle_already_paid_payment(payment, order, cleaned_bank_ref)

    if payment.status != PaymentStatus.PENDING_VERIFY:
        raise ValidationError(
            f"Chỉ có thể xác minh cho thanh toán QR ở trạng thái Chờ xác minh (PENDING_VERIFY). "
            f"Trạng thái hiện tại: {payment.get_status_display()}. Giao dịch cần đối soát riêng.",
            code='ineligible_payment_status'
        )

    if order.order_status != OrderStatus.PENDING_PAYMENT:
        raise ValidationError(
            f"Chỉ có thể xác minh thanh toán cho đơn hàng ở trạng thái Chờ thanh toán (PENDING_PAYMENT). "
            f"Trạng thái đơn hàng hiện tại: {order.get_order_status_display()}. "
            "Giao dịch chuyển khoản này cần chuyển sang quy trình đối soát riêng (BR-PAY-08).",
            code='ineligible_order_status'
        )

    existing_tx = PaymentTransaction.objects.filter(bank_transaction_ref__iexact=cleaned_bank_ref).first()
    if existing_tx:
        raise ValidationError({
            'bank_transaction_ref': (
                f"Mã giao dịch ngân hàng '{cleaned_bank_ref}' đã được ghi nhận cho thanh toán #{existing_tx.payment_id}. "
                "Không được phép tái sử dụng mã giao dịch để thanh toán nhiều đơn hàng (SRS Table 18 & BR-PAY-08)."
            )
        }, code='duplicate_bank_transaction')

    recipient_snapshot = payment.get_recipient_snapshot()
    if not recipient_snapshot or not isinstance(recipient_snapshot, dict):
        raise ValidationError(
            f"Thanh toán #{payment.pk} không có thông tin tài khoản thụ hưởng lưu trữ hợp lệ (thiếu recipient snapshot). "
            "Giao dịch cần chuyển sang quy trình đối soát riêng.",
            code='recipient_snapshot_missing'
        )

    stored_account = recipient_snapshot.get('account_number')
    if not stored_account or not str(stored_account).strip():
        raise ValidationError(
            f"Thanh toán #{payment.pk} thiếu số tài khoản thụ hưởng trong snapshot lưu trữ. "
            "Giao dịch cần chuyển sang quy trình đối soát riêng.",
            code='recipient_snapshot_missing'
        )

    cleaned_stored_acc = re.sub(r'[\s\-]', '', str(stored_account).strip())
    cleaned_observed_acc = re.sub(r'[\s\-]', '', str(observed_recipient_account or '').strip())

    if not cleaned_observed_acc:
        raise ValidationError({
            'observed_recipient_account': ValidationError('Tài khoản nhận tiền thực tế không được để trống.', code='recipient_account_required')
        })

    if cleaned_observed_acc != cleaned_stored_acc:
        raise ValidationError({
            'observed_recipient_account': ValidationError(
                f"Tài khoản nhận tiền thực tế ({observed_recipient_account}) không khớp đầy đủ với tài khoản "
                f"thụ hưởng của đơn hàng ({stored_account}). Cần chuyển sang quy trình đối soát riêng.",
                code='recipient_account_mismatch'
            )
        })

    if amount_dec != payment.expected_amount:
        if amount_dec < payment.expected_amount:
            raise ValidationError({
                'observed_amount': ValidationError(
                    f"Số tiền thực nhận ({amount_dec:,.0f} VNĐ) thiếu so với số tiền cần thanh toán "
                    f"({payment.expected_amount:,.0f} VNĐ). Giao dịch cần chuyển sang quy trình đối soát riêng.",
                    code='underpayment'
                )
            })
        else:
            raise ValidationError({
                'observed_amount': ValidationError(
                    f"Số tiền thực nhận ({amount_dec:,.0f} VNĐ) thừa so với số tiền cần thanh toán "
                    f"({payment.expected_amount:,.0f} VNĐ). Giao dịch cần chuyển sang quy trình đối soát riêng.",
                    code='overpayment'
                )
            })

    stored_ref = str(payment.qr_reference or order.order_code or '').strip().upper()
    cleaned_content = str(observed_transfer_reference or '').strip().upper()
    if not stored_ref:
        raise ValidationError({
            'observed_transfer_reference': ValidationError('Đơn hàng không có mã tham chiếu chuyển khoản lưu trữ hợp lệ.', code='reference_missing')
        })

    pattern = rf'(?<![A-Z0-9_-]){re.escape(stored_ref)}(?![A-Z0-9_-])'
    if not re.search(pattern, cleaned_content):
        raise ValidationError({
            'observed_transfer_reference': ValidationError(
                f"Nội dung chuyển khoản '{observed_transfer_reference}' không khớp với mã tham chiếu của đơn hàng "
                f"({stored_ref}). Giao dịch cần chuyển sang quy trình đối soát riêng.",
                code='reference_mismatch'
            )
        })

    if payment.expires_at is None:
        err = ValidationError({
            'received_at': ValidationError(
                f"Thanh toán #{payment.pk} không có thời hạn thanh toán lưu trữ (expires_at is None). "
                "Cần chuyển sang quy trình rà soát và đối soát riêng (review required).",
                code='missing_expiry'
            )
        })
        err.code = 'missing_expiry'
        raise err

    if order.created_at is not None and received_at < order.created_at:
        err = ValidationError({
            'received_at': ValidationError(
                f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) trước thời điểm tạo đơn hàng "
                f"({order.created_at.strftime('%H:%M:%S %d/%m/%Y')}). Giao dịch không hợp lệ, cần đối soát riêng.",
                code='received_before_order_creation'
            )
        })
        err.code = 'received_before_order_creation'
        raise err

    if received_at >= payment.expires_at:
        err = ValidationError({
            'received_at': ValidationError(
                f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) vượt quá hoặc chạm mốc "
                f"thời hạn thanh toán của đơn hàng ({payment.expires_at.strftime('%H:%M:%S %d/%m/%Y')}). "
                "Giao dịch muộn này cần chuyển sang quy trình đối soát riêng (BR-PAY-08).",
                code='late_transfer_reconciliation_required'
            )
        })
        err.code = 'late_transfer_reconciliation_required'
        raise err

    is_nested = connection.in_atomic_block
    effective_retries = 1 if is_nested else max(1, max_retries)

    for attempt in range(effective_retries):
        try:
            with transaction.atomic():
                fresh_payment = Payment.objects.select_related('order').filter(pk=payment_id).first()
                if fresh_payment is None:
                    raise ValidationError('Bản ghi thanh toán không tồn tại.', code='payment_not_found')

                if fresh_payment.status == PaymentStatus.PAID:
                    return _handle_already_paid_payment(fresh_payment, fresh_payment.order, cleaned_bank_ref)

                if fresh_payment.status != PaymentStatus.PENDING_VERIFY:
                    raise ValidationError(
                        f"Thanh toán không còn ở trạng thái Chờ xác minh (PENDING_VERIFY) mà là {fresh_payment.get_status_display()}.",
                        code='ineligible_payment_status'
                    )

                fresh_order = fresh_payment.order
                if fresh_order.order_status != OrderStatus.PENDING_PAYMENT:
                    raise ValidationError(
                        f"Đơn hàng không còn ở trạng thái Chờ thanh toán (PENDING_PAYMENT) mà là {fresh_order.get_order_status_display()}. "
                        "Giao dịch cần chuyển sang quy trình đối soát riêng.",
                        code='ineligible_order_status'
                    )

                if fresh_payment.expires_at is None:
                    err = ValidationError({
                        'received_at': ValidationError(
                            f"Thanh toán #{fresh_payment.pk} không có thời hạn thanh toán lưu trữ (expires_at is None). "
                            "Cần chuyển sang quy trình rà soát và đối soát riêng (review required).",
                            code='missing_expiry'
                        )
                    })
                    err.code = 'missing_expiry'
                    raise err

                if fresh_order.created_at is not None and received_at < fresh_order.created_at:
                    err = ValidationError({
                        'received_at': ValidationError(
                            f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) trước thời điểm tạo đơn hàng "
                            f"({fresh_order.created_at.strftime('%H:%M:%S %d/%m/%Y')}). Giao dịch không hợp lệ, cần đối soát riêng.",
                            code='received_before_order_creation'
                        )
                    })
                    err.code = 'received_before_order_creation'
                    raise err

                if received_at >= fresh_payment.expires_at:
                    err = ValidationError({
                        'received_at': ValidationError(
                            f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) vượt quá hoặc chạm mốc "
                            f"thời hạn thanh toán của đơn hàng ({fresh_payment.expires_at.strftime('%H:%M:%S %d/%m/%Y')}). "
                            "Giao dịch muộn này cần chuyển sang quy trình đối soát riêng (BR-PAY-08).",
                            code='late_transfer_reconciliation_required'
                        )
                    })
                    err.code = 'late_transfer_reconciliation_required'
                    raise err

                if PaymentTransaction.objects.filter(bank_transaction_ref__iexact=cleaned_bank_ref).exists():
                    raise ValidationError({
                        'bank_transaction_ref': (
                            f"Mã giao dịch ngân hàng '{cleaned_bank_ref}' đã được sử dụng cho một đơn hàng khác."
                        )
                    }, code='duplicate_bank_transaction')

                now_ts = timezone.now()
                last4 = cleaned_stored_acc[-4:]

                tx = PaymentTransaction(
                    payment=fresh_payment,
                    bank_transaction_ref=cleaned_bank_ref,
                    received_amount=amount_dec,
                    received_at=received_at,
                    recipient_account_last4=last4,
                    transfer_content=str(observed_transfer_reference or '').strip()[:255],
                    verification_status='VERIFIED',
                    verified_by=staff_user,
                )
                tx.full_clean()
                tx.save()

                updated_payments = Payment.objects.filter(
                    pk=payment_id,
                    status=PaymentStatus.PENDING_VERIFY
                ).update(
                    status=PaymentStatus.PAID,
                    verified_by=staff_user,
                    verified_at=now_ts,
                )
                if updated_payments != 1:
                    raise ValidationError(
                        "Trạng thái thanh toán đã bị thay đổi đồng thời bởi thao tác khác.",
                        code='concurrent_conflict'
                    )

                updated_orders = Order.objects.filter(
                    pk=fresh_order.pk,
                    order_status=OrderStatus.PENDING_PAYMENT
                ).update(
                    order_status=OrderStatus.PENDING_CONFIRMATION
                )
                if updated_orders != 1:
                    raise ValidationError(
                        "Trạng thái đơn hàng đã bị thay đổi đồng thời bởi thao tác khác. Không ghi đè trạng thái đơn.",
                        code='concurrent_conflict'
                    )

                OrderStatusHistory.objects.create(
                    order=fresh_order,
                    old_status=OrderStatus.PENDING_PAYMENT,
                    new_status=OrderStatus.PENDING_CONFIRMATION,
                    changed_by=staff_user,
                    reason=(
                        f"Nhân viên {staff_user.username} xác minh thanh toán QR khớp thành công "
                        f"(Mã GD NH: {cleaned_bank_ref}, số tiền: {amount_dec:,.0f} VNĐ)"
                    ),
                    changed_at=now_ts,
                )

                coupon_usage = CouponUsage.objects.filter(order=fresh_order).first()
                if coupon_usage:
                    if coupon_usage.status != CouponUsageStatus.RESERVED:
                        raise ValidationError(
                            f"Trạng thái sử dụng mã giảm giá không hợp lệ ({coupon_usage.status}), kỳ vọng RESERVED. Giao dịch cần đối soát.",
                            code='inconsistent_coupon_reservation'
                        )

                    rows_cu = CouponUsage.objects.filter(
                        pk=coupon_usage.pk,
                        status=CouponUsageStatus.RESERVED
                    ).update(
                        status=CouponUsageStatus.USED,
                        finalized_at=now_ts
                    )
                    if rows_cu != 1:
                        raise ValidationError(
                            "Cập nhật trạng thái sử dụng mã giảm giá thất bại. Toàn bộ giao dịch được hoàn nguyên.",
                            code='inconsistent_coupon_reservation'
                        )

                    coupon_rows = Coupon.objects.filter(
                        pk=coupon_usage.coupon_id,
                        reserved_count__gt=0
                    ).update(
                        reserved_count=F('reserved_count') - 1,
                        used_count=F('used_count') + 1
                    )
                    if coupon_rows != 1:
                        raise ValidationError(
                            "Cập nhật hạn ngạch mã giảm giá thất bại (reserved_count không hợp lệ). Toàn bộ giao dịch được hoàn nguyên.",
                            code='inconsistent_coupon_counter'
                        )


                fresh_payment.refresh_from_db()
                fresh_order.refresh_from_db()
                return VerificationResult(
                    payment=fresh_payment,
                    order=fresh_order,
                    transaction=tx,
                    already_verified=False,
                )

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            if attempt == effective_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

        except IntegrityError as ie:
            err_str = str(ie).lower()
            if 'bank_transaction_ref' in err_str or 'unique' in err_str:
                raise ValidationError({
                    'bank_transaction_ref': (
                        f"Mã giao dịch ngân hàng '{cleaned_bank_ref}' đã được xác nhận cho đơn hàng khác."
                    )
                }, code='duplicate_bank_transaction')
            raise

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


class ReconciliationResult(NamedTuple):
    reconciliation: PaymentReconciliation
    created: bool
    already_open: bool
    order_status_updated: bool
    old_order_status: str
    new_order_status: str


def open_payment_reconciliation(
    payment_id: int,
    staff_user,
    reason_code: str,
    notes: Optional[str] = None,
    transaction_id: Optional[int] = None,
    calendar_config: Optional[dict] = None,
    created_at: Optional[datetime] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ReconciliationResult:
    if not is_sales_staff(staff_user):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có quyền mở hồ sơ đối soát.")

    if not reason_code or str(reason_code).strip() not in ReconciliationReason.values:
        raise ValidationError({
            'reason_code': ValidationError('Lý do đối soát không hợp lệ hoặc để trống.', code='invalid_reason_code')
        })
    cleaned_reason = str(reason_code).strip()

    payment = Payment.objects.select_related('order').filter(pk=payment_id).first()
    if payment is None:
        raise ValidationError('Không tìm thấy bản ghi thanh toán chỉ định.', code='payment_not_found')

    if payment.method != PaymentMethod.QR:
        raise ValidationError(
            'Chỉ hỗ trợ mở hồ sơ đối soát cho đơn hàng thanh toán chuyển khoản qua mã QR.',
            code='invalid_payment_method'
        )

    tx_obj = None
    if transaction_id is not None:
        tx_obj = PaymentTransaction.objects.filter(pk=transaction_id).first()
        if tx_obj is None or tx_obj.payment_id != payment.pk:
            raise ValidationError({
                'transaction': ValidationError(
                    'Giao dịch ngân hàng chỉ định không tồn tại hoặc không thuộc về bản ghi thanh toán này.',
                    code='invalid_transaction'
                )
            })

    existing_case = PaymentReconciliation.objects.filter(
        payment=payment,
        status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED],
    ).first()
    if existing_case:
        order_st = payment.order.order_status if payment.order else ''
        return ReconciliationResult(
            reconciliation=existing_case,
            created=False,
            already_open=True,
            order_status_updated=False,
            old_order_status=order_st,
            new_order_status=order_st,
        )

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                p = Payment.objects.select_related('order').select_for_update().get(pk=payment.pk)
                order = p.order

                inner_existing = PaymentReconciliation.objects.filter(
                    payment=p,
                    status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED],
                ).first()
                if inner_existing:
                    ord_st = order.order_status if order else ''
                    return ReconciliationResult(
                        reconciliation=inner_existing,
                        created=False,
                        already_open=True,
                        order_status_updated=False,
                        old_order_status=ord_st,
                        new_order_status=ord_st,
                    )

                order_status_updated = False
                old_status = order.order_status if order else ''
                new_status = old_status

                if order is not None and order.order_status == OrderStatus.PENDING_PAYMENT:
                    rows_affected = Order.objects.filter(
                        pk=order.pk,
                        order_status=OrderStatus.PENDING_PAYMENT
                    ).update(order_status=OrderStatus.PENDING_RECONCILIATION)

                    if rows_affected == 1:
                        order.refresh_from_db(fields=['order_status'])
                        order_status_updated = True
                        new_status = OrderStatus.PENDING_RECONCILIATION

                        reason_label = dict(ReconciliationReason.choices).get(cleaned_reason, cleaned_reason)
                        hist_reason = f"Mở hồ sơ đối soát chuyển khoản QR (Lý do: {reason_label})"
                        cleaned_notes = str(notes).strip() if notes else ""
                        if cleaned_notes:
                            hist_reason += f" - Ghi chú: {cleaned_notes[:100]}"

                        OrderStatusHistory.objects.create(
                            order=order,
                            old_status=OrderStatus.PENDING_PAYMENT,
                            new_status=OrderStatus.PENDING_RECONCILIATION,
                            changed_by=staff_user,
                            reason=hist_reason,
                        )
                    else:
                        order.refresh_from_db(fields=['order_status'])
                        fresh_status = order.order_status
                        if fresh_status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED):
                            order_status_updated = False
                            new_status = fresh_status
                        elif fresh_status == OrderStatus.PENDING_RECONCILIATION:
                            order_status_updated = False
                            new_status = OrderStatus.PENDING_RECONCILIATION
                        else:
                            raise ValidationError(
                                f"Đơn hàng đã được chuyển sang trạng thái '{order.get_order_status_display()}' bởi thao tác đồng thời khác. Không thể mở đối soát.",
                                code='order_state_conflict'
                            )
                elif order is not None and order.order_status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED):
                    order_status_updated = False
                    new_status = order.order_status
                elif order is not None and order.order_status == OrderStatus.PENDING_RECONCILIATION:
                    order_status_updated = False
                    new_status = OrderStatus.PENDING_RECONCILIATION
                elif order is not None:
                    raise ValidationError(
                        f"Không thể mở hồ sơ đối soát cho đơn hàng ở trạng thái '{order.get_order_status_display()}'.",
                        code='order_state_conflict'
                    )

                reconciliation = PaymentReconciliation(
                    payment=p,
                    transaction=tx_obj,
                    reason_code=cleaned_reason,
                    status=ReconciliationStatus.OPEN,
                    handled_by=staff_user,
                    notes=str(notes).strip() if notes else "",
                    resolved_at=None,
                    escalated_at=None,
                )
                if created_at is not None:
                    reconciliation.created_at = created_at
                reconciliation.save()

                sla_due = calculate_sla_due_at(reconciliation.created_at, calendar_config=calendar_config)
                if sla_due is not None:
                    reconciliation.sla_due_at = sla_due
                    reconciliation.save(update_fields=['sla_due_at'])

                return ReconciliationResult(
                    reconciliation=reconciliation,
                    created=True,
                    already_open=False,
                    order_status_updated=order_status_updated,
                    old_order_status=old_status,
                    new_order_status=new_status,
                )

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

        except IntegrityError:
            existing = PaymentReconciliation.objects.filter(
                payment_id=payment_id,
                status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED],
            ).first()
            if existing:
                ord_st = existing.payment.order.order_status if existing.payment.order else ''
                return ReconciliationResult(
                    reconciliation=existing,
                    created=False,
                    already_open=True,
                    order_status_updated=False,
                    old_order_status=ord_st,
                    new_order_status=ord_st,
                )
            raise

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


class EscalationResult(NamedTuple):
    as_of: Any
    escalated_count: int
    escalated_ids: list
    dry_run: bool


def escalate_overdue_reconciliations(
    as_of=None,
    dry_run: bool = False,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> EscalationResult:
    if as_of is None:
        eval_time = timezone.now()
    elif isinstance(as_of, str):
        eval_time = datetime.fromisoformat(as_of)
        if timezone.is_naive(eval_time):
            eval_time = timezone.make_aware(eval_time, timezone.get_current_timezone())
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            eval_time = timezone.make_aware(as_of, timezone.get_current_timezone())
        else:
            eval_time = as_of
    else:
        raise ValidationError("Tham số as_of không hợp lệ. Phải là datetime hoặc chuỗi ISO 8601.")

    base_qs = PaymentReconciliation.objects.filter(
        status=ReconciliationStatus.OPEN,
        sla_due_at__isnull=False,
        sla_due_at__lte=eval_time,
    ).order_by('sla_due_at', 'id')

    candidate_ids = list(base_qs.values_list('id', flat=True))

    if dry_run:
        return EscalationResult(
            as_of=eval_time,
            escalated_count=len(candidate_ids),
            escalated_ids=candidate_ids,
            dry_run=True,
        )

    if not candidate_ids:
        return EscalationResult(
            as_of=eval_time,
            escalated_count=0,
            escalated_ids=[],
            dry_run=False,
        )

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                escalated_ids = []
                for case in PaymentReconciliation.objects.select_for_update().filter(
                    id__in=candidate_ids,
                    status=ReconciliationStatus.OPEN,
                    sla_due_at__lte=eval_time,
                ):
                    rows = PaymentReconciliation.objects.filter(
                        pk=case.pk,
                        status=ReconciliationStatus.OPEN,
                        sla_due_at__lte=eval_time,
                    ).update(
                        status=ReconciliationStatus.ESCALATED,
                        escalated_at=eval_time,
                    )
                    if rows == 1:
                        escalated_ids.append(case.pk)

                return EscalationResult(
                    as_of=eval_time,
                    escalated_count=len(escalated_ids),
                    escalated_ids=escalated_ids,
                    dry_run=False,
                )

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


class ResolveReconciliationResult(NamedTuple):
    reconciliation: PaymentReconciliation
    order: Order
    payment: Payment
    released_reservations_count: int
    released_coupon: bool
    already_resolved: bool = False


def resolve_expired_reconciliation(
    reconciliation_id: int,
    admin_user,
    audit_note: str,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ResolveReconciliationResult:
    if not is_admin(admin_user):
        raise PermissionDenied("Chỉ Quản trị viên (Admin) mới có thẩm quyền giải quyết đóng hồ sơ đối soát hết hạn.")

    if not audit_note or len(str(audit_note).strip()) < 5:
        raise ValidationError({
            'audit_note': ValidationError(
                "Ghi chú kiểm toán rà soát sao kê bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        })
    cleaned_note = str(audit_note).strip()

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                now = timezone.now()

                case = PaymentReconciliation.objects.select_for_update().select_related(
                    'payment', 'payment__order'
                ).filter(pk=reconciliation_id).first()

                if not case:
                    raise ValidationError("Hồ sơ đối soát không tồn tại.", code='reconciliation_not_found')

                payment = case.payment
                order = payment.order if payment else None

                if not payment or not order:
                    raise ValidationError(
                        "Dữ liệu thanh toán hoặc đơn hàng liên kết không tồn tại.",
                        code='invalid_reconciliation_target'
                    )

                if case.status == ReconciliationStatus.RESOLVED:
                    return ResolveReconciliationResult(
                        reconciliation=case,
                        order=order,
                        payment=payment,
                        released_reservations_count=0,
                        released_coupon=False,
                        already_resolved=True,
                    )

                if case.status not in [ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]:
                    raise ValidationError(
                        f"Hồ sơ đối soát đang ở trạng thái '{case.get_status_display()}', không thể giải quyết.",
                        code='ineligible_case_status'
                    )

                if payment.method != PaymentMethod.QR:
                    raise ValidationError(
                        "Chỉ áp dụng giải quyết đóng hồ sơ hết hạn cho đơn hàng thanh toán qua mã QR.",
                        code='not_qr_payment'
                    )

                if not payment.expires_at or now < payment.expires_at:
                    raise ValidationError(
                        "Thời hạn thanh toán của đơn hàng chưa hết hạn. Không thể đóng hồ sơ theo diện không chuyển tiền.",
                        code='not_expired'
                    )

                if order.order_status == OrderStatus.CANCELLED:
                    raise ValidationError(
                        "Đơn hàng này đã bị hủy trước đó. Không thể thay đổi hay khôi phục đơn đã hủy qua thao tác này.",
                        code='order_already_cancelled'
                    )

                if order.order_status not in [OrderStatus.PENDING_PAYMENT, OrderStatus.PENDING_RECONCILIATION]:
                    raise ValidationError(
                        f"Trạng thái đơn hàng hiện tại ({order.get_order_status_display()}) không đủ điều kiện đóng hồ sơ theo diện hết hạn.",
                        code='ineligible_order_status'
                    )

                if payment.status not in [PaymentStatus.UNPAID, PaymentStatus.PENDING_VERIFY]:
                    raise ValidationError(
                        f"Trạng thái thanh toán hiện tại ({payment.get_status_display()}) không đủ điều kiện đóng hồ sơ theo diện hết hạn.",
                        code='ineligible_payment_status'
                    )

                if case.transaction_id is not None:
                    raise ValidationError(
                        "Hồ sơ đối soát có chứng từ giao dịch ngân hàng liên kết. Cần đối soát và xử lý riêng.",
                        code='has_transaction_evidence'
                    )

                if payment.transactions.filter(verification_status='VERIFIED').exists():
                    raise ValidationError(
                        "Thanh toán này đã có giao dịch ngân hàng được xác minh. Không thể đóng theo diện không nhận được tiền.",
                        code='has_verified_transaction'
                    )

                if payment.transactions.exists():
                    raise ValidationError(
                        "Đơn hàng có bản ghi giao dịch ngân hàng phát sinh. Cần đối soát và xử lý riêng.",
                        code='has_bank_transactions'
                    )

                if case.reason_code != ReconciliationReason.UNCLEAR_OR_EXPIRED:
                    raise ValidationError(
                        f"Hồ sơ đối soát có mã lý do '{case.get_reason_code_display()}' không thuộc diện đóng do không chuyển khoản hợp lệ. "
                        f"Các trường hợp chênh lệch hoặc khiếu nại (bao gồm chuyển sai nội dung WRONG_REFERENCE) phải xử lý đối soát riêng.",
                        code='ineligible_reason_code'
                    )

                order_items = list(order.items.select_for_update().all())
                if not order_items:
                    raise ValidationError(
                        "Đơn hàng không có dòng sản phẩm nào để giải phóng giữ chỗ.",
                        code='order_items_missing'
                    )

                verified_reservations = []
                for item in order_items:
                    item_reservations = list(
                        InventoryReservation.objects.select_for_update().filter(order_item=item)
                    )
                    if len(item_reservations) == 0:
                        raise ValidationError(
                            f"Dòng đơn hàng #{item.pk} (biến thể #{item.variant_id}) thiếu bản ghi giữ chỗ tồn kho (InventoryReservation).",
                            code='missing_inventory_reservation'
                        )
                    if len(item_reservations) > 1:
                        raise ValidationError(
                            f"Dòng đơn hàng #{item.pk} có số lượng bản ghi giữ chỗ không hợp lệ ({len(item_reservations)}), kỳ vọng chính xác 1.",
                            code='inconsistent_inventory_reservation'
                        )

                    res = item_reservations[0]
                    if res.status != InventoryReservationStatus.ACTIVE:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ #{res.pk} của dòng đơn hàng #{item.pk} không ở trạng thái ACTIVE (hiện tại: {res.status}).",
                            code='inactive_inventory_reservation'
                        )
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ #{res.pk} có biến thể (#{res.variant_id}) không khớp với biến thể dòng đơn hàng (#{item.variant_id}).",
                            code='mismatched_reservation_variant'
                        )
                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ #{res.pk} có số lượng ({res.quantity}) không khớp với số lượng dòng đơn hàng ({item.quantity}).",
                            code='mismatched_reservation_quantity'
                        )

                    inv = Inventory.objects.select_for_update().filter(variant_id=res.variant_id).first()
                    if not inv or inv.reserved < res.quantity:
                        raise ValidationError(
                            f"Không thể giải phóng giữ chỗ cho biến thể #{res.variant_id}: "
                            f"số lượng tồn đã giữ ({inv.reserved if inv else 0}) không đủ để giải phóng {res.quantity}.",
                            code='inventory_counter_inconsistent'
                        )
                    verified_reservations.append((res, inv))

                all_order_res_count = InventoryReservation.objects.filter(order_item__order=order).count()
                if all_order_res_count != len(order_items):
                    raise ValidationError(
                        f"Tổng số bản ghi giữ chỗ của đơn hàng ({all_order_res_count}) không khớp với số dòng đơn hàng ({len(order_items)}).",
                        code='inconsistent_inventory_reservation'
                    )

                order_discount = order.discount_total if order.discount_total is not None else Decimal('0.00')
                verified_coupon_usage = None
                verified_coupon = None

                coupon_usages = list(CouponUsage.objects.select_for_update().filter(order=order))
                if len(coupon_usages) > 1:
                    raise ValidationError(
                        f"Tồn tại nhiều hơn một bản ghi giữ chỗ mã giảm giá gắn với đơn hàng ({len(coupon_usages)} bản ghi).",
                        code='inconsistent_coupon_usage'
                    )

                if coupon_usages:
                    coupon_usage = coupon_usages[0]
                    if coupon_usage.order_id != order.id:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ mã giảm giá #{coupon_usage.pk} không thuộc về đơn hàng #{order.id}.",
                            code='inconsistent_coupon_usage'
                        )
                    if coupon_usage.status != CouponUsageStatus.RESERVED:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ mã giảm giá #{coupon_usage.pk} không ở trạng thái RESERVED (hiện tại: {coupon_usage.status}).",
                            code='inconsistent_coupon_usage_status'
                        )
                    if not coupon_usage.coupon_id:
                        raise ValidationError(
                            "Bản ghi CouponUsage thiếu thông tin mã giảm giá (Coupon).",
                            code='inconsistent_coupon_usage'
                        )
                    coupon = Coupon.objects.select_for_update().filter(pk=coupon_usage.coupon_id).first()
                    if not coupon:
                        raise ValidationError(
                            "Không tìm thấy mã giảm giá tương ứng với bản ghi giữ chỗ.",
                            code='inconsistent_coupon_usage'
                        )
                    if coupon.reserved_count <= 0:
                        raise ValidationError(
                            f"Hạn ngạch giữ chỗ của mã giảm giá {coupon.code} không hợp lệ (reserved_count={coupon.reserved_count}).",
                            code='coupon_counter_inconsistent'
                        )
                    usage_discount = coupon_usage.discount_amount if coupon_usage.discount_amount is not None else Decimal('0.00')
                    if usage_discount != order_discount:
                        raise ValidationError(
                            f"Số tiền giảm giá trên CouponUsage ({usage_discount}) không khớp với tổng giảm giá của đơn hàng ({order_discount}).",
                            code='mismatched_coupon_discount'
                        )

                    verified_coupon_usage = coupon_usage
                    verified_coupon = coupon
                else:
                    if order_discount > Decimal('0.00'):
                        raise ValidationError(
                            f"Đơn hàng ghi nhận mức giảm giá voucher {order_discount:,.0f} VNĐ nhưng thiếu bản ghi giữ chỗ CouponUsage.",
                            code='missing_coupon_usage'
                        )

                prev_order_status = order.order_status
                order_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=prev_order_status
                ).update(
                    order_status=OrderStatus.EXPIRED
                )
                if order_updated != 1:
                    raise ValidationError(
                        "Trạng thái đơn hàng đã bị thay đổi đồng thời bởi thao tác khác.",
                        code='concurrent_order_conflict'
                    )
                order.order_status = OrderStatus.EXPIRED

                prev_payment_status = payment.status
                payment_updated = Payment.objects.filter(
                    pk=payment.pk,
                    status=prev_payment_status
                ).update(
                    status=PaymentStatus.EXPIRED
                )
                if payment_updated != 1:
                    raise ValidationError(
                        "Trạng thái thanh toán đã bị thay đổi đồng thời bởi thao tác khác.",
                        code='concurrent_payment_conflict'
                    )
                payment.status = PaymentStatus.EXPIRED

                OrderStatusHistory.objects.create(
                    order=order,
                    old_status=prev_order_status,
                    new_status=OrderStatus.EXPIRED,
                    changed_by=admin_user,
                    reason=f"Đóng hồ sơ đối soát #{case.pk} (xác nhận không có chuyển khoản hợp lệ): {cleaned_note}",
                    changed_at=now,
                )

                released_reservations_count = 0
                for res, inv in verified_reservations:
                    inv_updated = Inventory.objects.filter(
                        pk=inv.pk,
                        reserved__gte=res.quantity
                    ).update(
                        reserved=F('reserved') - res.quantity
                    )
                    if inv_updated != 1:
                        raise ValidationError("Xung đột cập nhật số lượng tồn kho đã giữ.", code='inventory_update_failed')

                    res_updated = InventoryReservation.objects.filter(
                        pk=res.pk,
                        status=InventoryReservationStatus.ACTIVE
                    ).update(
                        status=InventoryReservationStatus.RELEASED,
                        finished_at=now,
                    )
                    if res_updated != 1:
                        raise ValidationError("Xung đột cập nhật trạng thái dòng giữ hàng.", code='reservation_update_failed')

                    released_reservations_count += 1

                released_coupon = False
                if verified_coupon_usage and verified_coupon:
                    coupon_updated = Coupon.objects.filter(
                        pk=verified_coupon.pk,
                        reserved_count__gt=0
                    ).update(
                        reserved_count=F('reserved_count') - 1
                    )
                    if coupon_updated != 1:
                        raise ValidationError("Xung đột cập nhật hạn ngạch mã giảm giá.", code='coupon_update_failed')

                    usage_updated = CouponUsage.objects.filter(
                        pk=verified_coupon_usage.pk,
                        status=CouponUsageStatus.RESERVED
                    ).update(
                        status=CouponUsageStatus.RELEASED,
                        finalized_at=now,
                    )
                    if usage_updated != 1:
                        raise ValidationError("Xung đột cập nhật lượt sử dụng mã giảm giá.", code='coupon_usage_update_failed')

                    released_coupon = True

                audit_entry = f"[Admin {admin_user.username} {now.strftime('%H:%M:%S %d/%m/%Y')}]: Xác nhận không có chuyển khoản hợp lệ. {cleaned_note}"
                updated_notes = f"{case.notes}\n{audit_entry}".strip() if case.notes else audit_entry

                case_updated = PaymentReconciliation.objects.filter(
                    pk=case.pk,
                    status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]
                ).update(
                    status=ReconciliationStatus.RESOLVED,
                    resolved_at=now,
                    handled_by=admin_user,
                    notes=updated_notes,
                )
                if case_updated != 1:
                    raise ValidationError("Trạng thái hồ sơ đối soát đã bị thay đổi đồng thời.", code='concurrent_case_conflict')

                case.status = ReconciliationStatus.RESOLVED
                case.resolved_at = now
                case.handled_by = admin_user
                case.notes = updated_notes

                return ResolveReconciliationResult(
                    reconciliation=case,
                    order=order,
                    payment=payment,
                    released_reservations_count=released_reservations_count,
                    released_coupon=released_coupon,
                    already_resolved=False,
                )

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


class ResolveValidTransferResult(NamedTuple):
    reconciliation: PaymentReconciliation
    order: Order
    payment: Payment
    transaction: PaymentTransaction
    finalized_coupon: bool = False
    already_resolved: bool = False


def resolve_valid_transfer_reconciliation(
    reconciliation_id: int,
    admin_user,
    observed_recipient_account: str,
    observed_amount,
    observed_transfer_reference: str,
    bank_transaction_ref: str,
    received_at,
    audit_note: str,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ResolveValidTransferResult:
    if not is_admin(admin_user):
        raise PermissionDenied("Chỉ Quản trị viên (Admin) mới có thẩm quyền giải quyết hồ sơ đối soát chuyển khoản hợp lệ.")

    if not audit_note or len(str(audit_note).strip()) < 5:
        raise ValidationError({
            'audit_note': ValidationError(
                "Ghi chú kiểm toán đối soát bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        })
    cleaned_note = str(audit_note).strip()

    if not bank_transaction_ref or not str(bank_transaction_ref).strip():
        raise ValidationError({
            'bank_transaction_ref': ValidationError(
                "Mã giao dịch ngân hàng không được để trống.",
                code='bank_transaction_ref_required'
            )
        })
    cleaned_bank_ref = str(bank_transaction_ref).strip().upper()

    if isinstance(observed_amount, (bool, float)):
        raise ValidationError({
            'observed_amount': ValidationError(
                "Số tiền thực nhận không được sử dụng kiểu boolean hoặc float.",
                code='invalid_amount'
            )
        })

    try:
        amount_dec = Decimal(str(observed_amount))
    except Exception:
        raise ValidationError({
            'observed_amount': ValidationError(
                "Số tiền thực nhận không đúng định dạng số tiền hợp lệ.",
                code='invalid_amount'
            )
        })

    if not _is_finite_decimal(amount_dec) or amount_dec < Decimal('0.00'):
        raise ValidationError({
            'observed_amount': ValidationError(
                "Số tiền thực nhận phải là số hữu hạn lớn hơn hoặc bằng 0.",
                code='invalid_amount'
            )
        })

    if received_at is None:
        raise ValidationError({
            'received_at': ValidationError(
                "Thời điểm nhận tiền thực tế từ ngân hàng không được để trống.",
                code='received_at_required'
            )
        })

    if isinstance(received_at, str):
        from django.utils.dateparse import parse_datetime
        parsed_dt = parse_datetime(received_at)
        if parsed_dt is None:
            raise ValidationError({
                'received_at': ValidationError(
                    "Thời điểm nhận tiền thực tế không đúng định dạng ngày giờ hợp lệ.",
                    code='invalid_received_at'
                )
            })
        received_at = parsed_dt

    if not isinstance(received_at, datetime):
        raise ValidationError({
            'received_at': ValidationError(
                "Thời điểm nhận tiền thực tế không đúng định dạng ngày giờ hợp lệ.",
                code='invalid_received_at'
            )
        })

    if timezone.is_naive(received_at):
        received_at = timezone.make_aware(received_at, timezone.get_current_timezone())

    now_ts = timezone.now()
    if received_at > now_ts:
        raise ValidationError({
            'received_at': ValidationError(
                f"Thời điểm nhận tiền thực tế ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) không được ở tương lai so với hiện tại ({now_ts.strftime('%H:%M:%S %d/%m/%Y')}).",
                code='future_received_at'
            )
        })

    cleaned_observed_acc = re.sub(r'[\s\-]', '', str(observed_recipient_account or '').strip())
    if not cleaned_observed_acc:
        raise ValidationError({
            'observed_recipient_account': ValidationError(
                "Tài khoản nhận tiền thực tế không được để trống.",
                code='recipient_account_required'
            )
        })

    cleaned_content = str(observed_transfer_reference or '').strip()
    if not cleaned_content:
        raise ValidationError({
            'observed_transfer_reference': ValidationError(
                "Nội dung chuyển khoản thực tế không được để trống.",
                code='transfer_content_required'
            )
        })

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                now = timezone.now()

                case = PaymentReconciliation.objects.select_for_update().select_related(
                    'payment', 'payment__order', 'transaction'
                ).filter(pk=reconciliation_id).first()

                if not case:
                    raise ValidationError("Hồ sơ đối soát không tồn tại.", code='reconciliation_not_found')

                payment = case.payment
                order = payment.order if payment else None

                if not payment or not order:
                    raise ValidationError(
                        "Dữ liệu thanh toán hoặc đơn hàng liên kết không tồn tại.",
                        code='invalid_reconciliation_target'
                    )

                if case.status == ReconciliationStatus.RESOLVED:
                    if not case.transaction:
                        raise ValidationError(
                            "Hồ sơ đối soát đã được giải quyết trước đó với kết luận không có chuyển khoản hợp lệ.",
                            code='case_already_resolved'
                        )

                    linked_tx = case.transaction
                    norm_tx_ref = (linked_tx.bank_transaction_ref or '').strip().upper()

                    if norm_tx_ref != cleaned_bank_ref:
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Hồ sơ đối soát đã được giải quyết trước đó với mã giao dịch ngân hàng '{linked_tx.bank_transaction_ref}'. "
                                f"Mã giao dịch gửi lên '{cleaned_bank_ref}' không khớp với chứng từ đã xác nhận.",
                                code='conflicting_bank_reference'
                            )
                        })

                    if (
                        linked_tx.received_amount != amount_dec or
                        linked_tx.received_at != received_at or
                        linked_tx.recipient_account_last4 != cleaned_observed_acc[-4:] or
                        (linked_tx.transfer_content or '').strip() != cleaned_content[:255].strip()
                    ):
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Chứng từ gửi lên mâu thuẫn với chứng từ đã lưu của giao dịch '{cleaned_bank_ref}'. "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })

                    recipient_snapshot = payment.get_recipient_snapshot() if payment else None
                    if not recipient_snapshot or not isinstance(recipient_snapshot, dict):
                        raise ValidationError(
                            f"Thanh toán #{payment.pk} không có thông tin tài khoản thụ hưởng lưu trữ hợp lệ (thiếu recipient snapshot). "
                            "Không thể đối chiếu xác minh.",
                            code='recipient_snapshot_missing'
                        )
                    stored_account = recipient_snapshot.get('account_number')
                    if not stored_account or not str(stored_account).strip():
                        raise ValidationError(
                            f"Thanh toán #{payment.pk} thiếu số tài khoản thụ hưởng trong snapshot lưu trữ.",
                            code='recipient_snapshot_missing'
                        )
                    cleaned_stored_acc = re.sub(r'[\s\-]', '', str(stored_account).strip())
                    if cleaned_observed_acc != cleaned_stored_acc:
                        raise ValidationError({
                            'observed_recipient_account': ValidationError(
                                f"Tài khoản nhận tiền thực tế ({observed_recipient_account}) không khớp đầy đủ với tài khoản "
                                f"thụ hưởng của đơn hàng ({stored_account}). Khớp 4 số cuối là không đủ (matching last4 alone is insufficient).",
                                code='recipient_account_mismatch'
                            )
                        })

                    if (
                        linked_tx.payment_id != payment.pk or
                        linked_tx.verification_status != 'VERIFIED' or
                        payment.status != PaymentStatus.PAID
                    ):
                        raise ValidationError(
                            f"Kết quả giải quyết trước đó của hồ sơ đối soát #{case.pk} không nhất quán với trạng thái thanh toán xác nhận hợp lệ "
                            f"(Transaction status={linked_tx.verification_status}, Payment status={payment.get_status_display()}). "
                            "Yêu cầu rà soát đối soát độc lập.",
                            code='inconsistent_resolution_outcome'
                        )

                    return ResolveValidTransferResult(
                        reconciliation=case,
                        order=order,
                        payment=payment,
                        transaction=linked_tx,
                        finalized_coupon=False,
                        already_resolved=True,
                    )

                if case.status not in [ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]:
                    raise ValidationError(
                        f"Hồ sơ đối soát đang ở trạng thái '{case.get_status_display()}', không thể giải quyết.",
                        code='ineligible_case_status'
                    )

                if case.transaction_id:
                    linked_tx = case.transaction
                    norm_tx_ref = (linked_tx.bank_transaction_ref or '').strip().upper()
                    if norm_tx_ref != cleaned_bank_ref or linked_tx.payment_id != payment.pk:
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Hồ sơ đối soát #{case.pk} đang được liên kết với giao dịch ngân hàng khác ('{linked_tx.bank_transaction_ref}'). "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })
                    if (
                        linked_tx.received_amount != amount_dec or
                        linked_tx.received_at != received_at or
                        linked_tx.recipient_account_last4 != cleaned_observed_acc[-4:] or
                        (linked_tx.transfer_content or '').strip() != cleaned_content[:255].strip()
                    ):
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Giao dịch ngân hàng liên kết '{cleaned_bank_ref}' có chứng từ mâu thuẫn với thông tin gửi lên. "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })

                if payment.method != PaymentMethod.QR:
                    raise ValidationError(
                        "Chỉ áp dụng xác nhận chuyển khoản hợp lệ cho đơn hàng thanh toán qua mã QR.",
                        code='not_qr_payment'
                    )

                if case.reason_code != ReconciliationReason.UNCLEAR_OR_EXPIRED:
                    raise ValidationError(
                        f"Hồ sơ đối soát có mã lý do '{case.get_reason_code_display()}' chưa rõ quy định giải quyết trực tiếp sang thanh toán hợp lệ theo SRS v2.3. "
                        f"Giữ nguyên hồ sơ chưa giải quyết để báo cáo quyết định nghiệp vụ.",
                        code='ineligible_reason_code'
                    )

                if order.order_status == OrderStatus.CANCELLED:
                    raise ValidationError(
                        "Đơn hàng này đã bị hủy trước đó. Theo BR-PAY-08, không được tự động phục hồi đơn hàng đã hủy.",
                        code='order_already_cancelled'
                    )
                if order.order_status in [OrderStatus.EXPIRED, OrderStatus.DELIVERED]:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái kết thúc ({order.get_order_status_display()}). Không thể xác nhận thanh toán.",
                        code='terminal_order_state'
                    )
                if order.order_status != OrderStatus.PENDING_RECONCILIATION:
                    raise ValidationError(
                        f"Trạng thái đơn hàng hiện tại ({order.get_order_status_display()}) không hợp lệ để giải quyết chuyển khoản trong hạn. "
                        f"Yêu cầu đơn hàng ở trạng thái Chờ đối soát (PENDING_RECONCILIATION).",
                        code='ineligible_order_status'
                    )

                if payment.status not in [PaymentStatus.UNPAID, PaymentStatus.PENDING_VERIFY]:
                    raise ValidationError(
                        f"Trạng thái thanh toán hiện tại ({payment.get_status_display()}) không hợp lệ để xác nhận chuyển khoản.",
                        code='ineligible_payment_status'
                    )

                existing_tx = PaymentTransaction.objects.filter(
                    bank_transaction_ref__iexact=cleaned_bank_ref
                ).exclude(payment_id=payment.pk).first()
                if existing_tx:
                    raise ValidationError({
                        'bank_transaction_ref': ValidationError(
                            f"Mã giao dịch ngân hàng '{cleaned_bank_ref}' đã được ghi nhận cho thanh toán #{existing_tx.payment_id}. "
                            "Không được phép tái sử dụng mã giao dịch để thanh toán nhiều đơn hàng (SRS Bảng 18 & BR-PAY-08).",
                            code='duplicate_bank_transaction'
                        )
                    })

                recipient_snapshot = payment.get_recipient_snapshot()
                if not recipient_snapshot or not isinstance(recipient_snapshot, dict):
                    raise ValidationError(
                        f"Thanh toán #{payment.pk} không có thông tin tài khoản thụ hưởng lưu trữ hợp lệ (thiếu recipient snapshot). "
                        "Không thể đối chiếu xác minh.",
                        code='recipient_snapshot_missing'
                    )
                stored_account = recipient_snapshot.get('account_number')
                if not stored_account or not str(stored_account).strip():
                    raise ValidationError(
                        f"Thanh toán #{payment.pk} thiếu số tài khoản thụ hưởng trong snapshot lưu trữ.",
                        code='recipient_snapshot_missing'
                    )
                cleaned_stored_acc = re.sub(r'[\s\-]', '', str(stored_account).strip())
                if cleaned_observed_acc != cleaned_stored_acc:
                    raise ValidationError({
                        'observed_recipient_account': ValidationError(
                            f"Tài khoản nhận tiền thực tế ({observed_recipient_account}) không khớp đầy đủ với tài khoản "
                            f"thụ hưởng của đơn hàng ({stored_account}). Chứng từ mâu thuẫn.",
                            code='recipient_account_mismatch'
                        )
                    })

                if amount_dec != payment.expected_amount:
                    raise ValidationError({
                        'observed_amount': ValidationError(
                            f"Số tiền thực nhận ({amount_dec:,.0f} VNĐ) không khớp với số tiền cần thanh toán "
                            f"({payment.expected_amount:,.0f} VNĐ). Giao dịch chuyển tiền thiếu/thừa nằm ngoài phạm vi tác vụ này.",
                            code='amount_mismatch'
                        )
                    })

                stored_ref = str(payment.qr_reference or order.order_code or '').strip().upper()
                if not stored_ref:
                    raise ValidationError({
                        'observed_transfer_reference': ValidationError(
                            'Đơn hàng không có mã tham chiếu chuyển khoản hợp lệ.',
                            code='reference_missing'
                        )
                    })
                pattern = rf'(?<![A-Z0-9_-]){re.escape(stored_ref)}(?![A-Z0-9_-])'
                if not re.search(pattern, cleaned_content.upper()):
                    raise ValidationError({
                        'observed_transfer_reference': ValidationError(
                            f"Nội dung chuyển khoản '{observed_transfer_reference}' không khớp với mã tham chiếu của đơn hàng ({stored_ref}). Chứng từ mâu thuẫn.",
                            code='reference_mismatch'
                        )
                    })

                if payment.expires_at is None:
                    raise ValidationError({
                        'received_at': ValidationError(
                            f"Thanh toán #{payment.pk} không có thời hạn thanh toán lưu trữ (expires_at is None).",
                            code='missing_expiry'
                        )
                    })
                if order.created_at is not None and received_at < order.created_at:
                    raise ValidationError({
                        'received_at': ValidationError(
                            f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) trước thời điểm tạo đơn hàng "
                            f"({order.created_at.strftime('%H:%M:%S %d/%m/%Y')}). Giao dịch không hợp lệ.",
                            code='receipt_before_order_created'
                        )
                    })
                if received_at >= payment.expires_at:
                    raise ValidationError({
                        'received_at': ValidationError(
                            f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) vượt quá hoặc chạm mốc "
                            f"thời hạn thanh toán ({payment.expires_at.strftime('%H:%M:%S %d/%m/%Y')}). "
                            "Giao dịch muộn không thuộc phạm vi giải quyết thanh toán trong hạn (BR-PAY-08).",
                            code='late_receipt_after_deadline'
                        )
                    })

                order_items = list(order.items.select_for_update().all())
                if not order_items:
                    raise ValidationError(
                        "Đơn hàng không có dòng sản phẩm nào.",
                        code='order_items_missing'
                    )
                for item in order_items:
                    item_res = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
                    if len(item_res) != 1:
                        raise ValidationError(
                            f"Dòng đơn hàng #{item.pk} có số lượng bản ghi giữ chỗ không hợp lệ ({len(item_res)}), kỳ vọng chính xác 1.",
                            code='inconsistent_inventory_reservation'
                        )
                    res = item_res[0]
                    if res.status != InventoryReservationStatus.ACTIVE:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ #{res.pk} của dòng đơn hàng #{item.pk} không ở trạng thái ACTIVE (hiện tại: {res.status}).",
                            code='inactive_inventory_reservation'
                        )
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ #{res.pk} có biến thể (#{res.variant_id}) không khớp với dòng đơn hàng (#{item.variant_id}).",
                            code='mismatched_reservation_variant'
                        )
                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ #{res.pk} có số lượng ({res.quantity}) không khớp với số lượng dòng đơn hàng ({item.quantity}).",
                            code='mismatched_reservation_quantity'
                        )

                all_res_count = InventoryReservation.objects.filter(order_item__order=order).count()
                if all_res_count != len(order_items):
                    raise ValidationError(
                        f"Tổng số bản ghi giữ chỗ ({all_res_count}) không khớp số dòng đơn hàng ({len(order_items)}).",
                        code='inconsistent_inventory_reservation'
                    )

                order_discount = order.discount_total if order.discount_total is not None else Decimal('0.00')
                verified_cu = None
                verified_c = None
                coupon_usages = list(CouponUsage.objects.select_for_update().filter(order=order))
                if len(coupon_usages) > 1:
                    raise ValidationError(
                        f"Tồn tại nhiều hơn một bản ghi giữ chỗ coupon ({len(coupon_usages)}).",
                        code='inconsistent_coupon_usage'
                    )
                if coupon_usages:
                    cu = coupon_usages[0]
                    if cu.order_id != order.id:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ mã giảm giá #{cu.pk} không thuộc về đơn hàng #{order.id}.",
                            code='inconsistent_coupon_usage'
                        )
                    if cu.status != CouponUsageStatus.RESERVED:
                        raise ValidationError(
                            f"Bản ghi giữ chỗ mã giảm giá #{cu.pk} không ở trạng thái RESERVED (hiện tại: {cu.status}).",
                            code='inconsistent_coupon_usage_status'
                        )
                    if not cu.coupon_id:
                        raise ValidationError(
                            "Bản ghi CouponUsage thiếu thông tin mã giảm giá (Coupon).",
                            code='inconsistent_coupon_usage'
                        )
                    coupon = Coupon.objects.select_for_update().filter(pk=cu.coupon_id).first()
                    if not coupon:
                        raise ValidationError(
                            "Không tìm thấy mã giảm giá tương ứng với bản ghi giữ chỗ.",
                            code='inconsistent_coupon_usage'
                        )
                    if coupon.reserved_count <= 0:
                        raise ValidationError(
                            f"Hạn ngạch giữ chỗ của mã giảm giá {coupon.code} không hợp lệ (reserved_count={coupon.reserved_count}).",
                            code='coupon_counter_inconsistent'
                        )
                    usage_discount = cu.discount_amount if cu.discount_amount is not None else Decimal('0.00')
                    if usage_discount != order_discount:
                        raise ValidationError(
                            f"Số tiền giảm giá trên CouponUsage ({usage_discount}) không khớp với tổng giảm giá của đơn hàng ({order_discount}).",
                            code='mismatched_coupon_discount'
                        )
                    verified_cu = cu
                    verified_c = coupon
                else:
                    if order_discount > Decimal('0.00'):
                        raise ValidationError(
                            f"Đơn hàng ghi nhận giảm giá {order_discount:,.0f} VNĐ nhưng thiếu bản ghi giữ chỗ CouponUsage.",
                            code='missing_coupon_usage'
                        )

                existing_tx_this_payment = PaymentTransaction.objects.filter(
                    payment=payment,
                    bank_transaction_ref__iexact=cleaned_bank_ref
                ).first()
                if existing_tx_this_payment:
                    if (
                        existing_tx_this_payment.received_amount != amount_dec or
                        existing_tx_this_payment.received_at != received_at or
                        existing_tx_this_payment.recipient_account_last4 != cleaned_observed_acc[-4:] or
                        (existing_tx_this_payment.transfer_content or '').strip() != cleaned_content[:255].strip()
                    ):
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Giao dịch ngân hàng '{cleaned_bank_ref}' đã được ghi nhận cho thanh toán #{payment.pk} "
                                "nhưng có chứng từ mâu thuẫn với thông tin gửi lên (số tiền, thời điểm nhận, thông tin thụ hưởng hoặc nội dung chuyển). "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })
                    tx = existing_tx_this_payment
                    update_fields = []
                    if tx.verification_status != 'VERIFIED':
                        tx.verification_status = 'VERIFIED'
                        update_fields.append('verification_status')
                    if tx.verified_by_id != admin_user.id:
                        tx.verified_by = admin_user
                        update_fields.append('verified_by')
                    if update_fields:
                        tx.save(update_fields=update_fields)
                else:
                    tx = PaymentTransaction(
                        payment=payment,
                        bank_transaction_ref=cleaned_bank_ref,
                        received_amount=amount_dec,
                        received_at=received_at,
                        recipient_account_last4=cleaned_observed_acc[-4:],
                        transfer_content=cleaned_content[:255],
                        verification_status='VERIFIED',
                        verified_by=admin_user,
                    )
                    tx.full_clean()
                    tx.save()

                prev_payment_status = payment.status
                p_updated = Payment.objects.filter(
                    pk=payment.pk,
                    status=prev_payment_status
                ).update(
                    status=PaymentStatus.PAID,
                    verified_by=admin_user,
                    verified_at=now,
                )
                if p_updated != 1:
                    raise ValidationError(
                        "Trạng thái thanh toán đã bị thay đổi đồng thời bởi thao tác khác.",
                        code='concurrent_conflict'
                    )
                payment.status = PaymentStatus.PAID
                payment.verified_by = admin_user
                payment.verified_at = now

                prev_order_status = order.order_status
                o_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=prev_order_status
                ).update(
                    order_status=OrderStatus.PENDING_CONFIRMATION
                )
                if o_updated != 1:
                    raise ValidationError(
                        "Trạng thái đơn hàng đã bị thay đổi đồng thời bởi thao tác khác.",
                        code='concurrent_conflict'
                    )
                order.order_status = OrderStatus.PENDING_CONFIRMATION

                OrderStatusHistory.objects.create(
                    order=order,
                    old_status=prev_order_status,
                    new_status=OrderStatus.PENDING_CONFIRMATION,
                    changed_by=admin_user,
                    reason=(
                        f"Đóng hồ sơ đối soát #{case.pk} (xác nhận chuyển khoản hợp lệ trong hạn: "
                        f"Mã GD {cleaned_bank_ref}, {amount_dec:,.0f} VNĐ): {cleaned_note}"
                    ),
                    changed_at=now,
                )

                finalized_coupon = False
                if verified_cu and verified_c:
                    c_updated = Coupon.objects.filter(
                        pk=verified_c.pk,
                        reserved_count__gt=0
                    ).update(
                        reserved_count=F('reserved_count') - 1,
                        used_count=F('used_count') + 1
                    )
                    if c_updated != 1:
                        raise ValidationError(
                            "Cập nhật hạn ngạch mã giảm giá thất bại. Toàn bộ thao tác được hoàn nguyên.",
                            code='coupon_update_failed'
                        )

                    cu_updated = CouponUsage.objects.filter(
                        pk=verified_cu.pk,
                        status=CouponUsageStatus.RESERVED
                    ).update(
                        status=CouponUsageStatus.USED,
                        finalized_at=now,
                    )
                    if cu_updated != 1:
                        raise ValidationError(
                            "Cập nhật lượt sử dụng mã giảm giá thất bại. Toàn bộ thao tác được hoàn nguyên.",
                            code='coupon_usage_update_failed'
                        )
                    finalized_coupon = True


                audit_entry = (
                    f"[Admin {admin_user.username} {now.strftime('%H:%M:%S %d/%m/%Y')}]: "
                    f"Xác nhận chuyển khoản hợp lệ trong hạn (Mã GD NH: {cleaned_bank_ref}, "
                    f"Số tiền: {amount_dec:,.0f} VNĐ, Thời điểm nhận: {received_at.strftime('%H:%M:%S %d/%m/%Y')}). "
                    f"{cleaned_note}"
                )
                updated_notes = f"{case.notes}\n{audit_entry}".strip() if case.notes else audit_entry

                case_updated = PaymentReconciliation.objects.filter(
                    pk=case.pk,
                    status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]
                ).update(
                    status=ReconciliationStatus.RESOLVED,
                    resolved_at=now,
                    handled_by=admin_user,
                    transaction=tx,
                    notes=updated_notes,
                )
                if case_updated != 1:
                    raise ValidationError(
                        "Trạng thái hồ sơ đối soát đã bị thay đổi đồng thời.",
                        code='concurrent_conflict'
                    )
                case.status = ReconciliationStatus.RESOLVED
                case.resolved_at = now
                case.handled_by = admin_user
                case.transaction = tx
                case.notes = updated_notes

                return ResolveValidTransferResult(
                    reconciliation=case,
                    order=order,
                    payment=payment,
                    transaction=tx,
                    finalized_coupon=finalized_coupon,
                    already_resolved=False,
                )

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


class ResolveTerminalTransferResult(NamedTuple):
    reconciliation: PaymentReconciliation
    order: Order
    payment: Payment
    transaction: PaymentTransaction
    already_resolved: bool = False


def resolve_terminal_transfer_reconciliation(
    reconciliation_id: int,
    admin_user,
    observed_recipient_account: str,
    observed_amount,
    observed_transfer_reference: str,
    bank_transaction_ref: str,
    received_at,
    audit_note: str,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ResolveTerminalTransferResult:
    if not is_admin(admin_user):
        raise PermissionDenied("Chỉ Quản trị viên (Admin) mới có thẩm quyền giải quyết hồ sơ đối soát chuyển khoản cho đơn hàng kết thúc.")

    if not audit_note or len(str(audit_note).strip()) < 5:
        raise ValidationError({
            'audit_note': ValidationError(
                "Ghi chú kiểm toán đối soát bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        })
    cleaned_note = str(audit_note).strip()

    if not bank_transaction_ref or not str(bank_transaction_ref).strip():
        raise ValidationError({
            'bank_transaction_ref': ValidationError(
                "Mã giao dịch ngân hàng không được để trống.",
                code='bank_transaction_ref_required'
            )
        })
    cleaned_bank_ref = str(bank_transaction_ref).strip().upper()

    if isinstance(observed_amount, (bool, float)):
        raise ValidationError({
            'observed_amount': ValidationError(
                "Số tiền thực nhận không được sử dụng kiểu boolean hoặc float.",
                code='invalid_amount'
            )
        })

    try:
        amount_dec = Decimal(str(observed_amount))
    except Exception:
        raise ValidationError({
            'observed_amount': ValidationError(
                "Số tiền thực nhận không đúng định dạng số tiền hợp lệ.",
                code='invalid_amount'
            )
        })

    if not _is_finite_decimal(amount_dec) or amount_dec < Decimal('0.00'):
        raise ValidationError({
            'observed_amount': ValidationError(
                "Số tiền thực nhận phải là số hữu hạn lớn hơn hoặc bằng 0.",
                code='invalid_amount'
            )
        })

    if received_at is None:
        raise ValidationError({
            'received_at': ValidationError(
                "Thời điểm nhận tiền thực tế từ ngân hàng không được để trống.",
                code='received_at_required'
            )
        })

    if isinstance(received_at, str):
        from django.utils.dateparse import parse_datetime
        parsed_dt = parse_datetime(received_at)
        if parsed_dt is None:
            raise ValidationError({
                'received_at': ValidationError(
                    "Thời điểm nhận tiền thực tế không đúng định dạng ngày giờ hợp lệ.",
                    code='invalid_received_at'
                )
            })
        received_at = parsed_dt

    if not isinstance(received_at, datetime):
        raise ValidationError({
            'received_at': ValidationError(
                "Thời điểm nhận tiền thực tế không đúng định dạng ngày giờ hợp lệ.",
                code='invalid_received_at'
            )
        })

    if timezone.is_naive(received_at):
        received_at = timezone.make_aware(received_at, timezone.get_current_timezone())

    now_ts = timezone.now()
    if received_at > now_ts:
        raise ValidationError({
            'received_at': ValidationError(
                f"Thời điểm nhận tiền thực tế ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) không được ở tương lai so với hiện tại ({now_ts.strftime('%H:%M:%S %d/%m/%Y')}).",
                code='future_received_at'
            )
        })

    cleaned_observed_acc = re.sub(r'[\s\-]', '', str(observed_recipient_account or '').strip())
    if not cleaned_observed_acc:
        raise ValidationError({
            'observed_recipient_account': ValidationError(
                "Tài khoản nhận tiền thực tế không được để trống.",
                code='recipient_account_required'
            )
        })

    cleaned_content = str(observed_transfer_reference or '').strip()
    if not cleaned_content:
        raise ValidationError({
            'observed_transfer_reference': ValidationError(
                "Nội dung chuyển khoản thực tế không được để trống.",
                code='transfer_content_required'
            )
        })

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                now = timezone.now()

                case = PaymentReconciliation.objects.select_for_update().select_related(
                    'payment', 'payment__order', 'transaction'
                ).filter(pk=reconciliation_id).first()

                if not case:
                    raise ValidationError("Hồ sơ đối soát không tồn tại.", code='reconciliation_not_found')

                payment = case.payment
                order = payment.order if payment else None

                if not payment or not order:
                    raise ValidationError(
                        "Dữ liệu thanh toán hoặc đơn hàng liên kết không tồn tại.",
                        code='invalid_reconciliation_target'
                    )

                if case.status == ReconciliationStatus.RESOLVED:
                    if not case.transaction:
                        raise ValidationError(
                            "Hồ sơ đối soát đã được giải quyết trước đó mà không có chứng từ giao dịch hợp lệ.",
                            code='case_already_resolved'
                        )

                    linked_tx = case.transaction
                    norm_tx_ref = (linked_tx.bank_transaction_ref or '').strip().upper()

                    if norm_tx_ref != cleaned_bank_ref:
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Hồ sơ đối soát đã được giải quyết trước đó với mã giao dịch ngân hàng '{linked_tx.bank_transaction_ref}'. "
                                f"Mã giao dịch gửi lên '{cleaned_bank_ref}' không khớp với chứng từ đã xác nhận.",
                                code='conflicting_bank_reference'
                            )
                        })

                    if (
                        linked_tx.received_amount != amount_dec or
                        linked_tx.received_at != received_at or
                        linked_tx.recipient_account_last4 != cleaned_observed_acc[-4:] or
                        (linked_tx.transfer_content or '').strip() != cleaned_content[:255].strip()
                    ):
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Chứng từ gửi lên mâu thuẫn với chứng từ đã lưu của giao dịch '{cleaned_bank_ref}'. "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })

                    recipient_snapshot = payment.get_recipient_snapshot() if payment else None
                    if not recipient_snapshot or not isinstance(recipient_snapshot, dict):
                        raise ValidationError(
                            f"Thanh toán #{payment.pk} không có thông tin tài khoản thụ hưởng lưu trữ hợp lệ (thiếu recipient snapshot). "
                            "Không thể đối chiếu xác minh.",
                            code='recipient_snapshot_missing'
                        )
                    stored_account = recipient_snapshot.get('account_number')
                    if not stored_account or not str(stored_account).strip():
                        raise ValidationError(
                            f"Thanh toán #{payment.pk} thiếu số tài khoản thụ hưởng trong snapshot lưu trữ.",
                            code='recipient_snapshot_missing'
                        )
                    cleaned_stored_acc = re.sub(r'[\s\-]', '', str(stored_account).strip())
                    if cleaned_observed_acc != cleaned_stored_acc:
                        raise ValidationError({
                            'observed_recipient_account': ValidationError(
                                f"Tài khoản nhận tiền thực tế ({observed_recipient_account}) không khớp đầy đủ với tài khoản "
                                f"thụ hưởng của đơn hàng ({stored_account}). Khớp 4 số cuối là không đủ (matching last4 alone is insufficient).",
                                code='recipient_account_mismatch'
                            )
                        })

                    if (
                        linked_tx.payment_id != payment.pk or
                        linked_tx.verification_status != 'VERIFIED' or
                        payment.status != PaymentStatus.PENDING_REFUND or
                        order.order_status not in [OrderStatus.EXPIRED, OrderStatus.CANCELLED]
                    ):
                        raise ValidationError(
                            f"Kết quả giải quyết trước đó của hồ sơ đối soát #{case.pk} không nhất quán với trạng thái chờ hoàn tiền "
                            f"(Transaction status={linked_tx.verification_status}, Payment status={payment.get_status_display()}, Order status={order.get_order_status_display()}). "
                            "Yêu cầu rà soát đối soát độc lập.",
                            code='inconsistent_resolution_outcome'
                        )

                    return ResolveTerminalTransferResult(
                        reconciliation=case,
                        order=order,
                        payment=payment,
                        transaction=linked_tx,
                        already_resolved=True,
                    )

                if case.status not in [ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]:
                    raise ValidationError(
                        f"Hồ sơ đối soát đang ở trạng thái '{case.get_status_display()}', không thể giải quyết.",
                        code='ineligible_case_status'
                    )

                if payment.method != PaymentMethod.QR:
                    raise ValidationError(
                        "Chỉ áp dụng xác nhận chuyển khoản cho đơn hàng thanh toán qua mã QR.",
                        code='not_qr_payment'
                    )

                if order.order_status not in [OrderStatus.EXPIRED, OrderStatus.CANCELLED]:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không phải trạng thái kết thúc (EXPIRED hoặc CANCELLED). "
                        "Tác vụ này chỉ áp dụng cho đơn hàng ở trạng thái kết thúc.",
                        code='not_terminal_order'
                    )

                if order.created_at is not None and received_at < order.created_at:
                    raise ValidationError({
                        'received_at': ValidationError(
                            f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) trước thời điểm tạo đơn hàng "
                            f"({order.created_at.strftime('%H:%M:%S %d/%m/%Y')}). Giao dịch không hợp lệ.",
                            code='receipt_before_order_created'
                        )
                    })

                if order.order_status == OrderStatus.EXPIRED:
                    if case.reason_code != ReconciliationReason.UNCLEAR_OR_EXPIRED:
                        raise ValidationError(
                            f"Đơn hàng hết hạn (EXPIRED) chỉ chấp nhận mã lý do đối soát UNCLEAR_OR_EXPIRED, hiện tại là '{case.get_reason_code_display()}'.",
                            code='ineligible_reason_code'
                        )
                    if payment.expires_at is None:
                        raise ValidationError(
                            f"Thanh toán #{payment.pk} không có thời hạn thanh toán lưu trữ (expires_at is None).",
                            code='missing_expiry'
                        )
                    if received_at < payment.expires_at:
                        raise ValidationError({
                            'received_at': ValidationError(
                                f"Thời điểm chuyển khoản ({received_at.strftime('%H:%M:%S %d/%m/%Y')}) trước thời hạn thanh toán "
                                f"({payment.expires_at.strftime('%H:%M:%S %d/%m/%Y')}). Giao dịch trong hạn không thuộc diện xử lý tiền đến muộn cho đơn hết hạn.",
                                code='receipt_within_expiry_for_expired_order'
                            )
                        })
                elif order.order_status == OrderStatus.CANCELLED:
                    if case.reason_code != ReconciliationReason.TRANSFER_AFTER_CANCELLATION:
                        raise ValidationError(
                            f"Đơn hàng đã hủy (CANCELLED) chỉ chấp nhận mã lý do đối soát TRANSFER_AFTER_CANCELLATION, hiện tại là '{case.get_reason_code_display()}'.",
                            code='ineligible_reason_code'
                        )

                if payment.status not in [PaymentStatus.UNPAID, PaymentStatus.PENDING_VERIFY, PaymentStatus.EXPIRED, PaymentStatus.FAILED]:
                    raise ValidationError(
                        f"Trạng thái thanh toán hiện tại ({payment.get_status_display()}) không hợp lệ để xử lý chuyển khoản chờ hoàn tiền.",
                        code='ineligible_payment_status'
                    )

                has_live_inventory_res = InventoryReservation.objects.filter(
                    order_item__order=order,
                    status=InventoryReservationStatus.ACTIVE
                ).exists()
                has_live_coupon_usage = CouponUsage.objects.filter(
                    order=order,
                    status=CouponUsageStatus.RESERVED
                ).exists()
                if has_live_inventory_res or has_live_coupon_usage:
                    raise ValidationError(
                        "Đơn hàng ở trạng thái kết thúc nhưng còn giữ chỗ tồn kho hoặc mã giảm giá đang hoạt động. "
                        "Dữ liệu không nhất quán, từ chối xử lý.",
                        code='inconsistent_live_reservation'
                    )

                recipient_snapshot = payment.get_recipient_snapshot()
                if not recipient_snapshot or not isinstance(recipient_snapshot, dict):
                    raise ValidationError(
                        f"Thanh toán #{payment.pk} không có thông tin tài khoản thụ hưởng lưu trữ hợp lệ (thiếu recipient snapshot). "
                        "Không thể đối chiếu xác minh.",
                        code='recipient_snapshot_missing'
                    )
                stored_account = recipient_snapshot.get('account_number')
                if not stored_account or not str(stored_account).strip():
                    raise ValidationError(
                        f"Thanh toán #{payment.pk} thiếu số tài khoản thụ hưởng trong snapshot lưu trữ.",
                        code='recipient_snapshot_missing'
                    )
                cleaned_stored_acc = re.sub(r'[\s\-]', '', str(stored_account).strip())
                if cleaned_observed_acc != cleaned_stored_acc:
                    raise ValidationError({
                        'observed_recipient_account': ValidationError(
                            f"Tài khoản nhận tiền thực tế ({observed_recipient_account}) không khớp đầy đủ với tài khoản "
                            f"thụ hưởng của đơn hàng ({stored_account}). Khớp 4 số cuối là không đủ (matching last4 alone is insufficient).",
                            code='recipient_account_mismatch'
                        )
                    })

                if amount_dec != payment.expected_amount:
                    raise ValidationError({
                        'observed_amount': ValidationError(
                            f"Số tiền thực nhận ({amount_dec:,.0f} VNĐ) không khớp với số tiền cần thanh toán "
                            f"({payment.expected_amount:,.0f} VNĐ).",
                            code='amount_mismatch'
                        )
                    })

                stored_ref = str(payment.qr_reference or order.order_code or '').strip().upper()
                if not stored_ref:
                    raise ValidationError({
                        'observed_transfer_reference': ValidationError(
                            'Đơn hàng không có mã tham chiếu chuyển khoản hợp lệ.',
                            code='reference_missing'
                        )
                    })
                pattern = rf'(?<![A-Z0-9_-]){re.escape(stored_ref)}(?![A-Z0-9_-])'
                if not re.search(pattern, cleaned_content.upper()):
                    raise ValidationError({
                        'observed_transfer_reference': ValidationError(
                            f"Nội dung chuyển khoản '{observed_transfer_reference}' không khớp với mã tham chiếu của đơn hàng ({stored_ref}). Chứng từ mâu thuẫn.",
                            code='reference_mismatch'
                        )
                    })

                existing_other_tx = PaymentTransaction.objects.filter(
                    bank_transaction_ref__iexact=cleaned_bank_ref
                ).exclude(payment_id=payment.pk).first()
                if existing_other_tx:
                    raise ValidationError({
                        'bank_transaction_ref': ValidationError(
                            f"Mã giao dịch ngân hàng '{cleaned_bank_ref}' đã được ghi nhận cho thanh toán #{existing_other_tx.payment_id}. "
                            "Không được phép tái sử dụng mã giao dịch để thanh toán nhiều đơn hàng (SRS Bảng 18 & BR-PAY-08).",
                            code='duplicate_bank_transaction'
                        )
                    })

                if case.transaction_id:
                    linked_tx = case.transaction
                    norm_tx_ref = (linked_tx.bank_transaction_ref or '').strip().upper()
                    if norm_tx_ref != cleaned_bank_ref or linked_tx.payment_id != payment.pk:
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Hồ sơ đối soát #{case.pk} đang được liên kết với giao dịch ngân hàng khác ('{linked_tx.bank_transaction_ref}'). "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })
                    if (
                        linked_tx.received_amount != amount_dec or
                        linked_tx.received_at != received_at or
                        linked_tx.recipient_account_last4 != cleaned_observed_acc[-4:] or
                        (linked_tx.transfer_content or '').strip() != cleaned_content[:255].strip()
                    ):
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Giao dịch ngân hàng liên kết '{cleaned_bank_ref}' có chứng từ mâu thuẫn với thông tin gửi lên. "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })

                same_payment_tx = PaymentTransaction.objects.filter(
                    payment=payment,
                    bank_transaction_ref__iexact=cleaned_bank_ref
                ).first()

                if same_payment_tx:
                    if (
                        same_payment_tx.received_amount != amount_dec or
                        same_payment_tx.received_at != received_at or
                        same_payment_tx.recipient_account_last4 != cleaned_observed_acc[-4:] or
                        (same_payment_tx.transfer_content or '').strip() != cleaned_content[:255].strip()
                    ):
                        raise ValidationError({
                            'bank_transaction_ref': ValidationError(
                                f"Giao dịch ngân hàng '{cleaned_bank_ref}' đã tồn tại nhưng có chứng từ mâu thuẫn. "
                                "Yêu cầu rà soát đối soát độc lập (review required).",
                                code='review_required'
                            )
                        })
                    tx = same_payment_tx
                    if tx.verification_status != 'VERIFIED':
                        tx.verification_status = 'VERIFIED'
                        tx.verified_by = admin_user
                        tx.save(update_fields=['verification_status', 'verified_by'])
                else:
                    tx = PaymentTransaction.objects.create(
                        payment=payment,
                        bank_transaction_ref=cleaned_bank_ref,
                        received_amount=amount_dec,
                        received_at=received_at,
                        recipient_account_last4=cleaned_observed_acc[-4:],
                        transfer_content=cleaned_content[:255],
                        verification_status='VERIFIED',
                        verified_by=admin_user,
                    )

                payment.status = PaymentStatus.PENDING_REFUND
                payment.verified_by = admin_user
                payment.verified_at = now
                payment.save(update_fields=['status', 'verified_by', 'verified_at'])

                audit_entry = (
                    f"[Admin {admin_user.username} {now.strftime('%H:%M:%S %d/%m/%Y')}]: "
                    f"Xác nhận chuyển khoản cho đơn kết thúc ({order.order_status}) "
                    f"(Mã GD NH: {cleaned_bank_ref}, Số tiền: {amount_dec:,.0f} VNĐ, "
                    f"Thời điểm nhận: {received_at.strftime('%H:%M:%S %d/%m/%Y')}). "
                    f"Chuyển trạng thái thanh toán sang Chờ hoàn tiền (PENDING_REFUND). "
                    f"{cleaned_note}"
                )
                updated_notes = f"{case.notes}\n{audit_entry}".strip() if case.notes else audit_entry

                case_updated = PaymentReconciliation.objects.filter(
                    pk=case.pk,
                    status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]
                ).update(
                    status=ReconciliationStatus.RESOLVED,
                    resolved_at=now,
                    handled_by=admin_user,
                    transaction=tx,
                    notes=updated_notes,
                )
                if case_updated != 1:
                    raise ValidationError(
                        "Trạng thái hồ sơ đối soát đã bị thay đổi đồng thời.",
                        code='concurrent_conflict'
                    )
                case.status = ReconciliationStatus.RESOLVED
                case.resolved_at = now
                case.handled_by = admin_user
                case.transaction = tx
                case.notes = updated_notes

                return ResolveTerminalTransferResult(
                    reconciliation=case,
                    order=order,
                    payment=payment,
                    transaction=tx,
                    already_resolved=False,
                )

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


class RecordExternalRefundResult(NamedTuple):
    reconciliation: PaymentReconciliation
    order: Order
    payment: Payment
    refund: Refund
    already_confirmed: bool = False


def record_external_refund(
    reconciliation_id: int,
    admin_user,
    bank_reference: str,
    refund_amount,
    completed_at,
    audit_note: str,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> RecordExternalRefundResult:
    if not is_admin(admin_user):
        raise PermissionDenied("Chỉ Quản trị viên (Admin) mới có thẩm quyền xác nhận hoàn tiền ngân hàng ngoại bộ.")

    if not audit_note or len(str(audit_note).strip()) < 5:
        raise ValidationError({
            'audit_note': ValidationError(
                "Ghi chú kiểm toán hoàn tiền bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        })
    cleaned_note = str(audit_note).strip()

    if not bank_reference or not str(bank_reference).strip():
        raise ValidationError({
            'bank_reference': ValidationError(
                "Mã giao dịch ngân hàng chuyển hoàn không được để trống.",
                code='bank_reference_required'
            )
        })
    cleaned_bank_ref = str(bank_reference).strip().upper()
    if len(cleaned_bank_ref) > 100:
        raise ValidationError({
            'bank_reference': ValidationError(
                "Mã giao dịch ngân hàng chuyển hoàn không được vượt quá 100 ký tự.",
                code='bank_reference_too_long'
            )
        })

    if isinstance(refund_amount, (bool, float)):
        raise ValidationError({
            'refund_amount': ValidationError(
                "Số tiền hoàn không được sử dụng kiểu boolean hoặc float.",
                code='invalid_refund_amount_type'
            )
        })
    try:
        amount_dec = Decimal(str(refund_amount)) if not isinstance(refund_amount, Decimal) else refund_amount
    except Exception:
        raise ValidationError({
            'refund_amount': ValidationError(
                "Số tiền hoàn không hợp lệ.",
                code='invalid_refund_amount'
            )
        })
    if not _is_finite_decimal(amount_dec) or amount_dec <= Decimal('0.00'):
        raise ValidationError({
            'refund_amount': ValidationError(
                "Số tiền hoàn phải là số Decimal hữu hạn lớn hơn 0.",
                code='refund_amount_must_be_positive'
            )
        })
    if amount_dec.as_tuple().exponent < -2:
        raise ValidationError({
            'refund_amount': ValidationError(
                "Số tiền hoàn không được vượt quá 2 chữ số thập phân.",
                code='refund_amount_max_two_decimals'
            )
        })

    if not completed_at:
        raise ValidationError({
            'completed_at': ValidationError(
                "Thời điểm ngân hàng hoàn tất giao dịch không được để trống.",
                code='completed_at_required'
            )
        })
    if isinstance(completed_at, str):
        try:
            completed_at = datetime.fromisoformat(completed_at)
        except Exception:
            raise ValidationError({
                'completed_at': ValidationError(
                    "Thời điểm ngân hàng hoàn tất không đúng định dạng.",
                    code='invalid_completed_at_format'
                )
            })
    if timezone.is_naive(completed_at):
        completed_at = timezone.make_aware(completed_at, timezone.get_current_timezone())
    now = timezone.now()
    if completed_at > now:
        raise ValidationError({
            'completed_at': ValidationError(
                f"Thời điểm ngân hàng hoàn tất ({completed_at.strftime('%H:%M:%S %d/%m/%Y')}) không được ở tương lai so với hiện tại ({now.strftime('%H:%M:%S %d/%m/%Y')}).",
                code='future_completed_at'
            )
        })

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                case = PaymentReconciliation.objects.select_for_update().select_related(
                    'payment', 'payment__order', 'transaction'
                ).get(pk=reconciliation_id)
                payment = case.payment
                order = payment.order if payment else None

                if not payment or not order:
                    raise ValidationError({
                        'reconciliation': ValidationError(
                            "Hồ sơ đối soát không gắn liền với bản ghi thanh toán hoặc đơn hàng hợp lệ.",
                            code='missing_payment_or_order'
                        )
                    })

                if payment.method != PaymentMethod.QR:
                    raise ValidationError({
                        'payment': ValidationError(
                            "Nghiệp vụ này chỉ áp dụng cho phương thức thanh toán chuyển khoản qua mã QR.",
                            code='unsupported_payment_method'
                        )
                    })

                if order.order_status not in [OrderStatus.EXPIRED, OrderStatus.CANCELLED]:
                    raise ValidationError({
                        'order': ValidationError(
                            f"Đơn hàng đang ở trạng thái {order.order_status}, không thuộc diện đơn kết thúc (EXPIRED hoặc CANCELLED).",
                            code='order_not_terminal'
                        )
                    })

                if (order.order_status == OrderStatus.EXPIRED and case.reason_code != ReconciliationReason.UNCLEAR_OR_EXPIRED) or                    (order.order_status == OrderStatus.CANCELLED and case.reason_code != ReconciliationReason.TRANSFER_AFTER_CANCELLATION):
                    raise ValidationError({
                        'reconciliation': ValidationError(
                            "Cặp trạng thái đơn hàng và lý do đối soát không thuộc diện xử lý hoàn tiền đơn kết thúc.",
                            code='ineligible_terminal_reconciliation'
                        )
                    })

                if case.status != ReconciliationStatus.RESOLVED:
                    raise ValidationError({
                        'reconciliation': ValidationError(
                            f"Hồ sơ đối soát đang ở trạng thái {case.status}. Bắt buộc phải ở trạng thái RESOLVED sau quy trình đối soát tiền vào trước khi xác nhận hoàn tiền.",
                            code='reconciliation_not_resolved'
                        )
                    })

                if not case.transaction_id or not case.transaction or                    case.transaction.verification_status != 'VERIFIED' or                    case.transaction.payment_id != payment.id:
                    raise ValidationError({
                        'transaction': ValidationError(
                            "Hồ sơ đối soát chưa liên kết với giao dịch ngân hàng đã xác minh (VERIFIED) thuộc cùng bản ghi thanh toán.",
                            code='invalid_linked_transaction'
                        )
                    })

                tx = case.transaction

                if tx.received_at and completed_at < tx.received_at:
                    raise ValidationError({
                        'completed_at': ValidationError(
                            f"Thời điểm ngân hàng hoàn tiền ({completed_at.strftime('%H:%M:%S %d/%m/%Y')}) không được trước thời điểm tiền vào ngân hàng ({tx.received_at.strftime('%H:%M:%S %d/%m/%Y')}).",
                            code='completed_at_before_receipt'
                        )
                    })

                if amount_dec != tx.received_amount or amount_dec != payment.expected_amount:
                    raise ValidationError({
                        'refund_amount': ValidationError(
                            f"Số tiền hoàn ({amount_dec:,.0f} đ) phải khớp chính xác 100% số tiền thực nhận ({tx.received_amount:,.0f} đ) và số tiền dự kiến ({payment.expected_amount:,.0f} đ). Nghiệp vụ này từ chối hoàn tiền một phần hoặc vượt mức.",
                            code='amount_mismatch'
                        )
                    })

                if order.order_status == OrderStatus.EXPIRED:
                    expected_reason = RefundReasonType.LATE_PAYMENT
                else:
                    expected_reason = RefundReasonType.CANCEL

                if payment.status == PaymentStatus.REFUNDED:
                    existing_refunds = list(Refund.objects.filter(order=order))
                    if len(existing_refunds) == 1:
                        ref_rec = existing_refunds[0]
                        if (
                            ref_rec.bank_reference == cleaned_bank_ref and
                            ref_rec.amount == amount_dec and
                            ref_rec.status == RefundStatus.COMPLETED and
                            ref_rec.reason_type == expected_reason and
                            ref_rec.completed_at == completed_at
                        ):
                            return RecordExternalRefundResult(
                                reconciliation=case,
                                order=order,
                                payment=payment,
                                refund=ref_rec,
                                already_confirmed=True,
                            )
                        else:
                            raise ValidationError(
                                "Khoản thanh toán của đơn hàng này đã được hoàn tất trước đó với thông tin khác (xung đột dữ liệu hoàn tiền).",
                                code='payment_already_refunded_conflict'
                            )
                    else:
                        raise ValidationError(
                            "Dữ liệu hoàn tiền của đơn hàng không nhất quán (ambiguous refund records). Cần kiểm tra đối soát nội bộ.",
                            code='ambiguous_refund_records'
                        )

                elif payment.status == PaymentStatus.PENDING_REFUND:
                    existing_order_refunds = list(Refund.objects.filter(order=order))
                    if existing_order_refunds:
                        if any(r.status == RefundStatus.COMPLETED for r in existing_order_refunds):
                            raise ValidationError(
                                "Đơn hàng này đã tồn tại bản ghi hoàn tiền hoàn tất (COMPLETED). "
                                "Trạng thái thanh toán Chờ hoàn tiền (PENDING_REFUND) không nhất quán với dữ liệu hoàn tiền đã có.",
                                code='existing_refund_conflict'
                            )
                        if any(r.bank_reference == cleaned_bank_ref for r in existing_order_refunds):
                            raise ValidationError({
                                'bank_reference': ValidationError(
                                    f"Mã giao dịch ngân hàng chuyển hoàn '{cleaned_bank_ref}' đã được ghi nhận cho đơn hàng này.",
                                    code='duplicate_bank_reference'
                                )
                            })
                        raise ValidationError(
                            "Đơn hàng đã tồn tại bản ghi hoàn tiền chưa đồng bộ với trạng thái thanh toán. "
                            "Yêu cầu rà soát đối soát độc lập.",
                            code='ambiguous_refund_records'
                        )

                else:
                    raise ValidationError({
                        'payment': ValidationError(
                            f"Trạng thái thanh toán hiện tại là {payment.status}. Chỉ cho phép xác nhận hoàn tiền khi trạng thái là PENDING_REFUND.",
                            code='invalid_payment_status'
                        )
                    })

                if Refund.objects.filter(bank_reference__iexact=cleaned_bank_ref).exclude(order=order).exists():
                    raise ValidationError({
                        'bank_reference': ValidationError(
                            f"Mã giao dịch ngân hàng chuyển hoàn {cleaned_bank_ref} đã được sử dụng cho một đơn hàng khác.",
                            code='duplicate_bank_reference'
                        )
                    })

                try:
                    refund = Refund.objects.create(
                        order=order,
                        reason_type=expected_reason,
                        amount=amount_dec,
                        status=RefundStatus.COMPLETED,
                        bank_reference=cleaned_bank_ref,
                        confirmed_by=admin_user,
                        completed_at=completed_at,
                    )
                except IntegrityError:
                    raise ValidationError({
                        'bank_reference': ValidationError(
                            f"Mã giao dịch ngân hàng chuyển hoàn '{cleaned_bank_ref}' đã tồn tại trong hệ thống.",
                            code='duplicate_bank_reference'
                        )
                    })

                payment_updated = Payment.objects.filter(
                    pk=payment.pk,
                    status=PaymentStatus.PENDING_REFUND,
                ).update(status=PaymentStatus.REFUNDED)
                if payment_updated != 1:
                    raise ValidationError(
                        "Trạng thái thanh toán đã bị thay đổi đồng thời.",
                        code='concurrent_conflict'
                    )
                payment.status = PaymentStatus.REFUNDED

                ts_str = timezone.localtime(now).strftime("%H:%M:%S %d/%m/%Y")
                comp_str = timezone.localtime(completed_at).strftime("%H:%M:%S %d/%m/%Y")
                audit_entry = (
                    f"[Admin {admin_user.username} {ts_str}]: "
                    f"Xác nhận hoàn tiền 100% ngoài thực tế cho đơn kết thúc ({order.order_status}) "
                    f"(Mã GD hoàn tiền: {cleaned_bank_ref}, Số tiền hoàn: {amount_dec:,.0f} VNĐ, "
                    f"Thời điểm ngân hàng hoàn tất: {comp_str}). "
                    f"Chuyển trạng thái thanh toán sang Đã hoàn tiền (REFUNDED). "
                    f"Ghi chú: {cleaned_note}"
                )
                updated_notes = f"{case.notes}\n{audit_entry}".strip() if case.notes else audit_entry
                case.notes = updated_notes
                PaymentReconciliation.objects.filter(pk=case.pk).update(notes=updated_notes)

                return RecordExternalRefundResult(
                    reconciliation=case,
                    order=order,
                    payment=payment,
                    refund=refund,
                    already_confirmed=False,
                )

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )
