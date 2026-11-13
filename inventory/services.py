import time
import uuid
import random
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from accounts.permissions import is_inventory_staff
from inventory.models import Inventory, InventoryTransaction, InventoryAdjustment

MAX_INTEGER_LIMIT = 2147483647
SALT_RECEIVE_TOKEN = 'qhm.inventory.receive'
SALT_ADJUSTMENT_TOKEN = 'qhm.inventory.adjust'


class ConcurrencyConflict(Exception):
    pass


def generate_receiving_token(inventory_id: int, actor_id: int) -> tuple[str, uuid.UUID]:
    request_key = uuid.uuid4()
    payload = {
        'request_key': str(request_key),
        'inventory_id': inventory_id,
        'actor_id': actor_id,
    }
    signed_token = signing.dumps(payload, salt=SALT_RECEIVE_TOKEN)
    return signed_token, request_key


def verify_receiving_token(token: str, inventory_id: int, actor_id: int) -> uuid.UUID:
    if not token or not isinstance(token, str):
        raise ValidationError('Mã xác thực yêu cầu nhập hàng không được để trống.', code='invalid_token')

    try:
        payload = signing.loads(token, salt=SALT_RECEIVE_TOKEN, max_age=86400)
    except signing.SignatureExpired:
        raise ValidationError('Phiên làm việc nhập hàng đã hết hạn (quá 24 giờ). Vui lòng tải lại trang.', code='token_expired')
    except (signing.BadSignature, Exception):
        raise ValidationError('Mã xác thực yêu cầu nhập hàng không hợp lệ hoặc đã bị thay đổi.', code='invalid_token')

    if payload.get('inventory_id') != inventory_id:
        raise ValidationError('Mã xác thực không khớp với bản ghi tồn kho đang thao tác.', code='invalid_token_binding')

    if payload.get('actor_id') != actor_id:
        raise ValidationError('Mã xác thực không khớp với tài khoản người thực hiện.', code='invalid_token_binding')

    raw_key = payload.get('request_key')
    try:
        return uuid.UUID(raw_key)
    except (ValueError, TypeError):
        raise ValidationError('Mã định danh yêu cầu trong token không đúng định dạng UUID.', code='invalid_token')


def receive_purchase(
    inventory_id: int,
    quantity: int,
    request_token: str,
    actor,
    max_retries: int = 5
) -> tuple[InventoryTransaction, bool]:
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('Người thực hiện thao tác chưa được xác thực hoặc tài khoản bị vô hiệu hóa.')

    if not is_inventory_staff(actor):
        raise PermissionDenied('Bạn không có quyền thực hiện thao tác nhập hàng vào kho.')

    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValidationError({'quantity': 'Số lượng nhập phải là một số nguyên hợp lệ.'})
    q = quantity

    if q <= 0:
        raise ValidationError({'quantity': 'Số lượng nhập phải là số nguyên dương lớn hơn 0.'})

    if q > MAX_INTEGER_LIMIT:
        raise ValidationError({'quantity': f'Số lượng nhập không được vượt quá {MAX_INTEGER_LIMIT:,}.'})

    request_key = verify_receiving_token(request_token, inventory_id=inventory_id, actor_id=actor.id)

    try:
        Inventory.objects.get(id=inventory_id)
    except Inventory.DoesNotExist:
        raise ValidationError('Bản ghi tồn kho cần nhập hàng không tồn tại trong hệ thống.')

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                existing_tx = (
                    InventoryTransaction.objects
                    .select_related('inventory', 'performed_by')
                    .filter(request_key=request_key)
                    .first()
                )
                if existing_tx:
                    if (
                        existing_tx.inventory_id == inventory_id
                        and existing_tx.performed_by_id == actor.id
                        and existing_tx.quantity_delta == q
                    ):
                        return existing_tx, True
                    else:
                        raise ValidationError(
                            'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác hoặc với thông số khác.',
                            code='idempotency_payload_conflict'
                        )

                current_inv = (
                    Inventory.objects
                    .filter(id=inventory_id)
                    .values('physical_sellable', 'reserved', 'min_threshold')
                    .first()
                )
                if not current_inv:
                    raise ValidationError('Bản ghi tồn kho không tồn tại.')

                current_physical = current_inv['physical_sellable']
                new_physical = current_physical + q

                if new_physical > MAX_INTEGER_LIMIT:
                    raise ValidationError(
                        f'Số lượng nhập ({q:,}) làm tồn thực tế vượt quá giới hạn tối đa cho phép ({MAX_INTEGER_LIMIT:,}).',
                        code='balance_overflow'
                    )

                rows_updated = (
                    Inventory.objects
                    .filter(id=inventory_id, physical_sellable=current_physical)
                    .update(
                        physical_sellable=new_physical,
                        updated_at=timezone.now(),
                    )
                )

                if rows_updated == 0:
                    raise ConcurrencyConflict('Tồn kho đã thay đổi bởi một giao dịch khác.')

                tx = InventoryTransaction.objects.create(
                    inventory_id=inventory_id,
                    transaction_type=InventoryTransaction.TYPE_PURCHASE_IN,
                    quantity_delta=q,
                    physical_before=current_physical,
                    physical_after=new_physical,
                    performed_by=actor,
                    request_key=request_key,
                )

                return tx, False

        except ConcurrencyConflict:
            if attempt == max_retries - 1:
                raise ValidationError(
                    'Hệ thống đang có giao dịch kho đồng thời khác. Vui lòng thử lại sau giây lát.'
                )
            time.sleep(0.03 * (attempt + 1) + random.uniform(0.01, 0.04))
            continue

        except OperationalError as exc:
            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError(
                        'Cơ sở dữ liệu kho đang bận xử lý giao dịch khác. Vui lòng thử lại sau.'
                    )
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise

        except IntegrityError as exc:
            existing_tx = (
                InventoryTransaction.objects
                .select_related('inventory', 'performed_by')
                .filter(request_key=request_key)
                .first()
            )
            if existing_tx:
                if (
                    existing_tx.inventory_id == inventory_id
                    and existing_tx.performed_by_id == actor.id
                    and existing_tx.quantity_delta == q
                ):
                    return existing_tx, True
                else:
                    raise ValidationError(
                        'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác.',
                        code='idempotency_payload_conflict'
                    )

            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError('Cơ sở dữ liệu kho đang bận xử lý. Vui lòng thử lại sau.')
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise


def generate_adjustment_token(inventory: Inventory, actor_id: int) -> tuple[str, uuid.UUID]:
    request_key = uuid.uuid4()
    updated_at_str = str(inventory.updated_at.isoformat()) if inventory.updated_at else ''
    payload = {
        'operation': 'ADJUSTMENT',
        'request_key': str(request_key),
        'inventory_id': inventory.id,
        'actor_id': actor_id,
        'snapshot_physical': inventory.physical_sellable,
        'snapshot_reserved': inventory.reserved,
        'snapshot_updated_at': updated_at_str,
    }
    signed_token = signing.dumps(payload, salt=SALT_ADJUSTMENT_TOKEN)
    return signed_token, request_key


def verify_adjustment_token(token: str, inventory_id: int, actor_id: int) -> dict:
    if not token or not isinstance(token, str):
        raise ValidationError('Mã xác thực yêu cầu kiểm kê kho không được để trống.', code='invalid_token')

    try:
        payload = signing.loads(token, salt=SALT_ADJUSTMENT_TOKEN, max_age=86400)
    except signing.SignatureExpired:
        raise ValidationError('Phiên làm việc kiểm kê kho đã hết hạn (quá 24 giờ). Vui lòng tải lại trang.', code='token_expired')
    except (signing.BadSignature, Exception):
        raise ValidationError('Mã xác thực yêu cầu kiểm kê không hợp lệ hoặc đã bị thay đổi.', code='invalid_token')

    if payload.get('operation') != 'ADJUSTMENT':
        raise ValidationError('Mã xác thực không thuộc nghiệp vụ kiểm kê điều chỉnh kho.', code='invalid_token_operation')

    if payload.get('inventory_id') != inventory_id:
        raise ValidationError('Mã xác thực không khớp với bản ghi tồn kho đang thao tác.', code='invalid_token_binding')

    if payload.get('actor_id') != actor_id:
        raise ValidationError('Mã xác thực không khớp với tài khoản người thực hiện.', code='invalid_token_binding')

    raw_key = payload.get('request_key')
    try:
        payload['request_key_uuid'] = uuid.UUID(raw_key)
    except (ValueError, TypeError):
        raise ValidationError('Mã định danh yêu cầu trong token không đúng định dạng UUID.', code='invalid_token')

    return payload


