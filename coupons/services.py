import random
import time
from django.core.exceptions import ValidationError
from django.db import transaction, OperationalError, IntegrityError
from django.db.models import F
from coupons.models import Coupon, CouponProduct


def execute_sqlite_safe_save(save_callable, max_retries=5, initial_delay=0.03):
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                return save_callable()
        except OperationalError as exc:
            err_msg = str(exc).lower()
            if 'locked' in err_msg or 'busy' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError(
                        "Hệ thống đang bận do có thao tác ghi đồng thời hoặc dữ liệu đang được cập nhật. Vui lòng thử lại sau giây lát.",
                        code='database_busy'
                    )
                time.sleep(initial_delay * (attempt + 1) + random.uniform(0.01, 0.03))
                continue
            raise


def update_coupon_admin_fields(coupon_id, target_fields, selected_products=None):
    fresh = Coupon.objects.filter(pk=coupon_id).first()
    if not fresh:
        raise ValidationError("Mã voucher không tồn tại.")

    target_max_uses = target_fields['max_uses']
    current_consumed = (fresh.used_count or 0) + (fresh.reserved_count or 0)
    if target_max_uses < current_consumed:
        raise ValidationError(
            f"Tổng số lượt phát hành ({target_max_uses}) không thể nhỏ hơn tổng số lượt đã dùng và đang giữ ({current_consumed})."
        )

    rows_updated = Coupon.objects.filter(
        pk=coupon_id
    ).annotate(
        consumed_count=F('used_count') + F('reserved_count')
    ).filter(
        consumed_count__lte=target_max_uses
    ).update(
        code=target_fields['code'].strip().upper(),
        discount_type=target_fields['discount_type'],
        value=target_fields['value'],
        max_discount=target_fields.get('max_discount'),
        min_order_amount=target_fields.get('min_order_amount', 0),
        max_uses=target_max_uses,
        starts_at=target_fields['starts_at'],
        ends_at=target_fields['ends_at'],
        is_active=target_fields['is_active'],
        applies_to_all=target_fields['applies_to_all'],
    )

    if rows_updated == 0:
        fresh = Coupon.objects.get(pk=coupon_id)
        current_consumed = (fresh.used_count or 0) + (fresh.reserved_count or 0)
        raise ValidationError(
            f"Tổng số lượt phát hành ({target_max_uses}) không thể nhỏ hơn tổng số lượt đã dùng và đang giữ ({current_consumed})."
        )

    updated_coupon = Coupon.objects.get(pk=coupon_id)

    if selected_products is not None:
        if target_fields['applies_to_all']:
            CouponProduct.objects.filter(coupon=updated_coupon).delete()
        else:
            current_pids = set(CouponProduct.objects.filter(coupon=updated_coupon).values_list('product_id', flat=True))
            new_pids = set(p.id if hasattr(p, 'id') else p for p in selected_products)

            to_delete = current_pids - new_pids
            if to_delete:
                CouponProduct.objects.filter(coupon=updated_coupon, product_id__in=to_delete).delete()

            to_add = new_pids - current_pids
            if to_add:
                new_mappings = [
                    CouponProduct(coupon=updated_coupon, product_id=pid)
                    for pid in to_add
                ]
                CouponProduct.objects.bulk_create(new_mappings)

    return updated_coupon

