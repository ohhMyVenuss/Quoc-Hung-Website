from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from accounts.permissions import is_customer
from catalog.models import Category
from catalog.utils import format_vnd
from coupons.evaluation import evaluate_coupon_for_cart
from cart.forms import AddToCartForm, UpdateCartItemQuantityForm, SwitchCartItemVariantForm, CouponPreviewForm
from cart.services import (
    resolve_cart,
    add_to_cart,
    update_cart_item_quantity,
    switch_cart_item_variant,
    remove_cart_item,
    get_cart_evaluation,
)


def _check_cart_authorization(request):

    from django.contrib.auth import get_user_model, SESSION_KEY
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        if not user.is_active:
            return False, "Tài khoản của bạn hiện đang bị vô hiệu hóa."
        if not is_customer(user):
            return False, "Tài khoản nhân viên hoặc quản trị viên không có quyền thao tác giỏ hàng cá nhân. Vui lòng đăng nhập bằng tài khoản Khách hàng."
        return True, None

    if hasattr(request, 'session') and request.session.get(SESSION_KEY):
        UserModel = get_user_model()
        user_obj = UserModel.objects.filter(pk=request.session.get(SESSION_KEY)).first()
        if user_obj and not user_obj.is_active:
            return False, "Tài khoản của bạn hiện đang bị vô hiệu hóa."

    return True, None


def cart_detail_view(request):

    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'], "Phương thức yêu cầu không hợp lệ.")

    is_allowed, error_msg = _check_cart_authorization(request)
    if not is_allowed:
        return HttpResponseForbidden(error_msg)

    has_pending_guest_cart = False
    if request.user.is_authenticated and hasattr(request, 'session') and request.session.get('_pending_guest_cart_session_key'):
        has_pending_guest_cart = True

    try:
        cart = resolve_cart(request, create_if_empty=False)
    except ValidationError as e:
        messages.error(request, e.message)
        cart = None

    evaluation = get_cart_evaluation(cart)
    categories = Category.objects.filter(is_visible=True).order_by('name')

    context = {
        'cart': cart,
        'evaluation': evaluation,
        'categories': categories,
        'active_nav': 'cart',
        'title': 'Giỏ hàng — Quốc Hùng Mobile',
        'has_pending_guest_cart': has_pending_guest_cart,
    }
    return render(request, 'cart/cart_detail.html', context)


from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from django.utils.http import url_has_allowed_host_and_scheme
from catalog.models import ProductVariant


def _get_safe_redirect_target(request, variant_id=None, extra_params=None):

    referer = request.META.get('HTTP_REFERER', '').strip()
    safe_url = None

    if referer:
        allowed_hosts = {request.get_host()}
        is_safe = url_has_allowed_host_and_scheme(
            url=referer,
            allowed_hosts=allowed_hosts,
            require_https=request.is_secure()
        )
        if is_safe:
            parsed = urlparse(referer)
            if not parsed.netloc or parsed.netloc == request.get_host():
                safe_url = referer

    if not safe_url:
        if variant_id:
            try:
                var_obj = ProductVariant.objects.filter(pk=variant_id).select_related('product').first()
                if var_obj and var_obj.product_id:
                    safe_url = reverse('catalog:public_product_detail', kwargs={'product_id': var_obj.product_id})
            except Exception:
                safe_url = None
        if not safe_url:
            safe_url = reverse('cart:cart_detail')

    if extra_params:
        parsed = urlparse(safe_url)
        query_dict = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for k, v in extra_params.items():
            if v is not None and str(v).strip():
                query_dict[k] = str(v).strip()
        new_query = urlencode(query_dict)
        safe_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    return safe_url


def cart_add_view(request):

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác thêm vào giỏ hàng yêu cầu phương thức POST có CSRF token.")

    is_allowed, error_msg = _check_cart_authorization(request)
    if not is_allowed:
        return HttpResponseForbidden(error_msg)

    form = AddToCartForm(request.POST)
    if not form.is_valid():
        for field, err_list in form.errors.items():
            for err in err_list:
                messages.error(request, f"Dữ liệu không hợp lệ: {err}")
        v_id = request.POST.get('variant_id')
        qty = request.POST.get('quantity')
        extra_params = {}
        if v_id:
            extra_params['variant'] = v_id
        if qty:
            extra_params['quantity'] = qty
        target_url = _get_safe_redirect_target(request, variant_id=v_id, extra_params=extra_params)
        return redirect(target_url)

    variant_id = form.cleaned_data['variant_id']
    quantity = form.cleaned_data['quantity']

    if request.user.is_authenticated and hasattr(request, 'session') and request.session.get('_pending_guest_cart_session_key'):
        pending_key = request.session.get('_pending_guest_cart_session_key')
        from cart.services import merge_guest_cart_to_user
        try:
            merge_guest_cart_to_user(pending_key, request.user)
            request.session.pop('_pending_guest_cart_session_key', None)
        except ValidationError:
            pass

    try:
        cart = resolve_cart(request, create_if_empty=True)
        if not cart:
            messages.error(request, "Không thể xác thực phiên giỏ hàng của bạn.")
            return redirect('cart:cart_detail')
        if not request.user.is_authenticated and hasattr(request, 'session'):
            request.session['has_cart'] = True
        item, created = add_to_cart(cart, variant_id, quantity)
        messages.success(request, f"Đã thêm sản phẩm vào giỏ hàng thành công.")
    except ValidationError as e:
        messages.error(request, e.message)
        extra_params = {'variant': variant_id, 'quantity': quantity}
        target_url = _get_safe_redirect_target(request, variant_id=variant_id, extra_params=extra_params)
        return redirect(target_url)

    return redirect('cart:cart_detail')


def cart_update_quantity_view(request, item_id):

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác cập nhật yêu cầu phương thức POST có CSRF token.")

    is_allowed, error_msg = _check_cart_authorization(request)
    if not is_allowed:
        return HttpResponseForbidden(error_msg)

    try:
        cart = resolve_cart(request, create_if_empty=False)
    except ValidationError as e:
        messages.error(request, e.message)
        return redirect('cart:cart_detail')

    if not cart:
        raise Http404("Không tìm thấy dòng sản phẩm trong giỏ hàng của bạn.")

    form = UpdateCartItemQuantityForm(request.POST)
    if not form.is_valid():
        for field, err_list in form.errors.items():
            for err in err_list:
                messages.error(request, f"Lỗi số lượng: {err}")
        return redirect('cart:cart_detail')

    quantity = form.cleaned_data['quantity']

    try:
        update_cart_item_quantity(cart, item_id, quantity)
        messages.success(request, "Đã cập nhật số lượng sản phẩm thành công.")
    except ValidationError as e:
        messages.error(request, e.message)

    return redirect('cart:cart_detail')


def cart_switch_variant_view(request, item_id):

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác đổi phiên bản yêu cầu phương thức POST có CSRF token.")

    is_allowed, error_msg = _check_cart_authorization(request)
    if not is_allowed:
        return HttpResponseForbidden(error_msg)

    try:
        cart = resolve_cart(request, create_if_empty=False)
    except ValidationError as e:
        messages.error(request, e.message)
        return redirect('cart:cart_detail')

    if not cart:
        raise Http404("Không tìm thấy dòng sản phẩm trong giỏ hàng của bạn.")

    form = SwitchCartItemVariantForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Biến thể được chọn không hợp lệ.")
        return redirect('cart:cart_detail')

    new_variant_id = form.cleaned_data['new_variant_id']

    try:
        switch_cart_item_variant(cart, item_id, new_variant_id)
        messages.success(request, "Đã chuyển đổi phiên bản sản phẩm thành công.")
    except ValidationError as e:
        messages.error(request, e.message)

    return redirect('cart:cart_detail')


def cart_remove_item_view(request, item_id):

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác xóa dòng yêu cầu phương thức POST có CSRF token.")

    is_allowed, error_msg = _check_cart_authorization(request)
    if not is_allowed:
        return HttpResponseForbidden(error_msg)

    try:
        cart = resolve_cart(request, create_if_empty=False)
    except ValidationError as e:
        messages.error(request, e.message)
        return redirect('cart:cart_detail')

    if not cart:
        raise Http404("Không tìm thấy dòng sản phẩm trong giỏ hàng của bạn.")

    try:
        remove_cart_item(cart, item_id)
        messages.success(request, "Đã xóa sản phẩm khỏi giỏ hàng.")
    except ValidationError as e:
        messages.error(request, e.message)

    return redirect('cart:cart_detail')


def cart_sync_guest_cart_view(request):

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác đồng bộ giỏ hàng yêu cầu phương thức POST có CSRF token.")

    is_allowed, error_msg = _check_cart_authorization(request)
    if not is_allowed:
        return HttpResponseForbidden(error_msg)

    if not request.user.is_authenticated:
        return HttpResponseForbidden("Thao tác đồng bộ giỏ hàng yêu cầu tài khoản đã đăng nhập.")

    pending_key = request.session.get('_pending_guest_cart_session_key')
    if not pending_key:
        messages.info(request, "Không có giỏ hàng trước đăng nhập cần đồng bộ.")
        return redirect('cart:cart_detail')

    from cart.services import merge_guest_cart_to_user
    try:
        merge_guest_cart_to_user(pending_key, request.user)
        request.session.pop('_pending_guest_cart_session_key', None)
        request.session.save()
        messages.success(request, "Đã đồng bộ sản phẩm từ giỏ hàng trước đăng nhập vào tài khoản của bạn thành công!")
    except ValidationError as e:
        messages.error(
            request,
            f"Hệ thống đang bận, chưa thể đồng bộ giỏ hàng lúc này ({e.message}). Vui lòng nhấn thử lại sau ít giây."
        )

    return redirect('cart:cart_detail')


def cart_coupon_preview_view(request):

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Phương thức yêu cầu không hợp lệ. Vui lòng gửi yêu cầu POST.")

    is_allowed, error_msg = _check_cart_authorization(request)
    if not is_allowed:
        return HttpResponseForbidden(error_msg)

    has_pending_guest_cart = False
    if request.user.is_authenticated and hasattr(request, 'session') and request.session.get('_pending_guest_cart_session_key'):
        has_pending_guest_cart = True

    categories = Category.objects.filter(is_visible=True).order_by('name')
    raw_code = request.POST.get('code', '').strip()

    try:
        cart = resolve_cart(request, create_if_empty=False)
    except ValidationError:
        cart = None

    evaluation = get_cart_evaluation(cart)
    purchasable_lines = [line for line in evaluation.get('lines', []) if line.get('is_available')]

    if not cart or not purchasable_lines:
        form = CouponPreviewForm(request.POST)
        context = {
            'cart': cart,
            'evaluation': evaluation,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Giỏ hàng — Quốc Hùng Mobile',
            'has_pending_guest_cart': has_pending_guest_cart,
            'coupon_form': form,
            'coupon_error': 'Giỏ hàng của bạn đang trống hoặc không có sản phẩm khả dụng.',
            'raw_coupon_code': raw_code,
        }
        return render(request, 'cart/cart_detail.html', context)

    form = CouponPreviewForm(request.POST)
    if not form.is_valid():
        error_message = form.errors['code'][0] if 'code' in form.errors else "Mã giảm giá không hợp lệ."
        context = {
            'cart': cart,
            'evaluation': evaluation,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Giỏ hàng — Quốc Hùng Mobile',
            'has_pending_guest_cart': has_pending_guest_cart,
            'coupon_form': form,
            'coupon_error': error_message,
            'raw_coupon_code': raw_code,
        }
        return render(request, 'cart/cart_detail.html', context)

    cleaned_code = form.cleaned_data['code']
    try:
        quote = evaluate_coupon_for_cart(cart, cleaned_code)
        quote['discount_amount_formatted'] = format_vnd(quote['discount_amount'])
        quote['total_after_discount_formatted'] = format_vnd(quote['total_after_discount'])
        quote['eligible_subtotal_formatted'] = format_vnd(quote['eligible_subtotal'])
        quote['cart_subtotal_formatted'] = format_vnd(quote['cart_subtotal'])

        context = {
            'cart': cart,
            'evaluation': evaluation,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Giỏ hàng — Quốc Hùng Mobile',
            'has_pending_guest_cart': has_pending_guest_cart,
            'coupon_form': form,
            'coupon_quote': quote,
            'coupon_applied': True,
            'raw_coupon_code': cleaned_code,
        }
        return render(request, 'cart/cart_detail.html', context)
    except ValidationError as exc:
        context = {
            'cart': cart,
            'evaluation': evaluation,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Giỏ hàng — Quốc Hùng Mobile',
            'has_pending_guest_cart': has_pending_guest_cart,
            'coupon_form': form,
            'coupon_error': exc.message,
            'raw_coupon_code': cleaned_code,
        }
        return render(request, 'cart/cart_detail.html', context)

