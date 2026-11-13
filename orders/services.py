from decimal import Decimal
import random
import time
from typing import NamedTuple, Optional
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone

from accounts.permissions import is_customer, is_sales_staff, is_admin
from cart.models import Cart, CartItem
from coupons.evaluation import evaluate_coupon_for_cart
from coupons.models import Coupon, CouponUsage, CouponUsageStatus
from inventory.models import (
    Inventory,
    InventoryReservation,
    InventoryReservationStatus,
    InventoryTransaction,
)
from orders.models import (
    CancellationReasonCode,
    Order,
    OrderCancellation,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    validate_delivery_phone,
)
from payments.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    PaymentTransaction,
    Refund,
)
from payments.qr import get_qr_transfer_config, is_qr_transfer_available


class ConcurrencyConflict(Exception):
    pass


class OrderCreationResult(NamedTuple):
    order: Order
    is_replay: bool


class CancelOrderResult(NamedTuple):
    order: Order
    cancellation: OrderCancellation
    history_entry: OrderStatusHistory
    is_replay: bool


def _is_sqlite_lock_error(exc: Exception) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    msg = str(exc).lower()
    return 'locked' in msg or 'busy' in msg


def _resolve_and_validate_cart(customer, cart_id: Optional[int] = None, cart: Optional[Cart] = None) -> Cart:
    if cart is not None and cart_id is not None:
        if cart.id != cart_id:
            err = ValidationError({
                'cart': ValidationError('Đối tượng giỏ hàng và ID giỏ hàng không khớp.', code='cart_mismatch')
            })
            err.code = 'cart_mismatch'
            raise err

    target_cart = None
    if cart_id is not None:
        target_cart = Cart.objects.filter(id=cart_id).first()
    elif cart is not None:
        target_cart = Cart.objects.filter(id=cart.id).first()
    else:
        target_cart = Cart.objects.filter(user=customer).first()

    if target_cart is None or target_cart.user_id != customer.id:
        err = ValidationError({
            'cart': ValidationError(
                'Giỏ hàng không tồn tại hoặc không thuộc sở hữu của khách hàng.',
                code='cart_not_owned'
            )
        })
        err.code = 'cart_not_owned'
        raise err

    return target_cart


def _format_variant_attributes_display(variant) -> str:
    links = list(variant.attribute_values.select_related('attribute', 'attribute_value').all())
    if links:
        links.sort(key=lambda x: (x.attribute.name, x.id))
        return ", ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
    return "Phiên bản mặc định"


def _allocate_coupon_discount(
    evaluated_lines: list,
    coupon: Coupon,
    total_discount: Decimal,
    eligible_product_ids: Optional[set] = None,
) -> dict:
    count = len(evaluated_lines)
    if total_discount <= Decimal('0.00'):
        return {i: Decimal('0.00') for i in range(count)}

    eligible_indices = []
    for i, line in enumerate(evaluated_lines):
        is_eligible = coupon.applies_to_all or (
            eligible_product_ids is not None and line['product'].id in eligible_product_ids
        )
        if is_eligible and line['line_total'] > Decimal('0.00'):
            eligible_indices.append(i)

    if not eligible_indices:
        return {i: Decimal('0.00') for i in range(count)}

    eligible_subtotal = sum((evaluated_lines[i]['line_total'] for i in eligible_indices), Decimal('0.00'))
    if eligible_subtotal <= Decimal('0.00'):
        return {i: Decimal('0.00') for i in range(count)}

    total_discount_cents = int((total_discount * Decimal('100')).to_integral_value())

    base_cents_map = {}
    remainders_map = {}

    for i in eligible_indices:
        line_gross = evaluated_lines[i]['line_total']
        exact_cents = (line_gross * Decimal(total_discount_cents)) / eligible_subtotal
        floor_cents = int(exact_cents)
        base_cents_map[i] = floor_cents
        remainders_map[i] = exact_cents - Decimal(floor_cents)

    allocated_sum = sum(base_cents_map.values())
    remaining_cents = total_discount_cents - allocated_sum

    if remaining_cents > 0:
        sorted_indices = sorted(
            eligible_indices,
            key=lambda idx: (
                -remainders_map[idx],
                evaluated_lines[idx]['variant'].id,
                evaluated_lines[idx]['item'].id,
            )
        )
        for idx in range(min(remaining_cents, len(sorted_indices))):
            target_i = sorted_indices[idx]
            base_cents_map[target_i] += 1

    allocated_discounts = {}
    for i in range(count):
        if i in base_cents_map:
            cents = base_cents_map[i]
            disc = Decimal(cents) / Decimal('100')
            disc = max(Decimal('0.00'), min(disc, evaluated_lines[i]['line_total']))
            allocated_discounts[i] = disc
        else:
            allocated_discounts[i] = Decimal('0.00')

    return allocated_discounts


def _validate_and_resolve_order_replay(
    existing_order: Order,
    customer,
    coupon_code: Optional[str],
    expected_method: PaymentMethod = PaymentMethod.COD,
) -> OrderCreationResult:
    if existing_order.customer_id != customer.id:
        err = ValidationError({
            'order_code': ValidationError(
                'Mã đơn hàng đã được sử dụng bởi một khách hàng khác.',
                code='order_code_conflict'
            )
        })
        err.code = 'order_code_conflict'
        raise err

    has_coupon_usage = CouponUsage.objects.filter(order=existing_order).exists()
    if coupon_code is None:
        if has_coupon_usage:
            err = ValidationError({
                'order_code': ValidationError(
                    'Mã đơn hàng đã tồn tại với thông tin voucher khác.',
                    code='order_code_immutable'
                )
            })
            err.code = 'order_code_immutable'
            raise err
    else:
        if not has_coupon_usage:
            err = ValidationError({
                'order_code': ValidationError(
                    'Không thể thay đổi hoặc gán mới mã giảm giá cho đơn hàng đã tồn tại.',
                    code='order_code_immutable'
                )
            })
            err.code = 'order_code_immutable'
            raise err

    existing_payment = Payment.objects.filter(order=existing_order).first()
    if existing_payment is None:
        err = ValidationError({
            'payment': ValidationError(
                'Đơn hàng lịch sử thiếu bản ghi thanh toán phục vụ replay.',
                code='payment_missing_for_replay'
            )
        })
        err.code = 'payment_missing_for_replay'
        raise err

    if existing_payment.method != expected_method:
        err = ValidationError({
            'payment_method': ValidationError(
                f'Phương thức thanh toán của đơn hàng không khớp {expected_method}.',
                code='payment_method_conflict'
            )
        })
        err.code = 'payment_method_conflict'
        raise err

    return OrderCreationResult(order=existing_order, is_replay=True)


