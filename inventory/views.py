import uuid
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from accounts.permissions import is_inventory_staff, is_admin, is_sales_staff
from inventory.models import Inventory, InventoryTransaction, InventoryAdjustment
from inventory.forms import InventoryThresholdForm, PurchaseReceivingForm, StocktakeAdjustmentForm
from inventory.services import generate_receiving_token, receive_purchase, generate_adjustment_token, adjust_inventory


def parse_positive_id(val, max_digits=12):
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val or len(val) > max_digits:
        return None
    if not (val.isascii() and val.isdigit()):
        return None
    try:
        parsed = int(val)
        return parsed if parsed > 0 else None
    except ValueError:
        return None

def inventory_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    q = request.GET.get('q', '').strip()
    alert_filter = request.GET.get('alert', '').strip().lower()

    base_qs = (
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        )
        .prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        )
        .annotate(available_count=F('physical_sellable') - F('reserved'))
    )

    total_variants_count = base_qs.count()
    total_low_stock_count = base_qs.filter(available_count__lte=F('min_threshold')).count()
    total_out_of_stock_count = base_qs.filter(available_count__lte=0).count()

    queryset = base_qs

    if q:
        search_filter = (
            Q(variant__sku__icontains=q)
            | Q(variant__product__name__icontains=q)
        )
        clean_code = q.upper()
        if clean_code.startswith('SP'):
            pid = parse_positive_id(clean_code[2:])
            if pid is not None:
                search_filter |= Q(variant__product__id=pid)
        else:
            pid = parse_positive_id(q)
            if pid is not None:
                search_filter |= Q(variant__product__id=pid)

        queryset = queryset.filter(search_filter)

    if alert_filter == 'low_stock':
        queryset = queryset.filter(available_count__lte=F('min_threshold'))
    elif alert_filter == 'out_of_stock':
        queryset = queryset.filter(available_count__lte=0)
    elif alert_filter == 'in_stock':
        queryset = queryset.filter(available_count__gt=F('min_threshold'))
    else:
        alert_filter = 'all'

    queryset = queryset.order_by('variant__product__name', 'variant__id')

    paginator = Paginator(queryset, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'inventory_list': page_obj.object_list,
        'q': q,
        'alert_filter': alert_filter,
        'total_variants_count': total_variants_count,
        'total_low_stock_count': total_low_stock_count,
        'total_out_of_stock_count': total_out_of_stock_count,
        'filtered_count': queryset.count(),
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }
    return render(request, 'inventory/inventory_list.html', context)


def _build_inventory_detail_context(request, inventory, form=None):
    if form is None:
        form = InventoryThresholdForm(instance=inventory)

    transactions_qs = (
        inventory.transactions
        .select_related('performed_by', 'adjustment')
        .order_by('-created_at', '-id')
    )
    total_tx_count = transactions_qs.count()
    tx_paginator = Paginator(transactions_qs, 10)
    tx_page_number = request.GET.get('tx_page') or request.POST.get('tx_page', 1)
    tx_page_obj = tx_paginator.get_page(tx_page_number)

    return {
        'inventory': inventory,
        'variant': inventory.variant,
        'product': inventory.variant.product if inventory.variant else None,
        'form': form,
        'tx_page_obj': tx_page_obj,
        'total_tx_count': total_tx_count,
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }


def inventory_detail_view(request, inventory_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    context = _build_inventory_detail_context(request, inventory)
    return render(request, 'inventory/inventory_detail.html', context)


def inventory_update_threshold_view(request, inventory_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    old_threshold = inventory.min_threshold
    form = InventoryThresholdForm(request.POST, instance=inventory)
    if form.is_valid():
        updated_inv = form.save()
        messages.success(
            request,
            f"Đã cập nhật ngưỡng cảnh báo cho SKU '{inventory.variant.sku}' "
            f"từ {old_threshold} thành {updated_inv.min_threshold} thành công.",
        )
        return redirect('inventory:inventory_detail', inventory_id=inventory.id)

    context = _build_inventory_detail_context(request, inventory, form=form)
    return render(request, 'inventory/inventory_detail.html', context, status=200)


def _build_inventory_form_context(inventory, form, request_token, user):
    return {
        'inventory': inventory,
        'variant': inventory.variant,
        'product': inventory.variant.product if inventory.variant else None,
        'form': form,
        'request_token': request_token,
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(user),
        'is_sales_staff': is_sales_staff(user),
        'is_inventory_staff': is_inventory_staff(user),
    }


def _add_validation_error_to_form(form, exc):
    if hasattr(exc, 'message_dict'):
        for field, errs in exc.message_dict.items():
            for err in errs:
                if field in form.fields:
                    form.add_error(field, err)
                else:
                    form.add_error(None, err)
    else:
        for err in exc.messages:
            form.add_error(None, err)


def inventory_receive_view(request, inventory_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    if request.method == 'GET':
        token, _ = generate_receiving_token(inventory_id=inventory.id, actor_id=request.user.id)
        form = PurchaseReceivingForm(initial={'request_token': token})
        context = _build_inventory_form_context(inventory, form, token, request.user)
        return render(request, 'inventory/inventory_receive.html', context)

    elif request.method == 'POST':
        form = PurchaseReceivingForm(request.POST)
        if form.is_valid():
            quantity = form.cleaned_data['quantity']
            token = form.cleaned_data['request_token']
            try:
                tx, is_replay = receive_purchase(
                    inventory_id=inventory.id,
                    quantity=quantity,
                    request_token=token,
                    actor=request.user,
                )
                if is_replay:
                    messages.info(
                        request,
                        f"Yêu cầu nhập hàng trước đó cho SKU '{inventory.variant.sku}' đã được ghi nhận "
                        f"(Số lượng: {quantity}, Tồn thực tế: {tx.physical_after}, Mã yêu cầu: {tx.request_key})."
                    )
                else:
                    messages.success(
                        request,
                        f"Đã nhập thành công {quantity} sản phẩm cho biến thể '{inventory.variant.sku}'. "
                        f"Tồn thực tế mới: {tx.physical_after} (Khả dụng: {tx.physical_after - inventory.reserved})."
                    )
                return redirect('inventory:inventory_detail', inventory_id=inventory.id)
            except ValidationError as exc:
                _add_validation_error_to_form(form, exc)
            except PermissionDenied as exc:
                return HttpResponseForbidden(str(exc))

        context = _build_inventory_form_context(
            inventory, form, form.data.get('request_token', ''), request.user
        )
        return render(request, 'inventory/inventory_receive.html', context, status=200)

    else:
        return HttpResponseNotAllowed(['GET', 'POST'])


def inventory_transaction_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    q = request.GET.get('q', '').strip()
    tx_type = request.GET.get('type', '').strip().upper()

    base_qs = (
        InventoryTransaction.objects
        .select_related(
            'inventory',
            'inventory__variant',
            'inventory__variant__product',
            'performed_by',
            'adjustment',
        )
        .order_by('-created_at', '-id')
    )

    total_tx_count = base_qs.count()
    queryset = base_qs

    if q:
        search_filter = (
            Q(inventory__variant__sku__icontains=q)
            | Q(inventory__variant__product__name__icontains=q)
            | Q(performed_by__username__icontains=q)
            | Q(adjustment__reason__icontains=q)
        )
        clean_code = q.upper()
        if clean_code.startswith('SP'):
            pid = parse_positive_id(clean_code[2:])
            if pid is not None:
                search_filter |= Q(inventory__variant__product__id=pid)
        else:
            pid = parse_positive_id(q)
            if pid is not None:
                search_filter |= Q(inventory__variant__product__id=pid)

        try:
            val_uuid = uuid.UUID(q)
            search_filter |= Q(request_key=val_uuid)
        except (ValueError, TypeError):
            pass

        queryset = queryset.filter(search_filter)

    if tx_type in [InventoryTransaction.TYPE_PURCHASE_IN, InventoryTransaction.TYPE_ADJUSTMENT]:
        queryset = queryset.filter(transaction_type=tx_type)
    else:
        tx_type = 'ALL'

    paginator = Paginator(queryset, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'transaction_list': page_obj.object_list,
        'q': q,
        'tx_type': tx_type,
        'total_tx_count': total_tx_count,
        'filtered_count': queryset.count(),
        'active_nav': 'inventory_history',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }
    return render(request, 'inventory/inventory_history.html', context)


def inventory_adjust_view(request, inventory_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    if request.method == 'GET':
        token, _ = generate_adjustment_token(inventory=inventory, actor_id=request.user.id)
        form = StocktakeAdjustmentForm(initial={'request_token': token})
        context = _build_inventory_form_context(inventory, form, token, request.user)
        return render(request, 'inventory/inventory_adjust.html', context)

    elif request.method == 'POST':
        form = StocktakeAdjustmentForm(request.POST)
        if form.is_valid():
            counted_quantity = form.cleaned_data['counted_quantity']
            reason = form.cleaned_data['reason']
            token = form.cleaned_data['request_token']
            try:
                adj, is_replay = adjust_inventory(
                    inventory_id=inventory.id,
                    counted_quantity=counted_quantity,
                    reason=reason,
                    request_token=token,
                    actor=request.user,
                )
                if is_replay:
                    messages.info(
                        request,
                        f"Yêu cầu điều chỉnh kiểm kê trước đó cho SKU '{inventory.variant.sku}' đã được ghi nhận "
                        f"(Tồn mới: {adj.new_quantity}, Tồn cũ: {adj.old_quantity}, Chênh lệch: {adj.quantity_delta:+d}, Mã giao dịch: {adj.transaction.request_key})."
                    )
                else:
                    messages.success(
                        request,
                        f"Đã điều chỉnh tồn kho cho biến thể '{inventory.variant.sku}': từ {adj.old_quantity} thành {adj.new_quantity} "
                        f"(Chênh lệch: {adj.quantity_delta:+d}, Khả dụng mới: {adj.new_quantity - inventory.reserved}). Lý do: {adj.reason}."
                    )
                return redirect('inventory:inventory_detail', inventory_id=inventory.id)
            except ValidationError as exc:
                exc_code = getattr(exc, 'code', None)
                if exc_code == 'stale_snapshot':
                    inventory.refresh_from_db()
                    fresh_token, _ = generate_adjustment_token(inventory=inventory, actor_id=request.user.id)
                    form.data = form.data.copy()
                    form.data['request_token'] = fresh_token

                _add_validation_error_to_form(form, exc)
            except PermissionDenied as exc:
                return HttpResponseForbidden(str(exc))

        inventory.refresh_from_db()
        context = _build_inventory_form_context(
            inventory, form, form.data.get('request_token', ''), request.user
        )
        return render(request, 'inventory/inventory_adjust.html', context, status=200)

    else:
        return HttpResponseNotAllowed(['GET', 'POST'])
