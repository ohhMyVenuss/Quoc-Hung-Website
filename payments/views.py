from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import ROLE_ADMIN, ROLE_SALES_STAFF, is_admin, role_required
from orders.models import OrderStatus
from payments.forms import (
    PaymentVerificationForm, PaymentReconciliationForm,
    ResolveExpiredReconciliationForm, ResolveValidTransferReconciliationForm,
    ResolveTerminalTransferReconciliationForm, RecordExternalRefundForm
)
from payments.models import (
    Payment, PaymentMethod, PaymentStatus,
    PaymentReconciliation, ReconciliationReason, ReconciliationStatus,
    Refund, RefundStatus
)
from payments.services import (
    verify_qr_payment, open_payment_reconciliation,
    resolve_expired_reconciliation, resolve_valid_transfer_reconciliation,
    resolve_terminal_transfer_reconciliation, record_external_refund
)
from payments.sla import calculate_sla_due_at, get_store_calendar


def format_vnd(amount):
    if amount is None:
        return "0 đ"
    try:
        val = int(Decimal(str(amount)))
        return f"{val:,.0f} đ".replace(",", ".")
    except Exception:
        return f"{amount} đ"


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def payment_verification_list_view(request):
    search_q = request.GET.get('q', '').strip()
    status_tab = request.GET.get('status', 'PENDING_VERIFY').strip()

    qs = Payment.objects.filter(method=PaymentMethod.QR).select_related(
        'order', 'order__customer', 'verified_by'
    ).order_by('-customer_notified_at', '-id')

    count_pending = Payment.objects.filter(method=PaymentMethod.QR, status=PaymentStatus.PENDING_VERIFY).count()
    count_unpaid = Payment.objects.filter(method=PaymentMethod.QR, status=PaymentStatus.UNPAID).count()
    count_paid = Payment.objects.filter(method=PaymentMethod.QR, status=PaymentStatus.PAID).count()
    count_all = Payment.objects.filter(method=PaymentMethod.QR).count()
    count_reconciliation_open = PaymentReconciliation.objects.filter(status=ReconciliationStatus.OPEN).count()

    if status_tab == 'PENDING_VERIFY':
        qs = qs.filter(status=PaymentStatus.PENDING_VERIFY)
    elif status_tab == 'UNPAID':
        qs = qs.filter(status=PaymentStatus.UNPAID)
    elif status_tab == 'PAID':
        qs = qs.filter(status=PaymentStatus.PAID)

    if search_q:
        qs = qs.filter(
            Q(order__order_code__icontains=search_q) |
            Q(qr_reference__icontains=search_q) |
            Q(order__recipient_phone__icontains=search_q) |
            Q(order__recipient_name__icontains=search_q) |
            Q(order__customer__username__icontains=search_q)
        )

    paginator = Paginator(qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    for p in page_obj:
        p.expected_amount_formatted = format_vnd(p.expected_amount)
        if p.order:
            p.order.grand_total_formatted = format_vnd(p.order.grand_total)

    context = {
        'page_obj': page_obj,
        'search_q': search_q,
        'status_tab': status_tab,
        'count_pending': count_pending,
        'count_unpaid': count_unpaid,
        'count_paid': count_paid,
        'count_all': count_all,
        'count_reconciliation_open': count_reconciliation_open,
        'title': 'Xác minh thanh toán chuyển khoản QR — Quốc Hùng Mobile',
    }
    return render(request, 'payments/verification_list.html', context)


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def payment_verify_view(request, pk):
    payment = get_object_or_404(
        Payment.objects.select_related(
            'order', 'order__customer', 'verified_by'
        ).prefetch_related(
            'order__items', 'order__status_history', 'transactions'
        ),
        pk=pk
    )

    if payment.method != PaymentMethod.QR:
        messages.error(request, "Chỉ có thể xác minh thủ công cho đơn hàng thanh toán qua mã QR.")
        return redirect('payments:verification_list')

    order = payment.order
    payment.expected_amount_formatted = format_vnd(payment.expected_amount)
    if order:
        order.grand_total_formatted = format_vnd(order.grand_total)

    recipient_snapshot = payment.get_recipient_snapshot()
    existing_transactions = payment.transactions.all().order_by('-id')

    now = timezone.now()
    is_expired = bool(payment.expires_at and now >= payment.expires_at)

    if request.method == 'POST':
        form = PaymentVerificationForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            try:
                result = verify_qr_payment(
                    payment_id=payment.pk,
                    staff_user=request.user,
                    observed_recipient_account=cleaned['observed_recipient_account'],
                    observed_amount=cleaned['observed_amount'],
                    observed_transfer_reference=cleaned['observed_transfer_reference'],
                    bank_transaction_ref=cleaned['bank_transaction_ref'],
                    received_at=cleaned.get('received_at'),
                )
                if result.already_verified:
                    messages.info(
                        request,
                        f"Thanh toán của đơn hàng #{order.order_code} đã được xác nhận trước đó bởi nhân viên khác."
                    )
                else:
                    messages.success(
                        request,
                        f"Xác minh thanh toán thành công cho đơn hàng #{order.order_code}! "
                        f"Đơn hàng đã chuyển sang trạng thái 'Chờ xác nhận'."
                    )
                return redirect('payments:verify_payment', pk=pk)

            except ValidationError as ve:
                if hasattr(ve, 'message_dict'):
                    for field, errs in ve.message_dict.items():
                        if field in form.fields:
                            for e in errs:
                                form.add_error(field, e)
                        else:
                            for e in errs:
                                messages.error(request, e)
                elif hasattr(ve, 'message'):
                    messages.error(request, ve.message)
                else:
                    messages.error(request, str(ve))
    else:
        form = PaymentVerificationForm()

    active_reconciliation = payment.reconciliations.filter(
        status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]
    ).first()
    reconciliation_history = payment.reconciliations.all().order_by('-created_at')

    context = {
        'payment': payment,
        'order': order,
        'recipient_snapshot': recipient_snapshot,
        'existing_transactions': existing_transactions,
        'is_expired': is_expired,
        'form': form,
        'active_reconciliation': active_reconciliation,
        'reconciliation_history': reconciliation_history,
        'title': f"Xác minh thanh toán #{payment.pk} — Đơn {order.order_code}",
    }
    return render(request, 'payments/verify_payment.html', context)


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def reconciliation_list_view(request):
    search_q = request.GET.get('q', '').strip()
    status_tab = request.GET.get('status', 'OPEN').strip()
    reason_filter = request.GET.get('reason', '').strip()

    qs = PaymentReconciliation.objects.select_related(
        'payment', 'payment__order', 'payment__order__customer', 'handled_by', 'transaction'
    ).order_by('-created_at', '-id')

    count_open = PaymentReconciliation.objects.filter(status=ReconciliationStatus.OPEN).count()
    count_escalated = PaymentReconciliation.objects.filter(status=ReconciliationStatus.ESCALATED).count()
    count_resolved = PaymentReconciliation.objects.filter(status=ReconciliationStatus.RESOLVED).count()
    count_all = PaymentReconciliation.objects.count()

    if status_tab == 'OPEN':
        qs = qs.filter(status=ReconciliationStatus.OPEN)
    elif status_tab == 'ESCALATED':
        qs = qs.filter(status=ReconciliationStatus.ESCALATED)
    elif status_tab == 'RESOLVED':
        qs = qs.filter(status=ReconciliationStatus.RESOLVED)

    if reason_filter and reason_filter in ReconciliationReason.values:
        qs = qs.filter(reason_code=reason_filter)

    if search_q:
        qs = qs.filter(
            Q(payment__order__order_code__icontains=search_q) |
            Q(payment__qr_reference__icontains=search_q) |
            Q(payment__order__recipient_phone__icontains=search_q) |
            Q(payment__order__recipient_name__icontains=search_q) |
            Q(notes__icontains=search_q)
        )

    paginator = Paginator(qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    for r in page_obj:
        if r.payment:
            r.payment.expected_amount_formatted = format_vnd(r.payment.expected_amount)
            if r.payment.order:
                r.payment.order.grand_total_formatted = format_vnd(r.payment.order.grand_total)

    is_admin_user = is_admin(request.user)

    context = {
        'page_obj': page_obj,
        'search_q': search_q,
        'status_tab': status_tab,
        'reason_filter': reason_filter,
        'reasons': ReconciliationReason.choices,
        'count_open': count_open,
        'count_escalated': count_escalated,
        'count_resolved': count_resolved,
        'count_all': count_all,
        'is_admin_user': is_admin_user,
        'title': 'Danh sách hồ sơ đối soát thanh toán QR — Quốc Hùng Mobile',
    }
    return render(request, 'payments/reconciliation_list.html', context)


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def open_reconciliation_view(request, pk):
    payment = get_object_or_404(
        Payment.objects.select_related('order', 'order__customer').prefetch_related('transactions'),
        pk=pk
    )

    if payment.method != PaymentMethod.QR:
        messages.error(request, "Chỉ có thể mở hồ sơ đối soát cho đơn hàng thanh toán qua mã QR.")
        return redirect('payments:verification_list')

    order = payment.order
    payment.expected_amount_formatted = format_vnd(payment.expected_amount)
    if order:
        order.grand_total_formatted = format_vnd(order.grand_total)

    active_case = payment.reconciliations.filter(
        status__in=[ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]
    ).first()

    has_calendar = get_store_calendar() is not None
    sla_preview = calculate_sla_due_at(timezone.now()) if has_calendar else None

    if request.method == 'POST':
        form = PaymentReconciliationForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            try:
                result = open_payment_reconciliation(
                    payment_id=payment.pk,
                    staff_user=request.user,
                    reason_code=cleaned['reason_code'],
                    notes=cleaned.get('notes'),
                )
                if result.already_open:
                    messages.info(
                        request,
                        f"Đơn hàng #{order.order_code} đã có hồ sơ đối soát chưa giải quyết (Hồ sơ #{result.reconciliation.pk})."
                    )
                else:
                    messages.success(
                        request,
                        f"Đã mở hồ sơ đối soát #{result.reconciliation.pk} thành công cho đơn hàng #{order.order_code}!"
                    )
                return redirect('payments:reconciliation_detail', pk=result.reconciliation.pk)

            except ValidationError as ve:
                if hasattr(ve, 'message_dict'):
                    for field, errs in ve.message_dict.items():
                        if field in form.fields:
                            for e in errs:
                                form.add_error(field, e)
                        else:
                            for e in errs:
                                messages.error(request, e)
                elif hasattr(ve, 'message'):
                    messages.error(request, ve.message)
                else:
                    messages.error(request, str(ve))
    else:
        form = PaymentReconciliationForm()

    context = {
        'payment': payment,
        'order': order,
        'active_case': active_case,
        'has_calendar': has_calendar,
        'sla_preview': sla_preview,
        'form': form,
        'title': f"Mở hồ sơ đối soát #{payment.pk} — Đơn {order.order_code}",
    }
    return render(request, 'payments/open_reconciliation.html', context)


def _build_reconciliation_detail_context(request, case, resolve_form=None, valid_transfer_form=None, terminal_transfer_form=None, record_refund_form=None):
    payment = case.payment
    order = payment.order if payment else None
    if payment:
        payment.expected_amount_formatted = format_vnd(payment.expected_amount)
    if order:
        order.grand_total_formatted = format_vnd(order.grand_total)

    order_history = order.status_history.all().order_by('-changed_at', '-id') if order else []

    now = timezone.now()
    is_admin_user = is_admin(request.user)
    is_unresolved = case.status in [ReconciliationStatus.OPEN, ReconciliationStatus.ESCALATED]
    is_expired = bool(payment and payment.expires_at and now >= payment.expires_at)
    is_eligible_order_expired = bool(order and order.order_status in [OrderStatus.PENDING_PAYMENT, OrderStatus.PENDING_RECONCILIATION])
    is_eligible_payment = bool(payment and payment.status in [PaymentStatus.UNPAID, PaymentStatus.PENDING_VERIFY])
    has_no_transaction = bool(payment and not case.transaction_id and not payment.transactions.exists())
    is_eligible_reason = (case.reason_code == ReconciliationReason.UNCLEAR_OR_EXPIRED)

    can_resolve_expired = (
        is_admin_user and
        is_unresolved and
        is_expired and
        is_eligible_order_expired and
        is_eligible_payment and
        has_no_transaction and
        is_eligible_reason
    )

    if resolve_form is None and can_resolve_expired:
        resolve_form = ResolveExpiredReconciliationForm()

    is_order_pending_reconciliation = bool(order and order.order_status == OrderStatus.PENDING_RECONCILIATION)
    can_resolve_valid_transfer = (
        is_admin_user and
        is_unresolved and
        is_order_pending_reconciliation and
        is_eligible_payment and
        is_eligible_reason
    )

    if valid_transfer_form is None and can_resolve_valid_transfer:
        initial_valid = {}
        if payment:
            initial_valid['observed_recipient_account'] = payment.recipient_account_number or ''
            initial_valid['observed_amount'] = payment.expected_amount
            initial_valid['observed_transfer_reference'] = payment.qr_reference or ''
        valid_transfer_form = ResolveValidTransferReconciliationForm(initial=initial_valid)

    is_terminal_order = bool(order and order.order_status in [OrderStatus.EXPIRED, OrderStatus.CANCELLED])
    is_eligible_terminal_payment = bool(payment and payment.status in [
        PaymentStatus.UNPAID, PaymentStatus.PENDING_VERIFY, PaymentStatus.EXPIRED, PaymentStatus.FAILED
    ])
    is_eligible_terminal_pair = bool(
        (order and order.order_status == OrderStatus.EXPIRED and case.reason_code == ReconciliationReason.UNCLEAR_OR_EXPIRED) or
        (order and order.order_status == OrderStatus.CANCELLED and case.reason_code == ReconciliationReason.TRANSFER_AFTER_CANCELLATION)
    )
    can_resolve_terminal_transfer = (
        is_admin_user and
        is_unresolved and
        is_terminal_order and
        is_eligible_terminal_pair and
        is_eligible_terminal_payment
    )

    if terminal_transfer_form is None and can_resolve_terminal_transfer:
        initial_terminal = {}
        if payment:
            initial_terminal['observed_recipient_account'] = payment.recipient_account_number or ''
            initial_terminal['observed_amount'] = payment.expected_amount
            initial_terminal['observed_transfer_reference'] = payment.qr_reference or ''
        terminal_transfer_form = ResolveTerminalTransferReconciliationForm(initial=initial_terminal)

    is_terminal_qr_resolved = bool(
        case.status == ReconciliationStatus.RESOLVED and
        is_terminal_order and
        is_eligible_terminal_pair and
        payment and payment.method == PaymentMethod.QR and
        case.transaction_id and case.transaction and case.transaction.verification_status == 'VERIFIED'
    )
    can_record_refund = (
        is_admin_user and
        is_terminal_qr_resolved and
        payment and payment.status == PaymentStatus.PENDING_REFUND
    )

    if record_refund_form is None and can_record_refund:
        initial_refund = {}
        if case.transaction:
            initial_refund['refund_amount'] = case.transaction.received_amount
        record_refund_form = RecordExternalRefundForm(initial=initial_refund)

    completed_refund = None
    if order and payment and payment.status == PaymentStatus.REFUNDED:
        completed_refund = order.refunds.filter(status=RefundStatus.COMPLETED).order_by('-completed_at', '-id').first()

    return {
        'case': case,
        'payment': payment,
        'order': order,
        'order_history': order_history,
        'recipient_snapshot': payment.get_recipient_snapshot() if payment else None,
        'title': f"Hồ sơ đối soát #{case.pk} — Đơn {order.order_code if order else 'N/A'}",
        'is_admin_user': is_admin_user,
        'is_unresolved': is_unresolved,
        'is_expired': is_expired,
        'can_resolve_expired': can_resolve_expired,
        'resolve_form': resolve_form,
        'can_resolve_valid_transfer': can_resolve_valid_transfer,
        'valid_transfer_form': valid_transfer_form,
        'can_resolve_terminal_transfer': can_resolve_terminal_transfer,
        'terminal_transfer_form': terminal_transfer_form,
        'can_record_refund': can_record_refund,
        'record_refund_form': record_refund_form,
        'completed_refund': completed_refund,
    }


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def reconciliation_detail_view(request, pk):
    case = get_object_or_404(
        PaymentReconciliation.objects.select_related(
            'payment', 'payment__order', 'payment__order__customer', 'handled_by', 'transaction'
        ).prefetch_related(
            'payment__order__status_history', 'payment__transactions'
        ),
        pk=pk
    )
    context = _build_reconciliation_detail_context(request, case)
    return render(request, 'payments/reconciliation_detail.html', context)


@role_required(ROLE_ADMIN)
def resolve_expired_reconciliation_view(request, pk):
    case = get_object_or_404(
        PaymentReconciliation.objects.select_related(
            'payment', 'payment__order', 'payment__order__customer', 'handled_by', 'transaction'
        ).prefetch_related(
            'payment__order__status_history', 'payment__transactions'
        ),
        pk=pk
    )

    if request.method != 'POST':
        messages.error(request, "Thao tác chỉ hỗ trợ phương thức POST an toàn.")
        return redirect('payments:reconciliation_detail', pk=case.pk)

    form = ResolveExpiredReconciliationForm(request.POST)
    if form.is_valid():
        try:
            result = resolve_expired_reconciliation(
                reconciliation_id=case.pk,
                admin_user=request.user,
                audit_note=form.cleaned_data['audit_note'],
            )
            if result.already_resolved:
                messages.info(
                    request,
                    f"Hồ sơ đối soát #{case.pk} đã được giải quyết trước đó."
                )
            else:
                voucher_msg = " và 1 mã giảm giá" if result.released_coupon else ""
                messages.success(
                    request,
                    f"Đã đóng hồ sơ đối soát #{case.pk} thành công (xác nhận không có chuyển khoản). "
                    f"Đơn hàng #{result.order.order_code} đã chuyển sang Hết hạn thanh toán và giải phóng {result.released_reservations_count} dòng giữ kho{voucher_msg}."
                )
            return redirect('payments:reconciliation_detail', pk=case.pk)

        except ValidationError as ve:
            if hasattr(ve, 'message_dict'):
                for field, errs in ve.message_dict.items():
                    if field in form.fields:
                        for e in errs:
                            form.add_error(field, e)
                    else:
                        for e in errs:
                            messages.error(request, e)
            elif hasattr(ve, 'message'):
                messages.error(request, ve.message)
            else:
                messages.error(request, str(ve))
        except PermissionDenied as pd:
            raise pd

    context = _build_reconciliation_detail_context(request, case, resolve_form=form)
    return render(request, 'payments/reconciliation_detail.html', context)


@role_required(ROLE_ADMIN)
def resolve_valid_transfer_reconciliation_view(request, pk):
    case = get_object_or_404(
        PaymentReconciliation.objects.select_related(
            'payment', 'payment__order', 'payment__order__customer', 'handled_by', 'transaction'
        ).prefetch_related(
            'payment__order__status_history', 'payment__transactions'
        ),
        pk=pk
    )

    if request.method != 'POST':
        messages.error(request, "Thao tác chỉ hỗ trợ phương thức POST an toàn.")
        return redirect('payments:reconciliation_detail', pk=case.pk)

    form = ResolveValidTransferReconciliationForm(request.POST)
    if form.is_valid():
        try:
            cleaned = form.cleaned_data
            result = resolve_valid_transfer_reconciliation(
                reconciliation_id=case.pk,
                admin_user=request.user,
                observed_recipient_account=cleaned['observed_recipient_account'],
                observed_amount=cleaned['observed_amount'],
                observed_transfer_reference=cleaned['observed_transfer_reference'],
                bank_transaction_ref=cleaned['bank_transaction_ref'],
                received_at=cleaned['received_at'],
                audit_note=cleaned['audit_note'],
            )
            if result.already_resolved:
                messages.info(
                    request,
                    f"Hồ sơ đối soát #{case.pk} đã được xác nhận thanh toán hợp lệ trước đó (kết quả bảo toàn)."
                )
            else:
                voucher_msg = " và quyết toán ưu đãi mã giảm giá" if result.finalized_coupon else ""
                messages.success(
                    request,
                    f"Đã giải quyết đóng hồ sơ đối soát #{case.pk} thành công: Xác nhận chuyển khoản hợp lệ "
                    f"(Mã GD: {result.transaction.bank_transaction_ref}), đơn hàng #{result.order.order_code} "
                    f"chuyển sang Chờ xác nhận{voucher_msg}."
                )
            return redirect('payments:reconciliation_detail', pk=case.pk)

        except ValidationError as ve:
            if hasattr(ve, 'message_dict'):
                for field, errs in ve.message_dict.items():
                    if field in form.fields:
                        for e in errs:
                            form.add_error(field, e)
                    else:
                        for e in errs:
                            messages.error(request, e)
            elif hasattr(ve, 'message'):
                messages.error(request, ve.message)
            else:
                messages.error(request, str(ve))
        except PermissionDenied as pd:
            raise pd

    context = _build_reconciliation_detail_context(request, case, valid_transfer_form=form)
    return render(request, 'payments/reconciliation_detail.html', context)


@role_required(ROLE_ADMIN)
def resolve_terminal_transfer_reconciliation_view(request, pk):
    case = get_object_or_404(
        PaymentReconciliation.objects.select_related(
            'payment', 'payment__order', 'payment__order__customer', 'handled_by', 'transaction'
        ).prefetch_related(
            'payment__order__status_history', 'payment__transactions'
        ),
        pk=pk
    )

    if request.method != 'POST':
        messages.error(request, "Thao tác chỉ hỗ trợ phương thức POST an toàn.")
        return redirect('payments:reconciliation_detail', pk=case.pk)

    form = ResolveTerminalTransferReconciliationForm(request.POST)
    if form.is_valid():
        try:
            cleaned = form.cleaned_data
            result = resolve_terminal_transfer_reconciliation(
                reconciliation_id=case.pk,
                admin_user=request.user,
                observed_recipient_account=cleaned['observed_recipient_account'],
                observed_amount=cleaned['observed_amount'],
                observed_transfer_reference=cleaned['observed_transfer_reference'],
                bank_transaction_ref=cleaned['bank_transaction_ref'],
                received_at=cleaned['received_at'],
                audit_note=cleaned['audit_note'],
            )
            if result.already_resolved:
                messages.info(
                    request,
                    f"Hồ sơ đối soát #{case.pk} đã được xác nhận chuyển khoản cho đơn kết thúc trước đó (kết quả bảo toàn)."
                )
            else:
                messages.success(
                    request,
                    f"Đã giải quyết hồ sơ đối soát #{case.pk} thành công: Xác nhận chuyển khoản "
                    f"(Mã GD: {result.transaction.bank_transaction_ref}), thanh toán đơn #{result.order.order_code} "
                    f"chuyển sang Chờ hoàn tiền (PENDING_REFUND). Đơn hàng giữ nguyên trạng thái kết thúc theo BR-PAY-08."
                )
            return redirect('payments:reconciliation_detail', pk=case.pk)

        except ValidationError as ve:
            if hasattr(ve, 'message_dict'):
                for field, errs in ve.message_dict.items():
                    if field in form.fields:
                        for e in errs:
                            form.add_error(field, e)
                    else:
                        for e in errs:
                            messages.error(request, e)
            elif hasattr(ve, 'message'):
                messages.error(request, ve.message)
            else:
                messages.error(request, str(ve))
        except PermissionDenied as pd:
            raise pd

    context = _build_reconciliation_detail_context(request, case, terminal_transfer_form=form)
    return render(request, 'payments/reconciliation_detail.html', context)


@role_required(ROLE_ADMIN)
def record_external_refund_view(request, pk):
    case = get_object_or_404(
        PaymentReconciliation.objects.select_related(
            'payment', 'payment__order', 'payment__order__customer', 'handled_by', 'transaction'
        ).prefetch_related(
            'payment__order__status_history', 'payment__transactions'
        ),
        pk=pk
    )

    if request.method != 'POST':
        messages.error(request, "Thao tác chỉ hỗ trợ phương thức POST an toàn.")
        return redirect('payments:reconciliation_detail', pk=case.pk)

    form = RecordExternalRefundForm(request.POST)
    if form.is_valid():
        try:
            cleaned = form.cleaned_data
            result = record_external_refund(
                reconciliation_id=case.pk,
                admin_user=request.user,
                bank_reference=cleaned['bank_reference'],
                refund_amount=cleaned['refund_amount'],
                completed_at=cleaned['completed_at'],
                audit_note=cleaned['audit_note'],
            )
            if result.already_confirmed:
                messages.info(
                    request,
                    f"Giao dịch hoàn tiền cho đơn hàng #{result.order.order_code} đã được xác nhận trước đó (kết quả bảo toàn)."
                )
            else:
                messages.success(
                    request,
                    f"Đã xác nhận hoàn tiền thành công: Đơn hàng #{result.order.order_code} - "
                    f"Số tiền hoàn: {result.refund.amount:,.0f} đ - Mã GD: {result.refund.bank_reference}. "
                    f"Thanh toán đã chuyển sang Đã hoàn tiền (REFUNDED)."
                )
            return redirect('payments:reconciliation_detail', pk=case.pk)

        except ValidationError as ve:
            if hasattr(ve, 'message_dict'):
                for field, errs in ve.message_dict.items():
                    if field in form.fields:
                        for e in errs:
                            form.add_error(field, e)
                    else:
                        for e in errs:
                            messages.error(request, e)
            elif hasattr(ve, 'message'):
                messages.error(request, ve.message)
            else:
                messages.error(request, str(ve))
        except PermissionDenied as pd:
            raise pd

    context = _build_reconciliation_detail_context(request, case, record_refund_form=form)
    return render(request, 'payments/reconciliation_detail.html', context)