def _create_order_core(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    payment_method: PaymentMethod = PaymentMethod.COD,
    coupon_code: Optional[str] = None,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if customer is None:
        err = ValidationError({'customer': ValidationError('Khách hàng không được để trống.', code='customer_required')})
        err.code = 'customer_required'
        raise err

    if not getattr(customer, 'is_authenticated', False) or not getattr(customer, 'is_active', True):
        raise PermissionDenied('Tài khoản khách hàng chưa được xác thực hoặc bị vô hiệu hóa.')

    field_errors = {}

    clean_order_code = str(order_code).strip() if order_code is not None else ''
    if not clean_order_code:
        field_errors['order_code'] = ValidationError('Mã đơn hàng không được để trống.', code='order_code_required')
    elif len(clean_order_code) > 30:
        field_errors['order_code'] = ValidationError('Mã đơn hàng không được vượt quá 30 ký tự.', code='order_code_max_length')

    clean_name = str(recipient_name).strip() if recipient_name is not None else ''
    if not clean_name:
        field_errors['recipient_name'] = ValidationError('Tên người nhận không được để trống.', code='recipient_name_required')
    elif len(clean_name) > 120:
        field_errors['recipient_name'] = ValidationError('Tên người nhận không được vượt quá 120 ký tự.', code='recipient_name_max_length')

    clean_phone = ''
    if not recipient_phone or not str(recipient_phone).strip():
        field_errors['recipient_phone'] = ValidationError('Số điện thoại người nhận không được để trống.', code='recipient_phone_required')
    else:
        try:
            clean_phone = validate_delivery_phone(recipient_phone)
        except ValidationError as e:
            code = getattr(e, 'code', None) or 'invalid_phone'
            field_errors['recipient_phone'] = ValidationError(e.message, code=code)

    clean_address = str(shipping_address).strip() if shipping_address is not None else ''
    if not clean_address:
        field_errors['shipping_address'] = ValidationError('Địa chỉ giao hàng không được để trống.', code='shipping_address_required')

    if payment_method not in (PaymentMethod.COD, PaymentMethod.QR):
        field_errors['payment_method'] = ValidationError(
            f'Phương thức thanh toán không được hỗ trợ: {payment_method}.',
            code='payment_method_unsupported'
        )

    if field_errors:
        err = ValidationError(field_errors)
        raise err

    in_nested_tx = connection.in_atomic_block
    effective_retries = 1 if in_nested_tx else max(1, int(max_retries))

    for attempt in range(effective_retries):
        try:
            with transaction.atomic():
                if not is_customer(customer):
                    raise PermissionDenied('Tài khoản không có vai trò Khách hàng (Customer) để đặt hàng.')

                target_cart = _resolve_and_validate_cart(customer, cart_id=cart_id, cart=cart)

                existing_in_tx = Order.objects.filter(order_code=clean_order_code).first()
                if existing_in_tx:
                    return _validate_and_resolve_order_replay(
                        existing_order=existing_in_tx,
                        customer=customer,
                        coupon_code=coupon_code,
                        expected_method=payment_method,
                    )

                if payment_method == PaymentMethod.QR:
                    if not is_qr_transfer_available():
                        err = ValidationError({
                            'payment_method': ValidationError(
                                'Phương thức thanh toán chuyển khoản qua mã QR hiện chưa sẵn sàng hoặc chưa được cấu hình.',
                                code='qr_config_unavailable'
                            )
                        })
                        err.code = 'qr_config_unavailable'
                        raise err

                cart_items = list(
                    target_cart.items.select_related(
                        'variant__product__category',
                        'variant__inventory'
                    ).all()
                )

                if not cart_items:
                    err = ValidationError({
                        'cart': ValidationError('Giỏ hàng trống, không thể tạo đơn hàng.', code='cart_empty')
                    })
                    err.code = 'cart_empty'
                    raise err

                cart_items.sort(key=lambda item: item.variant_id)

                pricing_instant = timezone.now()
                subtotal = Decimal('0.00')
                evaluated_lines = []

                for item in cart_items:
                    variant = item.variant
                    product = variant.product
                    category = product.category

                    if not category.is_visible:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Sản phẩm "{product.name}" thuộc danh mục đang tạm ẩn, không thể đặt hàng.',
                                code='category_hidden'
                            )
                        })
                        err.code = 'category_hidden'
                        raise err

                    if not product.is_active or product.is_deleted:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Sản phẩm "{product.name}" đã ngừng kinh doanh hoặc không còn tồn tại.',
                                code='product_inactive'
                            )
                        })
                        err.code = 'product_inactive'
                        raise err

                    if not variant.is_active:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Phiên bản "{variant.sku}" của sản phẩm "{product.name}" đã ngừng kinh doanh.',
                                code='variant_inactive'
                            )
                        })
                        err.code = 'variant_inactive'
                        raise err

                    if variant.price is None or variant.price < Decimal('0.00'):
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Đơn giá của phiên bản "{variant.sku}" không hợp lệ.',
                                code='invalid_price'
                            )
                        })
                        err.code = 'invalid_price'
                        raise err

                    if item.quantity <= 0:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Số lượng của phiên bản "{variant.sku}" không hợp lệ.',
                                code='invalid_quantity'
                            )
                        })
                        err.code = 'invalid_quantity'
                        raise err

                    inv = getattr(variant, 'inventory', None)
                    if inv is None:
                        err = ValidationError({
                            'inventory': ValidationError(
                                f'Sản phẩm "{product.name}" ({variant.sku}) chưa có cấu hình kho hàng.',
                                code='inventory_missing'
                            )
                        })
                        err.code = 'inventory_missing'
                        raise err

                    available_stock = inv.physical_sellable - inv.reserved
                    if available_stock < item.quantity:
                        err = ValidationError({
                            'inventory': ValidationError(
                                f'Sản phẩm "{product.name}" ({variant.sku}) không đủ tồn kho khả dụng '
                                f'(yêu cầu: {item.quantity}, khả dụng: {max(0, available_stock)}).',
                                code='insufficient_stock'
                            )
                        })
                        err.code = 'insufficient_stock'
                        raise err

                    current_unit_price = variant.price
                    line_total = current_unit_price * Decimal(item.quantity)
                    subtotal += line_total

                    evaluated_lines.append({
                        'item': item,
                        'variant': variant,
                        'product': product,
                        'inv': inv,
                        'unit_price': current_unit_price,
                        'line_total': line_total,
                    })

                coupon = None
                total_discount = Decimal('0.00')
                allocated_discounts = {i: Decimal('0.00') for i in range(len(evaluated_lines))}

                if coupon_code is not None:
                    eval_result = evaluate_coupon_for_cart(target_cart, coupon_code, at=pricing_instant)
                    total_discount = eval_result['discount_amount']

                    coupon = Coupon.objects.filter(id=eval_result['coupon_id']).first()
                    if coupon is None:
                        err = ValidationError({
                            'coupon_code': ValidationError(
                                'Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.',
                                code='coupon_invalid'
                            )
                        })
                        err.code = 'coupon_invalid'
                        raise err

                    if payment_method == PaymentMethod.COD:
                        rows_updated = Coupon.objects.filter(
                            id=coupon.id,
                            is_active=True,
                            starts_at__lte=pricing_instant,
                            ends_at__gt=pricing_instant,
                            used_count=coupon.used_count,
                            reserved_count=coupon.reserved_count,
                        ).filter(
                            used_count__lt=F('max_uses') - F('reserved_count')
                        ).update(
                            used_count=F('used_count') + 1
                        )
                    else:
                        rows_updated = Coupon.objects.filter(
                            id=coupon.id,
                            is_active=True,
                            starts_at__lte=pricing_instant,
                            ends_at__gt=pricing_instant,
                            used_count=coupon.used_count,
                            reserved_count=coupon.reserved_count,
                        ).filter(
                            reserved_count__lt=F('max_uses') - F('used_count')
                        ).update(
                            reserved_count=F('reserved_count') + 1
                        )

                    if rows_updated == 0:
                        refreshed = Coupon.objects.filter(id=coupon.id).first()
                        if not refreshed or not refreshed.is_active:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.',
                                    code='coupon_invalid'
                                )
                            })
                            err.code = 'coupon_invalid'
                            raise err
                        if pricing_instant < refreshed.starts_at:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá chưa đến thời gian áp dụng.',
                                    code='coupon_not_started'
                                )
                            })
                            err.code = 'coupon_not_started'
                            raise err
                        if pricing_instant >= refreshed.ends_at:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá đã hết hạn sử dụng.',
                                    code='coupon_expired'
                                )
                            })
                            err.code = 'coupon_expired'
                            raise err
                        if refreshed.used_count + refreshed.reserved_count >= refreshed.max_uses:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá đã hết lượt sử dụng.',
                                    code='coupon_exhausted'
                                )
                            })
                            err.code = 'coupon_exhausted'
                            raise err
                        raise ConcurrencyConflict(
                            f'Xung đột cập nhật hạn mức mã giảm giá {coupon.code}.'
                        )

                    eligible_product_ids = None
                    if not coupon.applies_to_all:
                        eligible_product_ids = set(
                            coupon.coupon_products.values_list('product_id', flat=True)
                        )
                    allocated_discounts = _allocate_coupon_discount(
                        evaluated_lines=evaluated_lines,
                        coupon=coupon,
                        total_discount=total_discount,
                        eligible_product_ids=eligible_product_ids,
                    )

                grand_total = subtotal - total_discount
                if grand_total < Decimal('0.00'):
                    grand_total = Decimal('0.00')

                for line in evaluated_lines:
                    inv = line['inv']
                    qty = line['item'].quantity

                    rows_updated = Inventory.objects.filter(
                        id=inv.id,
                        physical_sellable__gte=F('reserved') + qty
                    ).update(
                        reserved=F('reserved') + qty,
                        updated_at=timezone.now(),
                    )

                    if rows_updated == 0:
                        cur_inv = (
                            Inventory.objects
                            .filter(id=inv.id)
                            .values('physical_sellable', 'reserved')
                            .first()
                        )
                        if cur_inv and (cur_inv['physical_sellable'] - cur_inv['reserved']) < qty:
                            err = ValidationError({
                                'inventory': ValidationError(
                                    f'Sản phẩm "{line["product"].name}" ({line["variant"].sku}) không đủ tồn kho khả dụng.',
                                    code='insufficient_stock'
                                )
                            })
                            err.code = 'insufficient_stock'
                            raise err
                        raise ConcurrencyConflict(
                            f'Xung đột cập nhật tồn kho đồng thời cho biến thể {line["variant"].sku}.'
                        )

                initial_order_status = (
                    OrderStatus.PENDING_CONFIRMATION if payment_method == PaymentMethod.COD
                    else OrderStatus.PENDING_PAYMENT
                )
                order = Order(
                    order_code=clean_order_code,
                    customer=customer,
                    recipient_name=clean_name,
                    recipient_phone=clean_phone,
                    shipping_address=clean_address,
                    order_status=initial_order_status,
                    subtotal=subtotal,
                    discount_total=total_discount,
                    grand_total=grand_total,
                )
                order.full_clean()
                order.save()

                for i, line in enumerate(evaluated_lines):
                    item = line['item']
                    variant = line['variant']
                    product = line['product']
                    unit_price = line['unit_price']
                    line_gross = line['line_total']
                    line_discount = allocated_discounts[i]
                    line_net = line_gross - line_discount
                    if line_net < Decimal('0.00'):
                        line_net = Decimal('0.00')

                    order_item = OrderItem(
                        order=order,
                        variant=variant,
                        product_name_snapshot=product.name[:150],
                        variant_snapshot=_format_variant_attributes_display(variant)[:255],
                        sku_snapshot=variant.sku[:60],
                        quantity=item.quantity,
                        unit_price=unit_price,
                        discount_amount=line_discount,
                        line_total=line_net,
                    )
                    order_item.full_clean()
                    order_item.save()

                    reservation = InventoryReservation(
                        variant=variant,
                        order_item=order_item,
                        quantity=item.quantity,
                        status=InventoryReservationStatus.ACTIVE,
                        finished_at=None,
                    )
                    reservation.full_clean()
                    reservation.save()

                if coupon is not None:
                    c_status = CouponUsageStatus.USED if payment_method == PaymentMethod.COD else CouponUsageStatus.RESERVED
                    c_finalized = pricing_instant if payment_method == PaymentMethod.COD else None

                    coupon_usage = CouponUsage(
                        coupon=coupon,
                        order=order,
                        discount_amount=total_discount,
                        status=c_status,
                        finalized_at=c_finalized,
                    )
                    coupon_usage.full_clean()
                    coupon_usage.save()

                if payment_method == PaymentMethod.COD:
                    hist_status = OrderStatus.PENDING_CONFIRMATION
                    hist_reason = 'Khách hàng tạo đơn hàng COD từ giỏ hàng.'
                else:
                    hist_status = OrderStatus.PENDING_PAYMENT
                    hist_reason = 'Khách hàng đặt hàng thanh toán chuyển khoản qua mã QR từ giỏ hàng.'

                history = OrderStatusHistory(
                    order=order,
                    old_status='',
                    new_status=hist_status,
                    changed_by=customer,
                    reason=hist_reason,
                    changed_at=timezone.now(),
                )
                history.full_clean()
                history.save()

                if payment_method == PaymentMethod.COD:
                    payment = Payment(
                        order=order,
                        method=PaymentMethod.COD,
                        status=PaymentStatus.UNPAID,
                        expected_amount=order.grand_total,
                    )
                else:
                    cfg = get_qr_transfer_config()
                    payment = Payment(
                        order=order,
                        method=PaymentMethod.QR,
                        status=PaymentStatus.UNPAID,
                        expected_amount=order.grand_total,
                        qr_reference=clean_order_code,
                        expires_at=pricing_instant + timezone.timedelta(minutes=30),
                        customer_notified_at=None,
                    )
                    payment.set_recipient_snapshot({
                        'bank_name': cfg.get('BANK_NAME'),
                        'bank_bin': cfg.get('BANK_BIN'),
                        'account_number': cfg.get('ACCOUNT_NUMBER'),
                        'account_holder': cfg.get('ACCOUNT_HOLDER'),
                    })
                payment.full_clean()
                payment.save()

                purchased_item_ids = [line['item'].id for line in evaluated_lines]
                target_cart.items.filter(id__in=purchased_item_ids).delete()
                target_cart.save(update_fields=['updated_at'])

                return OrderCreationResult(order=order, is_replay=False)

        except ConcurrencyConflict:
            if in_nested_tx or attempt == effective_retries - 1:
                raise ValidationError(
                    'Hệ thống đang có nhiều giao dịch đặt hàng đồng thời. Vui lòng thử lại sau giây lát.',
                    code='concurrency_conflict'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.04))
            continue
        except OperationalError as exc:
            if _is_sqlite_lock_error(exc):
                if in_nested_tx or attempt == effective_retries - 1:
                    raise ValidationError(
                        'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.',
                        code='database_busy'
                    )
                time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.04))
                continue
            raise
        except IntegrityError:
            try:
                _resolve_and_validate_cart(customer, cart_id=cart_id, cart=cart)
                existing = Order.objects.filter(order_code=clean_order_code).first()
                if existing:
                    return _validate_and_resolve_order_replay(
                        existing_order=existing,
                        customer=customer,
                        coupon_code=coupon_code,
                        expected_method=payment_method,
                    )
            except OperationalError as recov_exc:
                if _is_sqlite_lock_error(recov_exc):
                    if in_nested_tx or attempt == effective_retries - 1:
                        raise ValidationError(
                            'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.',
                            code='database_busy'
                        )
                    time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.04))
                    continue
                raise
            raise

    raise ValidationError('Không thể hoàn tất tạo đơn hàng do vượt quá số lần thử lại.', code='retry_exhausted')