def adjust_inventory(
    inventory_id: int,
    counted_quantity: int,
    reason: str,
    request_token: str,
    actor,
    max_retries: int = 5,
) -> tuple[InventoryAdjustment, bool]:
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('Người thực hiện thao tác chưa được xác thực hoặc tài khoản bị vô hiệu hóa.')

    if not is_inventory_staff(actor):
        raise PermissionDenied('Bạn không có quyền thực hiện thao tác kiểm kê điều chỉnh tồn kho.')

    if isinstance(counted_quantity, bool) or not isinstance(counted_quantity, int):
        raise ValidationError({'counted_quantity': 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'})
    q = counted_quantity

    if q < 0:
        raise ValidationError({'counted_quantity': 'Số lượng kiểm kê không được là số âm (phải >= 0).'})

    if q > MAX_INTEGER_LIMIT:
        raise ValidationError({'counted_quantity': f'Số lượng kiểm kê không được vượt quá {MAX_INTEGER_LIMIT:,}.'})

    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({'reason': 'Lý do điều chỉnh không được để trống hoặc chỉ chứa khoảng trắng.'})
    canonical_reason = reason.strip()

    token_payload = verify_adjustment_token(request_token, inventory_id=inventory_id, actor_id=actor.id)
    request_key = token_payload['request_key_uuid']
    snapshot_physical = token_payload.get('snapshot_physical')
    snapshot_reserved = token_payload.get('snapshot_reserved')
    snapshot_updated_at = token_payload.get('snapshot_updated_at')

    try:
        Inventory.objects.get(id=inventory_id)
    except Inventory.DoesNotExist:
        raise ValidationError('Bản ghi tồn kho cần điều chỉnh không tồn tại trong hệ thống.')

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                existing_adj = (
                    InventoryAdjustment.objects
                    .select_related('inventory', 'adjusted_by', 'transaction')
                    .filter(transaction__request_key=request_key)
                    .first()
                )
                if existing_adj:
                    if (
                        existing_adj.inventory_id == inventory_id
                        and existing_adj.adjusted_by_id == actor.id
                        and existing_adj.new_quantity == q
                        and existing_adj.reason == canonical_reason
                        and existing_adj.transaction.transaction_type == InventoryTransaction.TYPE_ADJUSTMENT
                    ):
                        return existing_adj, True
                    else:
                        raise ValidationError(
                            'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác hoặc với thông số khác.',
                            code='idempotency_payload_conflict'
                        )

                other_tx = InventoryTransaction.objects.filter(request_key=request_key).first()
                if other_tx:
                    raise ValidationError(
                        'Khóa yêu cầu này đã được sử dụng cho một loại giao dịch khác.',
                        code='idempotency_payload_conflict'
                    )

                current_inv = (
                    Inventory.objects
                    .filter(id=inventory_id)
                    .values('physical_sellable', 'reserved', 'min_threshold', 'updated_at')
                    .first()
                )
                if not current_inv:
                    raise ValidationError('Bản ghi tồn kho không tồn tại.')

                current_physical = current_inv['physical_sellable']
                current_reserved = current_inv['reserved']
                current_updated_at = current_inv['updated_at']
                current_updated_at_str = str(current_updated_at.isoformat()) if current_updated_at else ''

                if (
                    current_physical != snapshot_physical
                    or current_reserved != snapshot_reserved
                    or current_updated_at_str != snapshot_updated_at
                ):
                    raise ValidationError(
                        'Tồn kho đã thay đổi so với thời điểm xem trước kiểm kê. Vui lòng kiểm tra lại số liệu mới nhất.',
                        code='stale_snapshot'
                    )

                if q == current_physical:
                    raise ValidationError(
                        'Số lượng kiểm kê trùng khớp với tồn thực tế hiện tại (không có chênh lệch). Không phát sinh điều chỉnh kho.',
                        code='no_difference'
                    )

                if q < current_reserved:
                    raise ValidationError(
                        f'Số lượng kiểm kê ({q:,}) không được nhỏ hơn số lượng hàng đang giữ cho đơn hàng ({current_reserved:,}).',
                        code='below_reserved'
                    )

                rows_updated = (
                    Inventory.objects
                    .filter(id=inventory_id, physical_sellable=current_physical, reserved=current_reserved)
                    .update(
                        physical_sellable=q,
                        updated_at=timezone.now(),
                    )
                )

                if rows_updated == 0:
                    raise ConcurrencyConflict('Tồn kho đã thay đổi bởi một giao dịch khác trong quá trình xử lý.')

                delta = q - current_physical
                tx = InventoryTransaction.objects.create(
                    inventory_id=inventory_id,
                    transaction_type=InventoryTransaction.TYPE_ADJUSTMENT,
                    quantity_delta=delta,
                    physical_before=current_physical,
                    physical_after=q,
                    performed_by=actor,
                    request_key=request_key,
                )

                adj = InventoryAdjustment.objects.create(
                    inventory_id=inventory_id,
                    old_quantity=current_physical,
                    new_quantity=q,
                    reason=canonical_reason,
                    adjusted_by=actor,
                    transaction=tx,
                )

                return adj, False

        except ConcurrencyConflict:
            if attempt == max_retries - 1:
                raise ValidationError(
                    'Hệ thống đang có giao dịch kho đồng thời khác. Vui lòng thử lại sau giây lát.'
                )
            time.sleep(0.03 * (attempt + 1) + random.uniform(0.01, 0.04))
            continue

        except OperationalError as exc:
            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError(
                        'Cơ sở dữ liệu kho đang bận xử lý giao dịch khác. Vui lòng thử lại sau.'
                    )
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise

        except IntegrityError as exc:
            existing_adj = (
                InventoryAdjustment.objects
                .select_related('inventory', 'adjusted_by', 'transaction')
                .filter(transaction__request_key=request_key)
                .first()
            )
            if existing_adj:
                if (
                    existing_adj.inventory_id == inventory_id
                    and existing_adj.adjusted_by_id == actor.id
                    and existing_adj.new_quantity == q
                    and existing_adj.reason == canonical_reason
                    and existing_adj.transaction.transaction_type == InventoryTransaction.TYPE_ADJUSTMENT
                ):
                    return existing_adj, True
                else:
                    raise ValidationError(
                        'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác.',
                        code='idempotency_payload_conflict'
                    )

            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError('Cơ sở dữ liệu kho đang bận xử lý. Vui lòng thử lại sau.')
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise
