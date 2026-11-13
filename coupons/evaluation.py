from decimal import Decimal, ROUND_HALF_UP
import datetime
from django.core.exceptions import ValidationError
from django.utils import timezone

from catalog.utils import format_vnd
from coupons.models import Coupon, DiscountType
from cart.services import get_cart_evaluation


def evaluate_coupon_for_cart(cart, code, at=None):
    if not cart or not hasattr(cart, 'items'):
        raise ValidationError(
            "Giỏ hàng của bạn đang trống hoặc không có sản phẩm khả dụng.",
            code="cart_empty"
        )

    cart_eval = get_cart_evaluation(cart)
    purchasable_lines = [line for line in cart_eval.get('lines', []) if line.get('is_available')]

    if not purchasable_lines:
        raise ValidationError(
            "Giỏ hàng của bạn đang trống hoặc không có sản phẩm khả dụng.",
            code="cart_empty"
        )

    cart_subtotal = cart_eval.get('subtotal_available', Decimal('0.00'))
    if cart_subtotal < Decimal('0.00'):
        cart_subtotal = Decimal('0.00')
    has_unavailable_lines = bool(cart_eval.get('has_unavailable_lines', False))

    if not code or not isinstance(code, str) or not code.strip():
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    normalized_code = code.strip().upper()
    if len(normalized_code) > 40:
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    try:
        coupon = Coupon.objects.get(code=normalized_code)
    except Coupon.DoesNotExist:
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    if not coupon.is_active:
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    if at is None:
        at = timezone.now()
    elif not isinstance(at, datetime.datetime) or timezone.is_naive(at):
        raise ValidationError(
            "Thời điểm kiểm tra mã không hợp lệ (yêu cầu aware datetime).",
            code="invalid_time"
        )

    if at < coupon.starts_at:
        raise ValidationError(
            "Mã giảm giá chưa đến thời gian áp dụng.",
            code="coupon_not_started"
        )

    if at >= coupon.ends_at:
        raise ValidationError(
            "Mã giảm giá đã hết hạn sử dụng.",
            code="coupon_expired"
        )

    current_consumed = (coupon.used_count or 0) + (coupon.reserved_count or 0)
    if current_consumed >= coupon.max_uses:
        raise ValidationError(
            "Mã giảm giá đã hết lượt sử dụng.",
            code="coupon_exhausted"
        )

    if coupon.min_order_amount and coupon.min_order_amount > Decimal('0.00'):
        if cart_subtotal < coupon.min_order_amount:
            min_str = format_vnd(coupon.min_order_amount)
            raise ValidationError(
                f"Đơn hàng chưa đạt giá trị tối thiểu ({min_str}) để áp dụng mã giảm giá này.",
                code="min_order_not_reached"
            )

    if coupon.applies_to_all:
        eligible_lines = purchasable_lines
    else:
        eligible_product_ids = set(
            coupon.coupon_products.values_list('product_id', flat=True)
        )
        eligible_lines = [
            line for line in purchasable_lines
            if line['product'].id in eligible_product_ids
        ]

    if not eligible_lines:
        raise ValidationError(
            "Không có sản phẩm nào trong giỏ hàng thỏa mãn điều kiện áp dụng mã giảm giá này.",
            code="no_eligible_products"
        )

    eligible_subtotal = sum((line['line_total'] for line in eligible_lines), Decimal('0.00'))
    if eligible_subtotal < Decimal('0.00'):
        eligible_subtotal = Decimal('0.00')

    if coupon.discount_type == DiscountType.PERCENT:
        raw_discount = (eligible_subtotal * coupon.value) / Decimal('100')
        if coupon.max_discount is not None:
            raw_discount = min(raw_discount, coupon.max_discount)
    elif coupon.discount_type == DiscountType.FIXED:
        raw_discount = min(coupon.value, eligible_subtotal)
    else:
        raise ValidationError(
            "Loại hình giảm giá không được hỗ trợ.",
            code="unsupported_discount_type"
        )

    discount_amount = raw_discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    discount_amount = max(Decimal('0.00'), min(discount_amount, eligible_subtotal))

    total_after_discount = cart_subtotal - discount_amount
    total_after_discount = max(Decimal('0.00'), total_after_discount)

    return {
        'coupon_id': coupon.id,
        'code': coupon.code,
        'cart_subtotal': cart_subtotal,
        'eligible_subtotal': eligible_subtotal,
        'discount_amount': discount_amount,
        'total_after_discount': total_after_discount,
        'has_unavailable_lines': has_unavailable_lines,
    }