def _create_cod_order_core(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    coupon_code: Optional[str] = None,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    return _create_order_core(
        customer=customer,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        shipping_address=shipping_address,
        order_code=order_code,
        payment_method=PaymentMethod.COD,
        coupon_code=coupon_code,
        cart_id=cart_id,
        cart=cart,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
    )


def create_cod_order_from_cart(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    voucher_code: Optional[str] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if voucher_code is not None and str(voucher_code).strip() != '':
        err = ValidationError({
            'voucher_code': ValidationError(
                'Đơn hàng COD trong phạm vi này chưa hỗ trợ áp dụng voucher / mã giảm giá.',
                code='voucher_unsupported'
            )
        })
        err.code = 'voucher_unsupported'
        raise err

    return _create_cod_order_core(
        customer=customer,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        shipping_address=shipping_address,
        order_code=order_code,
        coupon_code=None,
        cart_id=cart_id,
        cart=cart,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
    )


def create_cod_order_with_coupon_from_cart(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    coupon_code: str,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if coupon_code is None or not isinstance(coupon_code, str) or not coupon_code.strip():
        err = ValidationError({
            'coupon_code': ValidationError('Mã giảm giá không được để trống.', code='coupon_required')
        })
        err.code = 'coupon_required'
        raise err

    clean_coupon_code = coupon_code.strip().upper()
    if len(clean_coupon_code) > 40:
        err = ValidationError({
            'coupon_code': ValidationError('Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.', code='coupon_invalid')
        })
        err.code = 'coupon_invalid'
        raise err

    return _create_cod_order_core(
        customer=customer,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        shipping_address=shipping_address,
        order_code=order_code,
        coupon_code=clean_coupon_code,
        cart_id=cart_id,
        cart=cart,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
    )


def create_qr_order_from_cart(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    voucher_code: Optional[str] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if voucher_code is not None and str(voucher_code).strip() != '':
        err = ValidationError({
            'voucher_code': ValidationError(
                'Đơn hàng QR trong phạm vi này chưa hỗ trợ áp dụng voucher / mã giảm giá qua tham số voucher_code.',
                code='voucher_unsupported'
            )
        })
        err.code = 'voucher_unsupported'
        raise err

    return _create_order_core(
        customer=customer,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        shipping_address=shipping_address,
        order_code=order_code,
        payment_method=PaymentMethod.QR,
        coupon_code=None,
        cart_id=cart_id,
        cart=cart,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
    )


def create_qr_order_with_coupon_from_cart(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    coupon_code: str,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if coupon_code is None or not isinstance(coupon_code, str) or not coupon_code.strip():
        err = ValidationError({
            'coupon_code': ValidationError('Mã giảm giá không được để trống.', code='coupon_required')
        })
        err.code = 'coupon_required'
        raise err

    clean_coupon_code = coupon_code.strip().upper()
    if len(clean_coupon_code) > 40:
        err = ValidationError({
            'coupon_code': ValidationError('Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.', code='coupon_invalid')
        })
        err.code = 'coupon_invalid'
        raise err

    return _create_order_core(
        customer=customer,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        shipping_address=shipping_address,
        order_code=order_code,
        payment_method=PaymentMethod.QR,
        coupon_code=clean_coupon_code,
        cart_id=cart_id,
        cart=cart,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
    )


def notify_customer_transfer(
    order_id: int,
    customer,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> dict:
    if customer is None or not getattr(customer, 'is_authenticated', False):
        raise PermissionDenied("Tài khoản chưa được xác thực.")

    if not is_customer(customer):
        raise PermissionDenied("Chỉ tài khoản Khách hàng (Customer) mới có quyền thông báo chuyển khoản.")

    if not order_id or not isinstance(order_id, int):
        err = ValidationError({'order': ValidationError('ID đơn hàng không hợp lệ.', code='invalid_order_id')})
        err.code = 'invalid_order_id'
        raise err

    in_nested_tx = connection.in_atomic_block
    effective_retries = 1 if in_nested_tx else max(1, int(max_retries))

    for attempt in range(effective_retries):
        try:
            with transaction.atomic():
                order = Order.objects.filter(id=order_id, customer=customer).first()
                if order is None:
                    err = ValidationError({
                        'order': ValidationError(
                            'Đơn hàng không tồn tại hoặc không thuộc quyền sở hữu của khách hàng.',
                            code='order_not_found'
                        )
                    })
                    err.code = 'order_not_found'
                    raise err

                payment = Payment.objects.filter(order=order).first()
                if payment is None:
                    err = ValidationError({
                        'payment': ValidationError(
                            'Đơn hàng chưa có bản ghi thanh toán.',
                            code='payment_missing'
                        )
                    })
                    err.code = 'payment_missing'
                    raise err

                if payment.method != PaymentMethod.QR:
                    err = ValidationError({
                        'payment': ValidationError(
                            'Đơn hàng không sử dụng phương thức thanh toán chuyển khoản qua mã QR.',
                            code='invalid_payment_method'
                        )
                    })
                    err.code = 'invalid_payment_method'
                    raise err

                now = timezone.now()

                is_under_reconciliation = (
                    order.order_status == OrderStatus.PENDING_RECONCILIATION
                    or (hasattr(payment, 'reconciliations') and payment.reconciliations.filter(status='OPEN').exists())
                )
                if is_under_reconciliation:
                    return {
                        'status': 'reconciliation',
                        'order': order,
                        'payment': payment,
                        'is_late': False,
                        'message': 'Đơn hàng đang trong quá trình rà soát và đối soát thông tin thanh toán. Chỗ giữ hàng và ưu đãi của bạn vẫn đang được bảo toàn nguyên vẹn.',
                    }

                if payment.expires_at and now >= payment.expires_at:
                    return {
                        'status': 'expired',
                        'order': order,
                        'payment': payment,
                        'is_late': True,
                        'message': 'Đơn hàng đã hết thời hạn thanh toán chuyển khoản QR (30 phút). Vui lòng liên hệ nhân viên hỗ trợ để đối soát thủ công.',
                    }

                if payment.status == PaymentStatus.PENDING_VERIFY:
                    return {
                        'status': 'already_notified',
                        'order': order,
                        'payment': payment,
                        'is_late': False,
                    }

                if payment.status != PaymentStatus.UNPAID:
                    err = ValidationError({
                        'payment': ValidationError(
                            f'Không thể thông báo chuyển khoản cho đơn hàng ở trạng thái thanh toán "{payment.get_status_display()}".',
                            code='invalid_payment_status'
                        )
                    })
                    err.code = 'invalid_payment_status'
                    raise err

                rows_updated = Payment.objects.filter(
                    id=payment.id,
                    status=PaymentStatus.UNPAID
                ).update(
                    status=PaymentStatus.PENDING_VERIFY,
                    customer_notified_at=now
                )

                if rows_updated == 1:
                    payment.refresh_from_db()
                    return {
                        'status': 'success',
                        'order': order,
                        'payment': payment,
                        'is_late': False,
                    }

                payment.refresh_from_db()
                if payment.status == PaymentStatus.PENDING_VERIFY:
                    return {
                        'status': 'already_notified',
                        'order': order,
                        'payment': payment,
                        'is_late': False,
                    }

                err = ValidationError({
                    'payment': ValidationError(
                        f'Không thể thông báo chuyển khoản cho đơn hàng ở trạng thái thanh toán "{payment.get_status_display()}".',
                        code='invalid_payment_status'
                    )
                })
                err.code = 'invalid_payment_status'
                raise err

        except OperationalError as exc:
            if _is_sqlite_lock_error(exc):
                if in_nested_tx or attempt == effective_retries - 1:
                    raise ValidationError(
                        'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.',
                        code='database_busy'
                    )
                time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.04))
                continue
            raise

    raise ValidationError('Không thể hoàn tất thông báo chuyển khoản do vượt quá số lần thử lại.', code='retry_exhausted')


class ConfirmOrderResult(NamedTuple):
    order: Order
    payment: Payment
    history_entry: Optional[OrderStatusHistory]
    already_confirmed: bool = False


def confirm_order(
    order_id: int,
    user,
    note: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ConfirmOrderResult:
    if not (is_sales_staff(user) or is_admin(user)):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có thẩm quyền xác nhận đơn hàng.")

    cleaned_note = str(note or '').strip()

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                try:
                    order = Order.objects.select_for_update().select_related('customer').prefetch_related('items').get(pk=order_id)
                except Order.DoesNotExist:
                    raise ValidationError('Đơn hàng không tồn tại.', code='order_not_found')

                payment = Payment.objects.select_for_update().filter(order=order).first()
                if not payment:
                    raise ValidationError('Đơn hàng không có bản ghi thanh toán liên kết.', code='missing_payment')

                if order.order_status == OrderStatus.CONFIRMED:
                    return ConfirmOrderResult(
                        order=order,
                        payment=payment,
                        history_entry=None,
                        already_confirmed=True,
                    )

                if order.order_status != OrderStatus.PENDING_CONFIRMATION:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thuộc diện chờ xác nhận.",
                        code='ineligible_order_status'
                    )

                if payment.expected_amount != order.grand_total:
                    raise ValidationError(
                        f"Số tiền thanh toán dự kiến ({payment.expected_amount:,.0f} đ) không khớp với tổng tiền đơn hàng ({order.grand_total:,.0f} đ). Yêu cầu rà soát đối soát vận hành.",
                        code='amount_mismatch'
                    )

                if payment.method == PaymentMethod.COD:
                    if payment.status != PaymentStatus.UNPAID:
                        raise ValidationError(
                            f"Đơn hàng COD chờ xác nhận phải có trạng thái thanh toán là Chưa thanh toán (UNPAID). Hiện tại: {payment.get_status_display()}.",
                            code='invalid_cod_payment_status'
                        )
                elif payment.method == PaymentMethod.QR:
                    if payment.status != PaymentStatus.PAID:
                        raise ValidationError(
                            f"Đơn hàng QR chỉ được xác nhận sau khi thanh toán đã hoàn tất (PAID). Hiện tại: {payment.get_status_display()}.",
                            code='unpaid_qr_order'
                        )
                else:
                    raise ValidationError(
                        f"Phương thức thanh toán '{payment.method}' không được hỗ trợ trong quy trình xác nhận này.",
                        code='unsupported_payment_method'
                    )

                items = list(order.items.all())
                if not items:
                    raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='empty_order')

                for item in items:
                    res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
                    active_res = [r for r in res_list if r.status == InventoryReservationStatus.ACTIVE]
                    if len(active_res) != 1:
                        raise ValidationError(
                            f"Dòng sản phẩm '{item.product_name_snapshot}' ({item.sku_snapshot}) "
                            f"có trạng thái giữ hàng không hợp lệ (số dòng ACTIVE: {len(active_res)}). "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )

                    res = active_res[0]
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' không khớp biến thể đã đặt.",
                            code='invalid_reservation_state'
                        )

                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Số lượng giữ hàng ({res.quantity}) không khớp với số lượng đặt mua ({item.quantity}) của sản phẩm '{item.product_name_snapshot}'.",
                            code='invalid_reservation_state'
                        )

                    if res.finished_at is not None:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' có finished_at không hợp lệ khi còn ACTIVE.",
                            code='invalid_reservation_state'
                        )

                rows_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=OrderStatus.PENDING_CONFIRMATION,
                ).update(order_status=OrderStatus.CONFIRMED)

                if rows_updated != 1:
                    order.refresh_from_db()
                    if order.order_status == OrderStatus.CONFIRMED:
                        return ConfirmOrderResult(
                            order=order,
                            payment=payment,
                            history_entry=None,
                            already_confirmed=True,
                        )
                    raise ValidationError("Xung đột tương tranh khi cập nhật trạng thái đơn hàng.", code='concurrent_conflict')

                order.order_status = OrderStatus.CONFIRMED

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=OrderStatus.PENDING_CONFIRMATION,
                    new_status=OrderStatus.CONFIRMED,
                    changed_by=user,
                    reason=cleaned_note or "Nhân viên xác nhận đơn hàng hợp lệ.",
                    changed_at=timezone.now(),
                )

                return ConfirmOrderResult(
                    order=order,
                    payment=payment,
                    history_entry=history_entry,
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


class ShipOrderResult(NamedTuple):
    order: Order
    payment: Payment
    history_entry: Optional[OrderStatusHistory]
    transactions: list[InventoryTransaction]
    already_shipped: bool = False


def _validate_consistent_shipped_outcome(order: Order, payment: Payment, items: list) -> ShipOrderResult:
    if payment.expected_amount != order.grand_total:
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số tiền thanh toán ({payment.expected_amount:,.0f} đ) "
            f"lệch tổng tiền đơn ({order.grand_total:,.0f} đ).",
            code='inconsistent_replay'
        )
    if payment.method == PaymentMethod.COD and payment.status != PaymentStatus.UNPAID:
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: đơn COD đã giao phải có thanh toán UNPAID, hiện tại: {payment.status}.",
            code='inconsistent_replay'
        )
    if payment.method == PaymentMethod.QR and payment.status != PaymentStatus.PAID:
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: đơn QR đã giao phải có thanh toán PAID, hiện tại: {payment.status}.",
            code='inconsistent_replay'
        )

    if not items:
        raise ValidationError("Dữ liệu xuất kho không nhất quán: đơn hàng không có dòng sản phẩm.", code='inconsistent_replay')

    variant_aggs = {}
    for item in items:
        res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
        if len(res_list) != 1:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: dòng sản phẩm '{item.product_name_snapshot}' "
                f"có {len(res_list)} bản ghi giữ kho (yêu cầu đúng 1 bản ghi SHIPPED).",
                code='inconsistent_replay'
            )
        res = res_list[0]
        if res.status != InventoryReservationStatus.SHIPPED or res.finished_at is None:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: dòng giữ hàng '{item.product_name_snapshot}' "
                f"chưa hoàn tất trạng thái SHIPPED (status={res.status}, finished_at={res.finished_at}).",
                code='inconsistent_replay'
            )
        if res.variant_id != item.variant_id or res.quantity != item.quantity:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: dòng giữ kho không khớp biến thể hoặc số lượng đã đặt.",
                code='inconsistent_replay'
            )

        vid = item.variant_id
        variant_aggs[vid] = variant_aggs.get(vid, 0) + item.quantity

    ship_txs = list(
        InventoryTransaction.objects.select_for_update()
        .filter(order=order, transaction_type=InventoryTransaction.TYPE_SHIP)
        .select_related('inventory')
        .order_by('id')
    )

    if len(ship_txs) != len(variant_aggs):
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số bản ghi sổ cái SHIP ({len(ship_txs)}) "
            f"không khớp số biến thể trong đơn ({len(variant_aggs)}).",
            code='inconsistent_replay'
        )

    for vid, total_qty in variant_aggs.items():
        matching = [tx for tx in ship_txs if tx.inventory.variant_id == vid]
        if len(matching) != 1:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: biến thể ID {vid} có {len(matching)} bản ghi sổ cái SHIP (yêu cầu đúng 1).",
                code='inconsistent_replay'
            )
        tx = matching[0]
        if total_qty <= 0 or tx.quantity_delta >= 0 or tx.quantity_delta != -total_qty:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: số lượng sổ cái SHIP ({tx.quantity_delta}) "
                f"không khớp số lượng đã đặt (-{total_qty}).",
                code='inconsistent_replay'
            )
        if tx.physical_before is None or tx.physical_after is None:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước hoặc sau trong sổ cái SHIP không được để trống.",
                code='inconsistent_replay'
            )
        if tx.physical_before < 0 or tx.physical_after < 0:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước ({tx.physical_before}) "
                f"hoặc sau ({tx.physical_after}) trong sổ cái SHIP không được âm.",
                code='inconsistent_replay'
            )
        if tx.physical_after != tx.physical_before + tx.quantity_delta:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: phép tính tồn kho sổ cái SHIP không khớp "
                f"(physical_after {tx.physical_after} != physical_before {tx.physical_before} + quantity_delta {tx.quantity_delta}).",
                code='inconsistent_replay'
            )
        if tx.order_id != order.id:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: bản ghi sổ cái SHIP không liên kết đúng đơn hàng.",
                code='inconsistent_replay'
            )

    shipping_history_count = OrderStatusHistory.objects.filter(
        order=order,
        old_status=OrderStatus.CONFIRMED,
        new_status=OrderStatus.SHIPPING,
    ).count()
    if shipping_history_count != 1:
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số bản ghi lịch sử chuyển trạng thái CONFIRMED -> SHIPPING là {shipping_history_count} (yêu cầu đúng 1 bản ghi).",
            code='inconsistent_replay'
        )

    return ShipOrderResult(
        order=order,
        payment=payment,
        history_entry=None,
        transactions=ship_txs,
        already_shipped=True,
    )


