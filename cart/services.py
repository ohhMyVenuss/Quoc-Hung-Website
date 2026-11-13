from decimal import Decimal
import random
import re
import time
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction, OperationalError, IntegrityError
from django.db.models import Sum
from django.shortcuts import get_object_or_404

from accounts.permissions import is_customer
from catalog.models import ProductVariant, ProductImage
from catalog.utils import format_vnd
from cart.models import Cart, CartItem


def parse_strictly_positive_int(raw_val, max_val=2147483647):

    if raw_val is None:
        raise ValidationError("Số lượng không được để trống.", code='required')

    val_str = str(raw_val).strip()
    if not val_str:
        raise ValidationError("Số lượng không được để trống.", code='blank')

    if not val_str.isascii():
        raise ValidationError("Số lượng chỉ được chứa các chữ số ASCII thuần (0-9).", code='invalid_ascii')

    if not re.fullmatch(r'[1-9][0-9]*', val_str):
        raise ValidationError("Số lượng phải là số nguyên dương lớn hơn 0 (không chứa dấu, số thập phân hoặc ký tự đặc biệt).", code='invalid_format')

    try:
        val = int(val_str)
    except (ValueError, OverflowError):
        raise ValidationError("Số lượng vượt quá giới hạn hỗ trợ của hệ thống.", code='overflow')

    if val > max_val:
        raise ValidationError(f"Số lượng không được vượt quá {max_val}.", code='max_value')

    return val


def get_variant_availability(variant):

    try:
        inv = variant.inventory
        if inv is None:
            return 0
        return max(0, inv.available)
    except (AttributeError, ObjectDoesNotExist):
        return 0


def _execute_with_sqlite_retry(fn, max_retries=10, initial_delay=0.03):

    from django.contrib.sessions.backends.base import UpdateError, CreateError
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                return fn()
        except (OperationalError, UpdateError, CreateError) as exc:
            err_msg = str(exc).lower()
            if isinstance(exc, (UpdateError, CreateError)) or 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError(
                        "Hệ thống đang bận xử lý giao dịch giỏ hàng khác. Vui lòng thử lại sau giây lát.",
                        code='database_busy'
                    )
                time.sleep(initial_delay * (attempt + 1) + random.uniform(0.01, 0.04))
                continue
            raise
        except IntegrityError:
            if attempt == max_retries - 1:
                raise
            time.sleep(initial_delay * (attempt + 1) + random.uniform(0.01, 0.04))
            continue


retry_on_db_lock = _execute_with_sqlite_retry


def _get_or_create_user_cart(user, max_retries=10):

    for attempt in range(max_retries):
        try:
            cart = Cart.objects.filter(user=user).first()
            if cart:
                return cart
            with transaction.atomic():
                return Cart.objects.create(user=user)
        except IntegrityError:
            time.sleep(0.02 * (attempt + 1) + random.uniform(0.01, 0.03))
            try:
                cart = Cart.objects.filter(user=user).first()
                if cart:
                    return cart
            except OperationalError:
                pass
            if attempt == max_retries - 1:
                return Cart.objects.get(user=user)
            continue
        except OperationalError as exc:
            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                time.sleep(0.03 * (attempt + 1) + random.uniform(0.01, 0.04))
                try:
                    cart = Cart.objects.filter(user=user).first()
                    if cart:
                        return cart
                except OperationalError:
                    pass
                if attempt == max_retries - 1:
                    raise ValidationError(
                        "Hệ thống đang bận xử lý giỏ hàng của bạn. Vui lòng thử lại.",
                        code='database_busy'
                    )
                continue
            raise
    return Cart.objects.get(user=user)


def _get_or_create_session_cart(session_key, max_retries=10):

    for attempt in range(max_retries):
        try:
            cart = Cart.objects.filter(session_key=session_key, user__isnull=True).first()
            if cart:
                return cart
            with transaction.atomic():
                return Cart.objects.create(session_key=session_key, user=None)
        except IntegrityError:
            time.sleep(0.02 * (attempt + 1) + random.uniform(0.01, 0.03))
            try:
                cart = Cart.objects.filter(session_key=session_key, user__isnull=True).first()
                if cart:
                    return cart
            except OperationalError:
                pass
            if attempt == max_retries - 1:
                return Cart.objects.get(session_key=session_key, user__isnull=True)
            continue
        except OperationalError as exc:
            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                time.sleep(0.03 * (attempt + 1) + random.uniform(0.01, 0.04))
                try:
                    cart = Cart.objects.filter(session_key=session_key, user__isnull=True).first()
                    if cart:
                        return cart
                except OperationalError:
                    pass
                if attempt == max_retries - 1:
                    raise ValidationError(
                        "Hệ thống đang bận xử lý giỏ hàng. Vui lòng thử lại.",
                        code='database_busy'
                    )
                continue
            raise
    return Cart.objects.get(session_key=session_key, user__isnull=True)


def resolve_cart(request=None, create_if_empty=False, user=None, session_key=None):

    if user is None and request is not None:
        UserModel = get_user_model()
        if isinstance(request, UserModel):
            user = request
        else:
            user = getattr(request, 'user', None)

    if user and user.is_authenticated:
        if not user.is_active:
            return None
        if not is_customer(user):
            return None

        if create_if_empty:
            return _get_or_create_user_cart(user)
        return Cart.objects.filter(user=user).first()

    if session_key is None and request is not None:
        session = getattr(request, 'session', None)
        if session is not None:
            session_key = session.session_key
            if not session_key and create_if_empty:
                session.save()
                session_key = session.session_key

    if not session_key:
        return None

    if create_if_empty:
        return _get_or_create_session_cart(session_key)
    return Cart.objects.filter(session_key=session_key, user__isnull=True).first()


def add_to_cart(cart, variant_id, raw_quantity):

    qty = parse_strictly_positive_int(raw_quantity)
    cart_id = cart.pk

    def _do_add():
        c = Cart.objects.get(id=cart_id)
        try:
            variant = ProductVariant.objects.select_related(
                'product__category',
                'inventory'
            ).get(id=variant_id)
        except (ProductVariant.DoesNotExist, ValueError):
            raise ValidationError("Biến thể sản phẩm không tồn tại.", code='variant_not_found')

        product = variant.product
        category = product.category

        if not category.is_visible:
            raise ValidationError("Sản phẩm thuộc danh mục đang tạm ẩn, không thể thêm vào giỏ hàng.", code='category_hidden')

        if not product.is_active or product.is_deleted:
            raise ValidationError("Sản phẩm đã ngừng kinh doanh hoặc không còn tồn tại.", code='product_inactive')

        if not variant.is_active:
            raise ValidationError("Phiên bản sản phẩm đã ngừng kinh doanh.", code='variant_inactive')

        available = get_variant_availability(variant)
        if available <= 0:
            raise ValidationError("Sản phẩm hiện đang tạm hết hàng.", code='out_of_stock')

        existing_item = c.items.filter(variant=variant).first()
        if existing_item:
            target_quantity = existing_item.quantity + qty
            if target_quantity > available:
                raise ValidationError(
                    f"Số lượng thêm ({qty}) cộng với số lượng đã có trong giỏ ({existing_item.quantity}) "
                    f"vượt quá tồn kho khả dụng ({available}).",
                    code='stock_exceeded'
                )
            existing_item.quantity = target_quantity
            if existing_item.price_at_addition is None:
                existing_item.price_at_addition = variant.price
            existing_item.save(update_fields=['quantity', 'price_at_addition', 'updated_at'])
            return existing_item, False
        else:
            if qty > available:
                raise ValidationError(
                    f"Số lượng yêu cầu ({qty}) vượt quá tồn kho khả dụng ({available}).",
                    code='stock_exceeded'
                )
            new_item = CartItem.objects.create(
                cart=c,
                variant=variant,
                quantity=qty,
                price_at_addition=variant.price,
            )
            return new_item, True

    return _execute_with_sqlite_retry(_do_add)


def update_cart_item_quantity(cart, item_id, raw_quantity):

    qty = parse_strictly_positive_int(raw_quantity)
    cart_id = cart.pk

    def _do_update():
        c = Cart.objects.get(id=cart_id)
        item = get_object_or_404(
            CartItem.objects.select_related('variant__inventory', 'variant__product__category'),
            id=item_id,
            cart=c,
        )

        variant = item.variant
        product = variant.product
        category = product.category

        if not category.is_visible or not product.is_active or product.is_deleted or not variant.is_active:
            raise ValidationError("Sản phẩm hoặc phiên bản này hiện không còn khả dụng để đặt mua.", code='variant_unavailable')

        available = get_variant_availability(variant)
        if qty > available:
            raise ValidationError(
                f"Số lượng yêu cầu ({qty}) vượt quá tồn kho khả dụng ({available}).",
                code='stock_exceeded'
            )

        item.quantity = qty
        item.price_at_addition = variant.price
        item.save(update_fields=['quantity', 'price_at_addition', 'updated_at'])
        return item

    return _execute_with_sqlite_retry(_do_update)


def switch_cart_item_variant(cart, item_id, new_variant_id):

    cart_id = cart.pk

    def _do_switch():
        c = Cart.objects.get(id=cart_id)
        source_item = get_object_or_404(
            CartItem.objects.select_related('variant__product', 'variant__inventory'),
            id=item_id,
            cart=c,
        )

        try:
            new_variant = ProductVariant.objects.select_related(
                'product__category',
                'inventory'
            ).get(id=new_variant_id)
        except (ProductVariant.DoesNotExist, ValueError):
            raise ValidationError("Biến thể chuyển đổi không tồn tại.", code='variant_not_found')

        if new_variant.product_id != source_item.variant.product_id:
            raise ValidationError("Biến thể được chọn không thuộc cùng sản phẩm với biến thể hiện tại.", code='different_product')

        product = new_variant.product
        category = product.category
        if not category.is_visible or not product.is_active or product.is_deleted:
            raise ValidationError("Sản phẩm không còn hoạt động hoặc danh mục đang tạm ẩn.", code='product_unavailable')

        if not new_variant.is_active:
            raise ValidationError("Phiên bản mới được chọn hiện đã ngừng kinh doanh.", code='variant_inactive')

        if new_variant.id == source_item.variant_id:
            return source_item

        available = get_variant_availability(new_variant)

        dest_item = c.items.filter(variant=new_variant).first()
        if dest_item:
            combined_quantity = source_item.quantity + dest_item.quantity
            if combined_quantity > available:
                raise ValidationError(
                    f"Biến thể đích đã có trong giỏ hàng. Tổng số lượng kết hợp ({combined_quantity}) "
                    f"vượt quá tồn kho khả dụng ({available}).",
                    code='stock_exceeded'
                )
            dest_item.quantity = combined_quantity
            if dest_item.price_at_addition is None:
                dest_item.price_at_addition = new_variant.price
            dest_item.save(update_fields=['quantity', 'price_at_addition', 'updated_at'])
            source_item.delete()
            return dest_item
        else:
            if source_item.quantity > available:
                raise ValidationError(
                    f"Phiên bản mới chỉ còn {available} sản phẩm khả dụng, "
                    f"không đủ cho số lượng {source_item.quantity} hiện có trong giỏ hàng.",
                    code='stock_exceeded'
                )
            source_item.variant = new_variant
            source_item.price_at_addition = new_variant.price
            source_item.save(update_fields=['variant', 'price_at_addition', 'updated_at'])
            return source_item

    return _execute_with_sqlite_retry(_do_switch)


def remove_cart_item(cart, item_id):

    cart_id = cart.pk

    def _do_remove():
        c = Cart.objects.get(id=cart_id)
        item = get_object_or_404(CartItem, id=item_id, cart=c)
        item.delete()
        return True

    return _execute_with_sqlite_retry(_do_remove)


def merge_guest_cart_to_user(guest_session_key, user):

    session_key = getattr(guest_session_key, 'session_key', guest_session_key)
    if not session_key or not user or not user.is_authenticated or not user.is_active:
        return None
    if not is_customer(user):
        return None

    def _do_merge():
        guest_cart = Cart.objects.filter(session_key=session_key, user__isnull=True).first()
        if not guest_cart:
            return _get_or_create_user_cart(user)

        user_cart = _get_or_create_user_cart(user)

        guest_items = list(guest_cart.items.select_related('variant').all())
        for g_item in guest_items:
            u_item = user_cart.items.filter(variant=g_item.variant).first()
            if u_item:
                u_item.quantity = u_item.quantity + g_item.quantity

                curr_price = g_item.variant.price
                u_ref = u_item.price_at_addition
                g_ref = g_item.price_at_addition

                if u_ref is not None and u_ref != curr_price:
                    final_ref = u_ref
                elif g_ref is not None and g_ref != curr_price:
                    final_ref = g_ref
                else:
                    final_ref = u_ref if u_ref is not None else (g_ref if g_ref is not None else curr_price)

                u_item.price_at_addition = final_ref
                u_item.save(update_fields=['quantity', 'price_at_addition', 'updated_at'])
                g_item.delete()
            else:
                g_item.cart = user_cart
                g_item.save(update_fields=['cart', 'updated_at'])

        guest_cart.delete()
        return user_cart

    return _execute_with_sqlite_retry(_do_merge)


def _format_variant_attributes_display(variant):
    links = list(variant.attribute_values.all())
    if links:
        links.sort(key=lambda x: (x.attribute.name, x.id))
        return ", ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
    return "Phiên bản mặc định"


def _resolve_variant_best_image(product, variant):
    all_product_images = list(product.images.all())
    variant_images = [img for img in all_product_images if img.variant_id == variant.id]
    if variant_images:
        return variant_images[0]
    general_images = [img for img in all_product_images if img.variant_id is None]
    return general_images[0] if general_images else None


def _evaluate_cart_line_availability(category, product, variant, available_stock, requested_quantity):

    if not category.is_visible:
        return False, 'CATEGORY_HIDDEN', 'Danh mục đang tạm ẩn', 'qhm-badge-unavailable'
    if not product.is_active or product.is_deleted:
        return False, 'PRODUCT_INACTIVE', 'Sản phẩm ngừng bán', 'qhm-badge-unavailable'
    if not variant.is_active:
        return False, 'VARIANT_INACTIVE', 'Phiên bản ngừng bán', 'qhm-badge-unavailable'
    if available_stock <= 0:
        return False, 'OUT_OF_STOCK', 'Hết hàng (Còn 0 SP)', 'qhm-badge-out-of-stock'
    if requested_quantity > available_stock:
        return False, 'INSUFFICIENT_STOCK', f'Tồn kho không đủ (Còn {available_stock} SP)', 'qhm-badge-warning'
    return True, 'AVAILABLE', 'Còn hàng', 'qhm-badge-in-stock'


def _get_alternative_variants_for_cart(product, current_variant):
    other_variants = []
    for other_v in product.variants.filter(is_active=True).exclude(id=current_variant.id):
        o_links = list(other_v.attribute_values.all())
        if o_links:
            o_links.sort(key=lambda x: (x.attribute.name, x.id))
            o_label = ", ".join(f"{l.attribute.name}: {l.attribute_value.value}" for l in o_links)
        else:
            o_label = "Mặc định"
        o_avail = get_variant_availability(other_v)
        other_variants.append({
            'id': other_v.id,
            'label': o_label,
            'price_formatted': format_vnd(other_v.price),
            'available': o_avail,
        })
    return other_variants


def get_cart_evaluation(cart):

    if not cart:
        return {
            'lines': [],
            'subtotal_available': Decimal('0'),
            'subtotal_available_formatted': format_vnd(Decimal('0')),
            'subtotal_all': Decimal('0'),
            'subtotal_all_formatted': format_vnd(Decimal('0')),
            'has_unavailable_lines': False,
            'unavailable_count': 0,
            'total_quantity': 0,
        }

    items = list(
        cart.items.select_related(
            'variant__product__category',
            'variant__inventory'
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
            'variant__product__images',
            'variant__product__variants__attribute_values__attribute',
            'variant__product__variants__attribute_values__attribute_value',
            'variant__product__variants__inventory',
        ).order_by('id')
    )

    lines = []
    subtotal_available = Decimal('0')
    subtotal_all = Decimal('0')
    unavailable_count = 0
    total_quantity = 0

    for item in items:
        variant = item.variant
        product = variant.product
        category = product.category

        attrs_display = _format_variant_attributes_display(variant)
        item_image = _resolve_variant_best_image(product, variant)
        available_stock = get_variant_availability(variant)

        is_available, status_code, status_text, status_badge_class = _evaluate_cart_line_availability(
            category, product, variant, available_stock, item.quantity
        )

        if not is_available:
            unavailable_count += 1

        current_unit_price = variant.price if variant.price is not None else Decimal('0')
        line_total = current_unit_price * Decimal(item.quantity)

        subtotal_all += line_total
        if is_available:
            subtotal_available += line_total

        total_quantity += item.quantity

        price_changed = False
        old_price_formatted = ""
        if item.price_at_addition is not None and item.price_at_addition != current_unit_price:
            price_changed = True
            old_price_formatted = format_vnd(item.price_at_addition)

        other_variants = _get_alternative_variants_for_cart(product, variant)

        lines.append({
            'item': item,
            'variant': variant,
            'product': product,
            'image': item_image,
            'attributes_display': attrs_display,
            'current_price': current_unit_price,
            'current_price_formatted': format_vnd(current_unit_price),
            'original_price_formatted': format_vnd(variant.original_price) if variant.original_price else "",
            'line_total': line_total,
            'line_total_formatted': format_vnd(line_total),
            'has_price_changed': price_changed,
            'old_price_formatted': old_price_formatted,
            'available_stock': available_stock,
            'is_available': is_available,
            'status_code': status_code,
            'status_text': status_text,
            'status_badge_class': status_badge_class,
            'other_variants': other_variants,
        })

    return {
        'lines': lines,
        'subtotal_available': subtotal_available,
        'subtotal_available_formatted': format_vnd(subtotal_available),
        'subtotal_all': subtotal_all,
        'subtotal_all_formatted': format_vnd(subtotal_all),
        'has_unavailable_lines': unavailable_count > 0,
        'unavailable_count': unavailable_count,
        'total_quantity': total_quantity,
    }
