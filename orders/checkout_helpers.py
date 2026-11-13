from decimal import Decimal
import hashlib
import json
import uuid
from typing import NamedTuple, Optional, List, Dict, Any

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from catalog.utils import format_vnd
from cart.services import get_cart_evaluation
from coupons.evaluation import evaluate_coupon_for_cart
from orders.models import Order


CHECKOUT_TOKEN_SALT = 'orders.checkout.token'
QUOTE_TOKEN_SALT = 'orders.checkout.quote'


class ServerQuote(NamedTuple):
    lines: List[Dict[str, Any]]
    subtotal: Decimal
    subtotal_formatted: str
    discount_total: Decimal
    discount_total_formatted: str
    grand_total: Decimal
    grand_total_formatted: str
    coupon_code: Optional[str]
    coupon_valid: bool
    coupon_error: Optional[str]
    has_unavailable: bool
    unavailable_count: int
    total_quantity: int
    is_valid: bool
    error_message: Optional[str]
    fingerprint: str
    quote_token: str


class QuoteUnavailable:
    def __init__(self, coupon_code: str = ""):
        self.lines: List[Dict[str, Any]] = []
        self.subtotal = Decimal('0.00')
        self.subtotal_formatted = "Chưa sẵn sàng"
        self.discount_total = Decimal('0.00')
        self.discount_total_formatted = "0 đ"
        self.grand_total = Decimal('0.00')
        self.grand_total_formatted = "Chưa sẵn sàng"
        self.coupon_code = coupon_code or ""
        self.coupon_valid = False
        self.coupon_error = None
        self.has_unavailable = True
        self.unavailable_count = 0
        self.total_quantity = 0
        self.is_valid = False
        self.error_message = "Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát."
        self.fingerprint = ""
        self.quote_token = ""
        self.is_unavailable = True


def generate_order_code() -> str:
    return f"QHM{uuid.uuid4().hex[:14].upper()}"


def get_or_create_pending_order_code(request, cart) -> str:
    session_key = f"_qhm_checkout_pending_{cart.id}"
    code = request.session.get(session_key)

    if code and isinstance(code, str):
        if Order.objects.filter(order_code=code).exists():
            code = generate_order_code()
            request.session[session_key] = code
            request.session.modified = True
    else:
        code = generate_order_code()
        request.session[session_key] = code
        request.session.modified = True

    return code


def clear_pending_order_code(request, cart=None) -> None:
    if hasattr(request, 'session'):
        if cart and hasattr(cart, 'id'):
            session_key = f"_qhm_checkout_pending_{cart.id}"
            request.session.pop(session_key, None)
        request.session.pop('pending_checkout_data', None)
        request.session.modified = True


def make_checkout_token(user_id: int, cart_id: int, order_code: str) -> str:
    payload = {
        'user_id': user_id,
        'cart_id': cart_id,
        'order_code': order_code,
    }
    return signing.dumps(payload, salt=CHECKOUT_TOKEN_SALT)


def verify_checkout_token(token: Optional[str], user_id: int, cart_id: int) -> Optional[str]:
    if not token or not isinstance(token, str):
        return None
    try:
        data = signing.loads(token, salt=CHECKOUT_TOKEN_SALT)
        if not isinstance(data, dict):
            return None
        if data.get('user_id') != user_id or data.get('cart_id') != cart_id:
            return None
        order_code = data.get('order_code')
        if not order_code or not isinstance(order_code, str):
            return None
        return order_code
    except (signing.BadSignature, Exception):
        return None


def make_quote_token(fingerprint: str) -> str:
    return signing.dumps({'fp': fingerprint}, salt=QUOTE_TOKEN_SALT)


def verify_quote_token(quote_token: Optional[str]) -> Optional[str]:
    if not quote_token or not isinstance(quote_token, str):
        return None
    try:
        data = signing.loads(quote_token, salt=QUOTE_TOKEN_SALT)
        if isinstance(data, dict):
            return data.get('fp')
    except (signing.BadSignature, Exception):
        pass
    return None


def calculate_server_quote(cart, coupon_code: Optional[str] = None, pricing_instant=None) -> ServerQuote:
    pricing_at = pricing_instant or timezone.now()
    eval_res = get_cart_evaluation(cart)
    raw_lines = eval_res.get('lines', [])

    if not cart or not raw_lines:
        fp = "EMPTY_CART"
        q_token = make_quote_token(fp)
        return ServerQuote(
            lines=[],
            subtotal=Decimal('0.00'),
            subtotal_formatted=format_vnd(Decimal('0.00')),
            discount_total=Decimal('0.00'),
            discount_total_formatted=format_vnd(Decimal('0.00')),
            grand_total=Decimal('0.00'),
            grand_total_formatted=format_vnd(Decimal('0.00')),
            coupon_code=None,
            coupon_valid=False,
            coupon_error=None,
            has_unavailable=False,
            unavailable_count=0,
            total_quantity=0,
            is_valid=False,
            error_message="Giỏ hàng của bạn đang trống.",
            fingerprint=fp,
            quote_token=q_token,
        )

    has_unavailable = eval_res.get('has_unavailable_lines', False)
    unavailable_count = eval_res.get('unavailable_count', 0)
    total_quantity = eval_res.get('total_quantity', 0)
    subtotal = eval_res.get('subtotal_available', Decimal('0.00'))

    processed_lines = []
    for line in raw_lines:
        item = line['item']
        variant = line['variant']
        unit_price = line.get('current_price') if line.get('current_price') is not None else (variant.price or Decimal('0.00'))
        qty = getattr(item, 'quantity', line.get('quantity', 1))
        line_total = line.get('line_total') if line.get('line_total') is not None else (unit_price * Decimal(qty))
        processed_lines.append({
            'item_id': item.id,
            'variant_id': variant.id,
            'product_name': variant.product.name,
            'attrs_display': line.get('attributes_display') or line.get('attrs_display', ''),
            'sku': variant.sku,
            'image': line.get('image'),
            'quantity': qty,
            'unit_price': unit_price,
            'unit_price_formatted': format_vnd(unit_price),
            'line_total': line_total,
            'line_total_formatted': format_vnd(line_total),
            'is_available': line.get('is_available', False),
            'availability_message': line.get('status_text') or line.get('availability_message', ''),
            'has_price_changed': line.get('has_price_changed', False),
            'price_change_message': line.get('old_price_formatted') or line.get('price_change_message', ''),
        })

    clean_code = str(coupon_code).strip().upper() if coupon_code and str(coupon_code).strip() else None
    discount_total = Decimal('0.00')
    coupon_valid = False
    coupon_error = None

    if clean_code:
        try:
            coupon_res = evaluate_coupon_for_cart(cart, clean_code, at=pricing_at)
            discount_total = coupon_res['discount_amount']
            coupon_valid = True
        except ValidationError as e:
            coupon_valid = False
            coupon_error = e.message if hasattr(e, 'message') else str(e)
            discount_total = Decimal('0.00')

    grand_total = max(Decimal('0.00'), subtotal - discount_total)

    is_valid = not has_unavailable and (subtotal > Decimal('0.00') or len(processed_lines) > 0)
    error_message = None
    if has_unavailable:
        error_message = "Giỏ hàng có sản phẩm không khả dụng. Vui lòng cập nhật trước khi đặt hàng."

    line_fingerprints = [
        f"{l['item_id']}:{l['variant_id']}:{l['quantity']}:{l['unit_price']}:{l['is_available']}"
        for l in sorted(processed_lines, key=lambda x: x['item_id'])
    ]
    raw_fp_str = "|".join([
        ",".join(line_fingerprints),
        f"subtotal={subtotal}",
        f"coupon={clean_code or ''}",
        f"coupon_valid={coupon_valid}",
        f"discount={discount_total}",
        f"grand_total={grand_total}",
    ])
    fingerprint = hashlib.sha256(raw_fp_str.encode('utf-8')).hexdigest()[:32]
    quote_token = make_quote_token(fingerprint)

    return ServerQuote(
        lines=processed_lines,
        subtotal=subtotal,
        subtotal_formatted=format_vnd(subtotal),
        discount_total=discount_total,
        discount_total_formatted=format_vnd(discount_total),
        grand_total=grand_total,
        grand_total_formatted=format_vnd(grand_total),
        coupon_code=clean_code,
        coupon_valid=coupon_valid,
        coupon_error=coupon_error,
        has_unavailable=has_unavailable,
        unavailable_count=unavailable_count,
        total_quantity=total_quantity,
        is_valid=is_valid,
        error_message=error_message,
        fingerprint=fingerprint,
        quote_token=quote_token,
    )


def map_validation_error_to_form(form, err: ValidationError) -> None:
    if hasattr(err, 'message_dict'):
        for field, messages in err.message_dict.items():
            if field in form.fields:
                form.add_error(field, messages)
            else:
                form.add_error(None, messages)
    elif hasattr(err, 'messages'):
        form.add_error(None, err.messages)
    else:
        form.add_error(None, str(err))