def ship_order(
    order_id: int,
    user,
    shipping_note: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ShipOrderResult:
    if not (is_sales_staff(user) or is_admin(user)):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có thẩm quyền xuất kho và bắt đầu giao hàng.")

    cleaned_note = str(shipping_note or '').strip()
    if len(cleaned_note) > 500:
        raise ValidationError('Ghi chú vận chuyển không được vượt quá 500 ký tự.', code='invalid_note')

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                try:
                    order = Order.objects.select_for_update().select_related('customer').prefetch_related('items').get(pk=order_id)
                except Order.DoesNotExist:
                    raise ValidationError('Đơn hàng không tồn tại.', code='order_not_found')

                payment = Payment.objects.select_for_update().filter(order=order).first()
                if not payment:
                    raise ValidationError('Đơn hàng không có bản ghi thanh toán liên kết.', code='missing_payment')

                items = list(order.items.select_for_update().select_related('variant').all())
                if not items:
                    raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='empty_order')

                if order.order_status == OrderStatus.SHIPPING:
                    return _validate_consistent_shipped_outcome(order, payment, items)

                if InventoryTransaction.objects.filter(order=order, transaction_type=InventoryTransaction.TYPE_SHIP).exists():
                    raise ValidationError(
                        "Phát hiện xung đột dữ liệu: Đơn hàng ở trạng thái CONFIRMED nhưng đã tồn tại bản ghi sổ cái SHIP. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_shipping_state'
                    )

                if order.order_status != OrderStatus.CONFIRMED:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thể xuất kho và bắt đầu giao hàng.",
                        code='ineligible_order_status'
                    )

                if payment.expected_amount != order.grand_total:
                    raise ValidationError(
                        f"Số tiền thanh toán dự kiến ({payment.expected_amount:,.0f} đ) không khớp với tổng tiền đơn hàng ({order.grand_total:,.0f} đ). Yêu cầu rà soát đối soát vận hành.",
                        code='amount_mismatch'
                    )

                if payment.method == PaymentMethod.COD:
                    if payment.status != PaymentStatus.UNPAID:
                        raise ValidationError(
                            f"Đơn hàng COD xuất kho phải có trạng thái thanh toán là Chưa thanh toán (UNPAID). Hiện tại: {payment.get_status_display()}.",
                            code='invalid_cod_payment_status'
                        )
                elif payment.method == PaymentMethod.QR:
                    if payment.status != PaymentStatus.PAID:
                        raise ValidationError(
                            f"Đơn hàng QR chỉ được xuất kho và giao hàng khi thanh toán đã hoàn tất (PAID). Hiện tại: {payment.get_status_display()}.",
                            code='unpaid_qr_order'
                        )
                else:
                    raise ValidationError(
                        f"Phương thức thanh toán '{payment.method}' không được hỗ trợ trong quy trình xuất kho này.",
                        code='unsupported_payment_method'
                    )

                variant_aggs = {}
                for item in items:
                    res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
                    active_res = [r for r in res_list if r.status == InventoryReservationStatus.ACTIVE]
                    if len(active_res) != 1:
                        raise ValidationError(
                            f"Dòng sản phẩm '{item.product_name_snapshot}' ({item.sku_snapshot}) "
                            f"có trạng thái giữ hàng không hợp lệ (số dòng ACTIVE: {len(active_res)}). "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )

                    res = active_res[0]
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' không khớp biến thể đã đặt.",
                            code='invalid_reservation_state'
                        )

                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Số lượng giữ hàng ({res.quantity}) không khớp với số lượng đặt mua ({item.quantity}) của sản phẩm '{item.product_name_snapshot}'.",
                            code='invalid_reservation_state'
                        )

                    if res.finished_at is not None:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' có finished_at không hợp lệ khi còn ACTIVE.",
                            code='invalid_reservation_state'
                        )

                    vid = item.variant_id
                    if vid not in variant_aggs:
                        variant_aggs[vid] = {
                            'variant': item.variant,
                            'total_quantity': 0,
                            'reservations': [],
                        }
                    variant_aggs[vid]['total_quantity'] += item.quantity
                    variant_aggs[vid]['reservations'].append(res)

                now = timezone.now()
                created_transactions = []

                for vid in sorted(variant_aggs.keys()):
                    agg = variant_aggs[vid]
                    total_qty = agg['total_quantity']

                    try:
                        inv = Inventory.objects.select_for_update().get(variant_id=vid)
                    except Inventory.DoesNotExist:
                        raise ValidationError(
                            f"Không tìm thấy bản ghi tồn kho cho biến thể SKU '{agg['variant'].sku}'.",
                            code='missing_inventory'
                        )

                    if inv.physical_sellable < total_qty:
                        raise ValidationError(
                            f"Tồn thực tế của biến thể SKU '{agg['variant'].sku}' ({inv.physical_sellable}) "
                            f"không đủ để xuất kho (yêu cầu {total_qty}).",
                            code='insufficient_inventory'
                        )

                    if inv.reserved < total_qty:
                        raise ValidationError(
                            f"Tồn đã giữ của biến thể SKU '{agg['variant'].sku}' ({inv.reserved}) "
                            f"không đủ để xuất kho (yêu cầu {total_qty}). Yêu cầu rà soát đối soát vận hành.",
                            code='inconsistent_inventory'
                        )

                    before_phys = inv.physical_sellable
                    after_phys = before_phys - total_qty

                    rows_updated = Inventory.objects.filter(
                        pk=inv.pk,
                        physical_sellable=before_phys,
                        reserved=inv.reserved,
                        physical_sellable__gte=total_qty,
                        reserved__gte=total_qty,
                    ).update(
                        physical_sellable=F('physical_sellable') - total_qty,
                        reserved=F('reserved') - total_qty,
                        updated_at=now,
                    )

                    if rows_updated != 1:
                        raise ConcurrencyConflict(f"Xung đột tương tranh khi cập nhật tồn kho biến thể SKU '{agg['variant'].sku}'.")

                    req_key = uuid.uuid5(uuid.NAMESPACE_URL, f"ship:{order.order_code}:{inv.id}")
                    tx = InventoryTransaction.objects.create(
                        inventory=inv,
                        order=order,
                        transaction_type=InventoryTransaction.TYPE_SHIP,
                        quantity_delta=-total_qty,
                        physical_before=before_phys,
                        physical_after=after_phys,
                        performed_by=user,
                        request_key=req_key,
                    )
                    created_transactions.append(tx)

                for vid in sorted(variant_aggs.keys()):
                    for res in variant_aggs[vid]['reservations']:
                        res.status = InventoryReservationStatus.SHIPPED
                        res.finished_at = now
                        res.save(update_fields=['status', 'finished_at'])

                rows_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=OrderStatus.CONFIRMED,
                ).update(order_status=OrderStatus.SHIPPING)

                if rows_updated != 1:
                    order.refresh_from_db()
                    if order.order_status == OrderStatus.SHIPPING:
                        return _validate_consistent_shipped_outcome(order, payment, items)
                    raise ValidationError("Xung đột tương tranh khi cập nhật trạng thái đơn hàng sang SHIPPING.", code='concurrent_conflict')

                order.order_status = OrderStatus.SHIPPING

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=OrderStatus.CONFIRMED,
                    new_status=OrderStatus.SHIPPING,
                    changed_by=user,
                    reason=cleaned_note or "Bắt đầu giao hàng và xuất kho.",
                    changed_at=now,
                )

                return ShipOrderResult(
                    order=order,
                    payment=payment,
                    history_entry=history_entry,
                    transactions=created_transactions,
                    already_shipped=False,
                )

        except ConcurrencyConflict:
            if attempt >= max_retries - 1:
                raise ValidationError(
                    "Xung đột dữ liệu khi cập nhật tồn kho đồng thời. Vui lòng thử lại sau giây lát.",
                    code='concurrent_conflict'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

        except IntegrityError as ie:
            try:
                order.refresh_from_db()
                if order.order_status == OrderStatus.SHIPPING:
                    return _validate_consistent_shipped_outcome(order, payment, items)
            except Exception:
                pass
            raise ie

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


def _validate_ship_ledger_records(order: Order, items: list, error_code: str = 'inconsistent_ship_ledger') -> list[InventoryTransaction]:
    variant_aggs = {}
    for item in items:
        vid = item.variant_id
        variant_aggs[vid] = variant_aggs.get(vid, 0) + item.quantity

    ship_txs = list(
        InventoryTransaction.objects.select_for_update()
        .filter(order=order, transaction_type=InventoryTransaction.TYPE_SHIP)
        .select_related('inventory')
        .order_by('id')
    )

    if not ship_txs:
        raise ValidationError(
            "Đơn hàng thiếu bản ghi sổ cái xuất kho SHIP.",
            code='missing_ship_ledger' if error_code != 'inconsistent_replay' else 'inconsistent_replay'
        )

    if len(ship_txs) != len(variant_aggs):
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số bản ghi sổ cái SHIP ({len(ship_txs)}) "
            f"không khớp số biến thể trong đơn ({len(variant_aggs)}). Yêu cầu rà soát đối soát vận hành.",
            code=error_code
        )

    for vid, total_qty in variant_aggs.items():
        matching = [tx for tx in ship_txs if tx.inventory.variant_id == vid]
        if len(matching) != 1:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: biến thể ID {vid} có {len(matching)} bản ghi sổ cái SHIP (yêu cầu đúng 1).",
                code=error_code
            )
        tx = matching[0]
        if total_qty <= 0 or tx.quantity_delta >= 0 or tx.quantity_delta != -total_qty:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: số lượng sổ cái SHIP ({tx.quantity_delta}) "
                f"không khớp số lượng đã đặt (-{total_qty}).",
                code=error_code
            )
        if tx.physical_before is None or tx.physical_after is None:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước hoặc sau trong sổ cái SHIP không được để trống.",
                code=error_code
            )
        if tx.physical_before < 0 or tx.physical_after < 0:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước ({tx.physical_before}) "
                f"hoặc sau ({tx.physical_after}) trong sổ cái SHIP không được âm.",
                code=error_code
            )
        if tx.physical_after != tx.physical_before + tx.quantity_delta:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: phép tính tồn kho sổ cái SHIP không khớp "
                f"(physical_after {tx.physical_after} != physical_before {tx.physical_before} + quantity_delta {tx.quantity_delta}). "
                f"Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                code=error_code
            )
        if tx.order_id != order.id:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: bản ghi sổ cái SHIP không liên kết đúng đơn hàng.",
                code=error_code
            )

    return ship_txs


class DeliverOrderResult(NamedTuple):
    order: Order
    payment: Payment
    history_entry: Optional[OrderStatusHistory]
    already_delivered: bool = False


def _validate_consistent_delivered_outcome(order: Order, payment: Payment, items: list) -> DeliverOrderResult:
    if order.delivered_at is None:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: đơn DELIVERED thiếu mốc thời gian delivered_at.",
            code='inconsistent_replay'
        )

    now = timezone.now()
    if order.delivered_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian delivered_at nằm ở tương lai.",
            code='inconsistent_replay'
        )

    if order.created_at and order.delivered_at < order.created_at - timezone.timedelta(seconds=1):
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: thời điểm giao hàng trước thời điểm tạo đơn hàng.",
            code='inconsistent_replay'
        )

    if order.created_at and order.created_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian tạo đơn hàng nằm ở tương lai.",
            code='inconsistent_replay'
        )

    shipping_history_qs = OrderStatusHistory.objects.filter(
        order=order,
        old_status=OrderStatus.CONFIRMED,
        new_status=OrderStatus.SHIPPING,
    )
    if shipping_history_qs.count() != 1:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: số bản ghi lịch sử CONFIRMED -> SHIPPING là {shipping_history_qs.count()} (yêu cầu đúng 1 bản ghi).",
            code='inconsistent_replay'
        )
    shipping_history = shipping_history_qs.first()

    if shipping_history.changed_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian xuất kho nằm ở tương lai.",
            code='inconsistent_replay'
        )

    delivered_history_qs = OrderStatusHistory.objects.filter(
        order=order,
        old_status=OrderStatus.SHIPPING,
        new_status=OrderStatus.DELIVERED,
    )
    if delivered_history_qs.count() != 1:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: số bản ghi lịch sử SHIPPING -> DELIVERED là {delivered_history_qs.count()} (yêu cầu đúng 1 bản ghi).",
            code='inconsistent_replay'
        )
    delivered_history = delivered_history_qs.first()

    if delivered_history.changed_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian ghi nhận lịch sử DELIVERED nằm ở tương lai.",
            code='inconsistent_replay'
        )

    if abs((order.delivered_at - delivered_history.changed_at).total_seconds()) > 2:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: mốc thời gian delivered_at ({order.delivered_at.isoformat()}) "
            f"không khớp thời điểm ghi nhận lịch sử DELIVERED ({delivered_history.changed_at.isoformat()}).",
            code='inconsistent_replay'
        )

    if order.delivered_at < shipping_history.changed_at - timezone.timedelta(seconds=1):
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: thời điểm giao hàng trước thời điểm xuất kho.",
            code='inconsistent_replay'
        )

    if payment.expected_amount != order.grand_total:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: số tiền thanh toán ({payment.expected_amount:,.0f} đ) "
            f"lệch tổng tiền đơn ({order.grand_total:,.0f} đ).",
            code='inconsistent_replay'
        )
    if payment.method == PaymentMethod.COD:
        if payment.status not in (PaymentStatus.UNPAID, PaymentStatus.PAID):
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: đơn COD đã giao phải có thanh toán UNPAID hoặc PAID, hiện tại: {payment.status}.",
                code='inconsistent_replay'
            )
    elif payment.method == PaymentMethod.QR:
        if payment.status != PaymentStatus.PAID:
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: đơn QR đã giao phải có thanh toán PAID, hiện tại: {payment.status}.",
                code='inconsistent_replay'
            )
    else:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: phương thức thanh toán '{payment.method}' không được hỗ trợ.",
            code='inconsistent_replay'
        )

    if not items:
        raise ValidationError("Dữ liệu giao hàng không nhất quán: đơn hàng không có dòng sản phẩm.", code='inconsistent_replay')

    for item in items:
        res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
        if len(res_list) != 1:
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: dòng sản phẩm '{item.product_name_snapshot}' "
                f"có {len(res_list)} bản ghi giữ kho (yêu cầu đúng 1 bản ghi SHIPPED).",
                code='inconsistent_replay'
            )
        res = res_list[0]
        if res.status != InventoryReservationStatus.SHIPPED or res.finished_at is None:
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: dòng giữ hàng '{item.product_name_snapshot}' "
                f"chưa hoàn tất trạng thái SHIPPED (status={res.status}, finished_at={res.finished_at}).",
                code='inconsistent_replay'
            )
        if res.finished_at > now:
            raise ValidationError(
                "Dữ liệu giao hàng không nhất quán: mốc thời gian hoàn tất giữ kho nằm ở tương lai.",
                code='inconsistent_replay'
            )
        if res.variant_id != item.variant_id or res.quantity != item.quantity:
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: dòng giữ kho không khớp biến thể hoặc số lượng đã đặt.",
                code='inconsistent_replay'
            )
        if order.delivered_at < res.finished_at - timezone.timedelta(seconds=1):
            raise ValidationError(
                "Dữ liệu giao hàng không nhất quán: thời điểm giao hàng trước thời điểm hoàn tất giữ kho.",
                code='inconsistent_replay'
            )

    _validate_ship_ledger_records(order, items, error_code='inconsistent_replay')

    return DeliverOrderResult(
        order=order,
        payment=payment,
        history_entry=None,
        already_delivered=True,
    )


def deliver_order(
    order_id: int,
    user,
    delivery_note: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> DeliverOrderResult:
    if not (is_sales_staff(user) or is_admin(user)):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có thẩm quyền hoàn tất giao hàng.")

    cleaned_note = str(delivery_note or '').strip()
    if len(cleaned_note) > 500:
        raise ValidationError('Ghi chú giao hàng không được vượt quá 500 ký tự.', code='invalid_note')

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                try:
                    order = Order.objects.select_for_update().select_related('customer').prefetch_related('items').get(pk=order_id)
                except Order.DoesNotExist:
                    raise ValidationError('Đơn hàng không tồn tại.', code='order_not_found')

                payment = Payment.objects.select_for_update().filter(order=order).first()
                if not payment:
                    raise ValidationError('Đơn hàng không có bản ghi thanh toán liên kết.', code='missing_payment')

                items = list(order.items.select_for_update().select_related('variant').all())
                if not items:
                    raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='empty_order')

                if order.order_status == OrderStatus.DELIVERED:
                    return _validate_consistent_delivered_outcome(order, payment, items)

                if order.order_status != OrderStatus.SHIPPING:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thể hoàn tất giao hàng. "
                        f"Chỉ có thể hoàn tất giao hàng cho đơn hàng đang ở trạng thái Đang giao hàng (SHIPPING).",
                        code='ineligible_order_status'
                    )

                if order.delivered_at is not None:
                    raise ValidationError(
                        "Phát hiện xung đột dữ liệu: Đơn hàng ở trạng thái SHIPPING nhưng đã có mốc thời gian delivered_at. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_delivery_state'
                    )

                if OrderStatusHistory.objects.filter(order=order, old_status=OrderStatus.SHIPPING, new_status=OrderStatus.DELIVERED).exists():
                    raise ValidationError(
                        "Phát hiện xung đột dữ liệu: Đơn hàng ở trạng thái SHIPPING nhưng đã tồn tại bản ghi lịch sử DELIVERED. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_delivery_state'
                    )

                if payment.expected_amount != order.grand_total:
                    raise ValidationError(
                        f"Số tiền thanh toán dự kiến ({payment.expected_amount:,.0f} đ) không khớp với tổng tiền đơn hàng ({order.grand_total:,.0f} đ). Yêu cầu rà soát đối soát vận hành.",
                        code='amount_mismatch'
                    )

                if payment.method == PaymentMethod.COD:
                    if payment.status not in (PaymentStatus.UNPAID, PaymentStatus.PAID):
                        raise ValidationError(
                            f"Đơn hàng COD giao hàng phải có trạng thái thanh toán là Chưa thanh toán (UNPAID) hoặc Đã thanh toán (PAID). Hiện tại: {payment.get_status_display()}.",
                            code='invalid_cod_payment_status'
                        )
                elif payment.method == PaymentMethod.QR:
                    if payment.status != PaymentStatus.PAID:
                        raise ValidationError(
                            f"Đơn hàng QR chỉ được hoàn tất giao hàng khi thanh toán đã hoàn tất (PAID). Hiện tại: {payment.get_status_display()}.",
                            code='unpaid_qr_order'
                        )
                else:
                    raise ValidationError(
                        f"Phương thức thanh toán '{payment.method}' không được hỗ trợ trong quy trình giao hàng này.",
                        code='unsupported_payment_method'
                    )

                now = timezone.now()

                if order.created_at and order.created_at > now:
                    raise ValidationError(
                        "Dữ liệu đơn hàng không nhất quán: mốc thời gian tạo đơn nằm ở tương lai so với thời điểm giao hàng. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_delivery_state'
                    )

                for item in items:
                    res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
                    if len(res_list) != 1:
                        raise ValidationError(
                            f"Dòng sản phẩm '{item.product_name_snapshot}' ({item.sku_snapshot}) "
                            f"có số lượng dòng giữ kho không hợp lệ ({len(res_list)} dòng, yêu cầu đúng 1 dòng SHIPPED). "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )
                    res = res_list[0]
                    if res.status != InventoryReservationStatus.SHIPPED or res.finished_at is None:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' chưa hoàn tất xuất kho (status={res.status}, finished_at={res.finished_at}). "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )
                    if res.finished_at > now:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' có mốc hoàn tất finished_at nằm ở tương lai so với thời điểm giao hàng. "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )
                    if order.created_at and res.finished_at < order.created_at - timezone.timedelta(seconds=1):
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' có mốc hoàn tất finished_at trước thời điểm tạo đơn hàng.",
                            code='invalid_reservation_state'
                        )
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' không khớp biến thể đã đặt.",
                            code='invalid_reservation_state'
                        )
                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Số lượng giữ hàng ({res.quantity}) không khớp với số lượng đặt mua ({item.quantity}) của sản phẩm '{item.product_name_snapshot}'.",
                            code='invalid_reservation_state'
                        )

                _validate_ship_ledger_records(order, items, error_code='inconsistent_ship_ledger')

                shipping_history_qs = OrderStatusHistory.objects.filter(
                    order=order,
                    old_status=OrderStatus.CONFIRMED,
                    new_status=OrderStatus.SHIPPING,
                )
                if shipping_history_qs.count() != 1:
                    raise ValidationError(
                        f"Phát hiện xung đột dữ liệu: Đơn hàng thiếu bản ghi lịch sử CONFIRMED -> SHIPPING (hiện có {shipping_history_qs.count()} bản ghi).",
                        code='inconsistent_shipping_history'
                    )
                shipping_history = shipping_history_qs.first()
                if shipping_history.changed_at > now:
                    raise ValidationError(
                        "Dữ liệu xuất kho không nhất quán: mốc thời gian xuất kho nằm ở tương lai so với thời điểm giao hàng.",
                        code='inconsistent_shipping_history'
                    )
                if order.created_at and shipping_history.changed_at < order.created_at - timezone.timedelta(seconds=1):
                    raise ValidationError(
                        "Dữ liệu xuất kho không nhất quán: mốc thời gian xuất kho trước thời điểm tạo đơn hàng.",
                        code='inconsistent_shipping_history'
                    )

                rows_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=OrderStatus.SHIPPING,
                ).update(order_status=OrderStatus.DELIVERED, delivered_at=now)

                if rows_updated != 1:
                    order.refresh_from_db()
                    if order.order_status == OrderStatus.DELIVERED:
                        return _validate_consistent_delivered_outcome(order, payment, items)
                    raise ValidationError("Xung đột tương tranh khi cập nhật trạng thái đơn hàng sang DELIVERED.", code='concurrent_conflict')

                order.order_status = OrderStatus.DELIVERED
                order.delivered_at = now

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=OrderStatus.SHIPPING,
                    new_status=OrderStatus.DELIVERED,
                    changed_by=user,
                    reason=cleaned_note or "Giao hàng thành công đến người nhận.",
                    changed_at=now,
                )

                return DeliverOrderResult(
                    order=order,
                    payment=payment,
                    history_entry=history_entry,
                    already_delivered=False,
                )

        except ConcurrencyConflict:
            if attempt >= max_retries - 1:
                raise ValidationError(
                    "Xung đột dữ liệu khi cập nhật đơn hàng đồng thời. Vui lòng thử lại sau giây lát.",
                    code='concurrent_conflict'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

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


def _validate_consistent_cancelled_outcome(
    order: Order,
    payment: Payment,
    items: list[OrderItem],
    user,
) -> CancelOrderResult:
    if order.order_status != OrderStatus.CANCELLED:
        raise ValidationError(
            f"Trạng thái đơn hàng '{order.get_order_status_display()}' không phải là Đã hủy (CANCELLED).",
            code='inconsistent_cancelled_outcome'
        )

    if user is None or not getattr(user, 'is_authenticated', False) or user.id != order.customer_id:
        raise ValidationError(
            "Người dùng thực hiện không phải là khách hàng sở hữu đơn hàng.",
            code='inconsistent_cancelled_outcome'
        )

    cancellation = getattr(order, 'cancellation', None)
    if not cancellation:
        cancellation = OrderCancellation.objects.filter(order=order).first()
    if not cancellation:
        raise ValidationError(
            "Đơn hàng ở trạng thái CANCELLED nhưng thiếu bản ghi hủy đơn (OrderCancellation).",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.requested_by_id != order.customer_id:
        raise ValidationError(
            "Người yêu cầu hủy trong bản ghi OrderCancellation không khớp với khách hàng sở hữu đơn.",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.reason_code not in CancellationReasonCode.values:
        raise ValidationError(
            f"Mã lý do hủy '{cancellation.reason_code}' trong bản ghi OrderCancellation không hợp lệ.",
            code='inconsistent_cancelled_outcome'
        )

    cleaned_desc = str(cancellation.description or '').strip()
    if cancellation.reason_code == CancellationReasonCode.OTHER and not cleaned_desc:
        raise ValidationError(
            "Bản ghi OrderCancellation với lý do 'Khác' nhưng thiếu mô tả chi tiết.",
            code='inconsistent_cancelled_outcome'
        )

    if cleaned_desc and len(cleaned_desc) > 500:
        raise ValidationError(
            "Mô tả chi tiết trong OrderCancellation vượt quá 500 ký tự.",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.cancelled_at is None:
        raise ValidationError(
            "Bản ghi OrderCancellation thiếu thời điểm hủy (cancelled_at is NULL).",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.reservation_released_at is None:
        raise ValidationError(
            "Bản ghi OrderCancellation chưa ghi nhận thời điểm giải phóng tồn kho (reservation_released_at is NULL).",
            code='inconsistent_cancelled_outcome'
        )

    cancel_histories = list(OrderStatusHistory.objects.filter(
        order=order,
        new_status=OrderStatus.CANCELLED,
    ))
    if len(cancel_histories) != 1:
        raise ValidationError(
            f"Đơn hàng phải có chính xác 01 bản ghi lịch sử chuyển sang CANCELLED (tìm thấy {len(cancel_histories)} bản ghi).",
            code='inconsistent_cancelled_outcome'
        )
    cancel_history = cancel_histories[0]

    if cancel_history.changed_by_id != order.customer_id:
        raise ValidationError(
            "Người thực hiện trong lịch sử hủy không khớp với khách hàng sở hữu đơn.",
            code='inconsistent_cancelled_outcome'
        )

    if cancel_history.old_status not in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED):
        raise ValidationError(
            f"Trạng thái trước khi hủy trong lịch sử ('{cancel_history.old_status}') không hợp lệ cho đơn hủy trước xuất kho.",
            code='inconsistent_cancelled_outcome'
        )

    reason_display = dict(CancellationReasonCode.choices).get(cancellation.reason_code, cancellation.reason_code)
    expected_history_reason = f"Khách hàng hủy đơn: {reason_display}"
    if cancellation.description:
        expected_history_reason += f" - {cancellation.description}"

    if cancel_history.reason != expected_history_reason:
        raise ValidationError(
            f"Nội dung lý do trong lịch sử hủy không khớp với hồ sơ hủy canonical ('{cancel_history.reason}' != '{expected_history_reason}').",
            code='inconsistent_cancelled_outcome'
        )

    shipping_lifecycle_statuses = [
        OrderStatus.SHIPPING,
        OrderStatus.DELIVERED,
        OrderStatus.DELIVERY_FAILED,
        OrderStatus.RETURNING_TO_WAREHOUSE,
        OrderStatus.FAILED_CLOSED,
    ]
    if OrderStatusHistory.objects.filter(order=order, new_status__in=shipping_lifecycle_statuses).exists():
        raise ValidationError(
            "Đơn hàng tồn tại lịch sử vận chuyển hoặc giao hàng, mâu thuẫn với quy trình hủy trước xuất kho.",
            code='inconsistent_cancelled_outcome'
        )

    now = timezone.now()
    event_time = cancellation.cancelled_at
    rel_time = cancellation.reservation_released_at
    changed_time = cancel_history.changed_at

    if rel_time != event_time:
        raise ValidationError(
            "Thời điểm giải phóng giữ kho trong OrderCancellation không đồng nhất với thời điểm hủy.",
            code='inconsistent_cancelled_outcome'
        )

    if changed_time != event_time:
        raise ValidationError(
            "Thời điểm ghi nhận trong lịch sử hủy không đồng nhất với thời điểm hủy đơn.",
            code='inconsistent_cancelled_outcome'
        )

    if event_time > now:
        raise ValidationError(
            "Thời điểm hủy đơn ở tương lai so với thời gian hiện tại của hệ thống.",
            code='inconsistent_cancelled_outcome'
        )

    if rel_time > now:
        raise ValidationError(
            "Thời điểm giải phóng giữ kho ở tương lai so với thời gian hiện tại của hệ thống.",
            code='inconsistent_cancelled_outcome'
        )

    if changed_time > now:
        raise ValidationError(
            "Thời điểm ghi nhận trong lịch sử hủy ở tương lai so với thời gian hiện tại của hệ thống.",
            code='inconsistent_cancelled_outcome'
        )

    if order.created_at:
        if event_time < order.created_at:
            raise ValidationError(
                "Thời điểm hủy đơn xảy ra trước thời điểm tạo đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if rel_time < order.created_at:
            raise ValidationError(
                "Thời điểm giải phóng giữ kho xảy ra trước thời điểm tạo đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if changed_time < order.created_at:
            raise ValidationError(
                "Thời điểm ghi nhận trong lịch sử hủy xảy ra trước thời điểm tạo đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if order.created_at > now:
            raise ValidationError(
                "Thời điểm tạo đơn hàng ở tương lai so với thời gian hiện tại của hệ thống.",
                code='inconsistent_cancelled_outcome'
            )

    if payment.method != PaymentMethod.COD or payment.status != PaymentStatus.UNPAID:
        raise ValidationError(
            f"Thanh toán của đơn hàng hủy không nhất quán (phương thức: {payment.method}, trạng thái: {payment.status}).",
            code='inconsistent_cancelled_outcome'
        )
    if payment.expected_amount != order.grand_total:
        raise ValidationError(
            "Số tiền thanh toán dự kiến không khớp với tổng tiền đơn hàng.",
            code='inconsistent_cancelled_outcome'
        )
    if payment.verified_at is not None or payment.verified_by is not None:
        raise ValidationError(
            "Đơn hàng COD chưa thanh toán có thông tin xác minh thanh toán bất thường.",
            code='inconsistent_cancelled_outcome'
        )
    if PaymentTransaction.objects.filter(payment=payment).exists():
        raise ValidationError(
            "Đơn hàng có phát sinh bản ghi PaymentTransaction bất thường.",
            code='inconsistent_cancelled_outcome'
        )
    if Refund.objects.filter(order=order).exists():
        raise ValidationError(
            "Đơn hàng có phát sinh bản ghi hoàn tiền (Refund) bất thường.",
            code='inconsistent_cancelled_outcome'
        )

    if not items:
        raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='inconsistent_cancelled_outcome')

    for item in items:
        res_list = list(InventoryReservation.objects.filter(order_item=item))
        if len(res_list) != 1:
            raise ValidationError(
                f"Dòng sản phẩm '{item.product_name_snapshot}' có số lượng bản ghi giữ kho bất thường ({len(res_list)}).",
                code='inconsistent_cancelled_outcome'
            )
        res = res_list[0]
        if res.status != InventoryReservationStatus.RELEASED:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' chưa được giải phóng (trạng thái: {res.status}).",
                code='inconsistent_cancelled_outcome'
            )
        if res.finished_at is None:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' ở trạng thái RELEASED nhưng finished_at là NULL.",
                code='inconsistent_cancelled_outcome'
            )
        if res.finished_at != event_time:
            raise ValidationError(
                f"Thời điểm finished_at của dòng giữ kho ({res.finished_at}) không đồng nhất với thời điểm hủy ({event_time}).",
                code='inconsistent_cancelled_outcome'
            )
        if res.finished_at > now:
            raise ValidationError(
                f"Thời điểm finished_at của dòng giữ kho ({res.finished_at}) ở tương lai so với thời gian hiện tại.",
                code='inconsistent_cancelled_outcome'
            )
        if res.created_at:
            if res.created_at > now:
                raise ValidationError(
                    f"Thời điểm tạo giữ kho của sản phẩm '{item.product_name_snapshot}' ở tương lai.",
                    code='inconsistent_cancelled_outcome'
                )
            if res.finished_at < res.created_at:
                raise ValidationError(
                    f"Thời điểm hủy đơn xảy ra trước thời điểm tạo giữ kho của sản phẩm '{item.product_name_snapshot}'.",
                    code='inconsistent_cancelled_outcome'
                )
            if order.created_at and res.created_at < order.created_at:
                raise ValidationError(
                    f"Thời điểm tạo giữ kho trước thời điểm tạo đơn hàng.",
                    code='inconsistent_cancelled_outcome'
                )
        if res.variant_id != item.variant_id:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' lệch biến thể.",
                code='inconsistent_cancelled_outcome'
            )
        if res.quantity != item.quantity:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' lệch số lượng.",
                code='inconsistent_cancelled_outcome'
            )

    if InventoryTransaction.objects.filter(order=order).exists():
        raise ValidationError(
            "Đơn hàng đã phát sinh bản ghi biến động xuất kho (InventoryTransaction), không hợp lệ cho đơn hủy trước xuất.",
            code='inconsistent_cancelled_outcome'
        )
    if order.delivered_at is not None or order.failed_delivery_at is not None:
        raise ValidationError(
            "Đơn hàng có dấu mốc thời gian giao hàng, không hợp lệ cho đơn hủy trước xuất.",
            code='inconsistent_cancelled_outcome'
        )

    coupon_usage = getattr(order, 'coupon_usage', None)
    if not coupon_usage:
        coupon_usage = CouponUsage.objects.filter(order=order).first()

    if coupon_usage:
        if coupon_usage.status != CouponUsageStatus.USED:
            raise ValidationError(
                f"Bản ghi coupon của đơn COD phải giữ trạng thái USED. Hiện tại: {coupon_usage.status}.",
                code='inconsistent_cancelled_outcome'
            )
        if coupon_usage.discount_amount != order.discount_total:
            raise ValidationError(
                f"Mức giảm giá của coupon usage ({coupon_usage.discount_amount}) không khớp với discount_total của đơn hàng ({order.discount_total}).",
                code='inconsistent_cancelled_outcome'
            )
        if coupon_usage.order_id != order.id:
            raise ValidationError(
                "Bản ghi coupon usage không khớp với đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if coupon_usage.created_at:
            if coupon_usage.created_at > now:
                raise ValidationError(
                    "Thời điểm tạo coupon usage ở tương lai.",
                    code='inconsistent_cancelled_outcome'
                )
            if order.created_at and coupon_usage.created_at < order.created_at:
                raise ValidationError(
                    "Thời điểm tạo coupon usage trước thời điểm tạo đơn hàng.",
                    code='inconsistent_cancelled_outcome'
                )
    else:
        if order.discount_total > Decimal('0.00'):
            raise ValidationError(
                "Đơn hàng có giảm giá nhưng thiếu bản ghi sử dụng coupon.",
                code='inconsistent_cancelled_outcome'
            )

    return CancelOrderResult(
        order=order,
        cancellation=cancellation,
        history_entry=cancel_history,
        is_replay=True,
    )


def cancel_customer_cod_order(
    order_id: int,
    customer,
    reason_code: str,
    description: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.05,
) -> CancelOrderResult:
    if not (customer and customer.is_authenticated and customer.is_active):
        raise PermissionDenied("Yêu cầu đăng nhập tài khoản Khách hàng để thực hiện hủy đơn.")

    if not is_customer(customer):
        raise PermissionDenied("Bạn không có quyền thực hiện chức năng này. Chỉ tài khoản Khách hàng mới có quyền hủy đơn hàng của mình.")

    if not reason_code or reason_code not in CancellationReasonCode.values:
        raise ValidationError(
            f"Lý do hủy đơn không hợp lệ: '{reason_code}'.",
            code='invalid_cancellation_reason'
        )

    cleaned_desc = str(description or '').strip()
    if reason_code == CancellationReasonCode.OTHER:
        if not cleaned_desc:
            raise ValidationError(
                {'description': 'Vui lòng nhập mô tả chi tiết khi chọn lý do "Khác".'},
                code='missing_other_description'
            )

    if cleaned_desc and len(cleaned_desc) > 500:
        raise ValidationError(
            {'description': 'Mô tả chi tiết không được vượt quá 500 ký tự.'},
            code='description_too_long'
        )

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                try:
                    order = Order.objects.select_related('customer').prefetch_related('items').get(pk=order_id)
                except Order.DoesNotExist:
                    raise ValidationError('Đơn hàng không tồn tại.', code='order_not_found')

                if order.customer_id != customer.id:
                    raise PermissionDenied("Bạn không có quyền hủy đơn hàng của khách hàng khác.")

                event_now = timezone.now()
                if order.created_at and order.created_at > event_now:
                    raise ValidationError(
                        "Thời điểm tạo đơn hàng ở tương lai, không thể xử lý hủy đơn.",
                        code='invalid_order_chronology'
                    )

                payment = Payment.objects.filter(order=order).first()
                if not payment:
                    raise ValidationError('Đơn hàng không có bản ghi thanh toán liên kết.', code='missing_payment')

                items = list(order.items.all().order_by('id'))

                if order.order_status == OrderStatus.CANCELLED:
                    return _validate_consistent_cancelled_outcome(order, payment, items, customer)

                if order.order_status not in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED):
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thể hủy trước xuất kho.",
                        code='ineligible_order_status'
                    )

                if payment.method != PaymentMethod.COD:
                    raise ValidationError(
                        f"Chức năng hủy trực tiếp chỉ áp dụng cho đơn hàng COD. Phương thức hiện tại: {payment.get_method_display()}.",
                        code='ineligible_payment_method'
                    )

                if payment.status != PaymentStatus.UNPAID:
                    raise ValidationError(
                        f"Đơn hàng COD chỉ được hủy khi chưa thanh toán (UNPAID). Trạng thái hiện tại: {payment.get_status_display()}.",
                        code='invalid_cod_payment_status'
                    )

                if payment.expected_amount != order.grand_total:
                    raise ValidationError(
                        f"Số tiền thanh toán dự kiến ({payment.expected_amount:,.0f} đ) không khớp với tổng tiền đơn hàng ({order.grand_total:,.0f} đ).",
                        code='amount_mismatch'
                    )

                if payment.verified_at is not None or payment.verified_by is not None:
                    raise ValidationError(
                        "Đơn hàng có thông tin xác minh thanh toán bất thường, không thể hủy tự động.",
                        code='inconsistent_payment_verification'
                    )

                if PaymentTransaction.objects.filter(payment=payment).exists():
                    raise ValidationError(
                        "Đơn hàng đã phát sinh giao dịch thanh toán thực tế, không thể hủy.",
                        code='existing_payment_transaction'
                    )

                if Refund.objects.filter(order=order).exists():
                    raise ValidationError(
                        "Đơn hàng đã phát sinh bản ghi hoàn tiền, không thể hủy.",
                        code='existing_refund'
                    )

                if order.delivered_at is not None or order.failed_delivery_at is not None:
                    raise ValidationError(
                        "Đơn hàng đã có dấu mốc thời gian giao hàng, không thuộc diện hủy trước xuất kho.",
                        code='already_delivered'
                    )

                if InventoryTransaction.objects.filter(order=order).exists():
                    raise ValidationError(
                        "Đơn hàng đã phát sinh bản ghi xuất kho (SHIP), không thể hủy trước xuất kho.",
                        code='existing_stock_issue'
                    )

                shipping_lifecycle_statuses = [
                    OrderStatus.SHIPPING,
                    OrderStatus.DELIVERED,
                    OrderStatus.DELIVERY_FAILED,
                    OrderStatus.RETURNING_TO_WAREHOUSE,
                    OrderStatus.FAILED_CLOSED,
                ]
                if OrderStatusHistory.objects.filter(order=order, new_status__in=shipping_lifecycle_statuses).exists():
                    raise ValidationError(
                        "Đơn hàng đã từng bước qua quy trình vận chuyển, không thể hủy trước xuất kho.",
                        code='invalid_lifecycle_history'
                    )

                if OrderCancellation.objects.filter(order=order).exists():
                    raise ValidationError(
                        "Đơn hàng đã tồn tại bản ghi hủy đơn mâu thuẫn với trạng thái chưa hủy hiện thời.",
                        code='contradictory_cancellation_record'
                    )

                if OrderStatusHistory.objects.filter(order=order, new_status=OrderStatus.CANCELLED).exists():
                    raise ValidationError(
                        "Đơn hàng đã tồn tại lịch sử trạng thái CANCELLED mâu thuẫn với trạng thái chưa hủy hiện thời.",
                        code='contradictory_cancellation_history'
                    )

                if not items:
                    raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='empty_order')

                variant_reservations = {}
                for item in items:
                    res_list = list(InventoryReservation.objects.filter(order_item=item))
                    if len(res_list) != 1:
                        raise ValidationError(
                            f"Dòng sản phẩm '{item.product_name_snapshot}' ({item.sku_snapshot}) "
                            f"có số dòng giữ kho không hợp lệ ({len(res_list)} dòng). Cần rà soát đối soát vận hành.",
                            code='invalid_reservation_state'
                        )
                    res = res_list[0]
                    if res.status != InventoryReservationStatus.ACTIVE:
                        raise ValidationError(
                            f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' không ở trạng thái ACTIVE (hiện tại: {res.status}).",
                            code='invalid_reservation_state'
                        )
                    if res.finished_at is not None:
                        raise ValidationError(
                            f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' có finished_at không hợp lệ khi còn ACTIVE.",
                            code='invalid_reservation_state'
                        )
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' không khớp biến thể đã đặt.",
                            code='invalid_reservation_state'
                        )
                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Số lượng giữ kho ({res.quantity}) không khớp với số lượng đặt ({item.quantity}) của '{item.product_name_snapshot}'.",
                            code='invalid_reservation_state'
                        )
                    if res.created_at:
                        if res.created_at > event_now:
                            raise ValidationError(
                                f"Thời điểm tạo giữ kho của sản phẩm '{item.product_name_snapshot}' ở tương lai.",
                                code='invalid_reservation_chronology'
                            )
                        if order.created_at and res.created_at < order.created_at:
                            raise ValidationError(
                                f"Thời điểm tạo giữ kho trước thời điểm tạo đơn hàng.",
                                code='invalid_reservation_chronology'
                            )
                    variant_reservations.setdefault(item.variant_id, []).append(res)

                coupon_usage = getattr(order, 'coupon_usage', None)
                if not coupon_usage:
                    coupon_usage = CouponUsage.objects.filter(order=order).first()

                if coupon_usage:
                    if coupon_usage.status != CouponUsageStatus.USED:
                        raise ValidationError(
                            f"Bản ghi coupon của đơn COD phải ở trạng thái USED. Hiện tại: {coupon_usage.status}.",
                            code='invalid_coupon_usage_status'
                        )
                    if coupon_usage.discount_amount != order.discount_total:
                        raise ValidationError(
                            f"Mức giảm giá của coupon usage ({coupon_usage.discount_amount}) không khớp với discount_total của đơn hàng ({order.discount_total}).",
                            code='inconsistent_coupon_discount'
                        )
                    if coupon_usage.order_id != order.id:
                        raise ValidationError(
                            "Bản ghi coupon usage không khớp với đơn hàng.",
                            code='inconsistent_coupon_owner'
                        )
                    if coupon_usage.created_at:
                        if coupon_usage.created_at > event_now:
                            raise ValidationError(
                                "Thời điểm tạo coupon usage ở tương lai.",
                                code='invalid_coupon_chronology'
                            )
                        if order.created_at and coupon_usage.created_at < order.created_at:
                            raise ValidationError(
                                "Thời điểm tạo coupon usage trước thời điểm tạo đơn hàng.",
                                code='invalid_coupon_chronology'
                            )
                else:
                    if order.discount_total > Decimal('0.00'):
                        raise ValidationError(
                            "Đơn hàng có giảm giá nhưng thiếu bản ghi sử dụng coupon.",
                            code='missing_coupon_usage'
                        )

                sorted_variant_ids = sorted(variant_reservations.keys())

                for vid in sorted_variant_ids:
                    tot_qty = sum(r.quantity for r in variant_reservations[vid])
                    inv = Inventory.objects.filter(variant_id=vid).first()
                    if not inv:
                        raise ValidationError(
                            f"Không tìm thấy bản ghi tồn kho cho biến thể ID {vid}.",
                            code='missing_inventory'
                        )
                    if inv.reserved < tot_qty:
                        raise ValidationError(
                            f"Tồn đã giữ của biến thể SKU '{inv.variant.sku}' ({inv.reserved}) không đủ để giải phóng ({tot_qty}).",
                            code='inconsistent_inventory'
                        )

                old_status = order.order_status

                rows_order = Order.objects.filter(
                    pk=order.pk,
                    order_status=old_status,
                ).update(order_status=OrderStatus.CANCELLED)

                if rows_order != 1:
                    raise ConcurrencyConflict("Xung đột trạng thái đơn hàng khi thực hiện hủy đơn.")

                for vid in sorted_variant_ids:
                    tot_qty = sum(r.quantity for r in variant_reservations[vid])
                    inv = Inventory.objects.get(variant_id=vid)
                    rows_inv = Inventory.objects.filter(
                        pk=inv.pk,
                        reserved=inv.reserved,
                        reserved__gte=tot_qty,
                    ).update(
                        reserved=F('reserved') - tot_qty,
                        updated_at=event_now,
                    )
                    if rows_inv != 1:
                        raise ConcurrencyConflict(f"Xung đột tồn kho khi giải phóng giữ chỗ biến thể SKU '{inv.variant.sku}'.")

                for vid in sorted_variant_ids:
                    for res in variant_reservations[vid]:
                        rows_res = InventoryReservation.objects.filter(
                            pk=res.pk,
                            status=InventoryReservationStatus.ACTIVE,
                            finished_at__isnull=True,
                        ).update(
                            status=InventoryReservationStatus.RELEASED,
                            finished_at=event_now,
                        )
                        if rows_res != 1:
                            raise ConcurrencyConflict(f"Xung đột giữ kho khi giải phóng dòng ID {res.pk}.")

                cancellation = OrderCancellation.objects.create(
                    order=order,
                    reason_code=reason_code,
                    description=cleaned_desc if cleaned_desc else None,
                    requested_by=customer,
                    cancelled_at=event_now,
                    reservation_released_at=event_now,
                )

                reason_display = dict(CancellationReasonCode.choices).get(reason_code, reason_code)
                history_reason = f"Khách hàng hủy đơn: {reason_display}"
                if cleaned_desc:
                    history_reason += f" - {cleaned_desc}"

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=old_status,
                    new_status=OrderStatus.CANCELLED,
                    changed_by=customer,
                    reason=history_reason,
                    changed_at=event_now,
                )

                order.refresh_from_db()

                return CancelOrderResult(
                    order=order,
                    cancellation=cancellation,
                    history_entry=history_entry,
                    is_replay=False,
                )

        except (ConcurrencyConflict, OperationalError) as exc:
            if isinstance(exc, OperationalError) and not _is_sqlite_lock_error(exc):
                raise
            if attempt == max_retries - 1:
                raise ValidationError(
                    "Hệ thống đang bận do có thao tác xử lý đồng thời trên đơn hàng hoặc kho hàng. Vui lòng thử lại sau.",
                    code='concurrency_exhausted'
                )
            backoff_delay = initial_retry_delay * (2 ** attempt) + random.uniform(0.01, 0.03)
            time.sleep(backoff_delay)
