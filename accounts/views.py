from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.models import User, Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from django.http import HttpResponseNotAllowed, HttpResponseForbidden, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.forms import (
    CustomerRegistrationForm,
    LoginForm,
    CustomerProfileForm,
    StaffCreationForm,
    StaffEditForm,
)
from accounts.models import CustomerProfile
from accounts.permissions import (
    is_admin,
    is_sales_staff,
    is_inventory_staff,
    is_customer,
    get_user_roles,
    ROLE_SALES_STAFF,
    ROLE_INVENTORY_STAFF,
    ROLE_ADMIN,
    ROLE_CUSTOMER,
)
from django.conf import settings
from django.db import DatabaseError, OperationalError
from django.contrib.auth.models import AnonymousUser
from importlib import import_module
from django.contrib.sessions.backends.base import UpdateError, CreateError
from cart import services
from cart.services import merge_guest_cart_to_user, retry_on_db_lock


def get_post_login_redirect_url(user, next_url=None, request=None):

    login_url = reverse('accounts:login')
    landing_url = reverse('accounts:landing')

    if next_url:
        next_clean = next_url.strip()
        allowed_hosts = {request.get_host()} if request else None
        require_https = request.is_secure() if request else False

        is_safe = url_has_allowed_host_and_scheme(
            url=next_clean,
            allowed_hosts=allowed_hosts,
            require_https=require_https
        )

        if is_safe and next_clean.rstrip('/') != login_url.rstrip('/'):
            return next_clean

    return landing_url


def register_view(request):

    if request.method == 'POST':
        form = CustomerRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                'Đăng ký tài khoản thành công! Chào mừng bạn đến với Quốc Hùng Mobile. '
                'Bạn đã được cộng ưu đãi thành viên và có thể đăng nhập ngay bây giờ.'
            )
            return redirect(reverse('accounts:register') + '?registered=true')
    else:
        form = CustomerRegistrationForm()

    registered_success = request.GET.get('registered') == 'true'

    context = {
        'form': form,
        'registered_success': registered_success,
    }
    return render(request, 'accounts/register.html', context)


def login_view(request):

    if request.user.is_authenticated:
        next_url = request.GET.get('next', '')
        return redirect(get_post_login_redirect_url(request.user, next_url, request))

    next_url = request.POST.get('next', '') or request.GET.get('next', '')

    if request.method == 'POST':
        form = LoginForm(request=request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            guest_session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME) or request.session.session_key

            from cart.models import Cart
            has_guest_cart = False
            if guest_session_key:
                has_guest_cart = Cart.objects.filter(session_key=guest_session_key, user__isnull=True).exists()

            has_pending_merge = False
            if has_guest_cart:
                try:
                    services.merge_guest_cart_to_user(guest_session_key, user)
                except ValidationError:
                    has_pending_merge = True

            remember_me = form.cleaned_data.get('remember_me', False)
            login_persisted = False
            try:
                def _do_login_and_persist():
                    login(request, user)
                    if has_pending_merge and guest_session_key:
                        request.session['_pending_guest_cart_session_key'] = guest_session_key
                    else:
                        request.session.pop('_pending_guest_cart_session_key', None)
                    if remember_me:
                        request.session.set_expiry(None)
                    else:
                        request.session.set_expiry(0)
                    request.session.save()

                services._execute_with_sqlite_retry(_do_login_and_persist, max_retries=5, initial_delay=0.01)
                login_persisted = True
            except (ValidationError, OperationalError, DatabaseError, UpdateError, CreateError):
                login_persisted = False

            if not login_persisted:

                request.user = AnonymousUser()
                if guest_session_key:
                    engine = import_module(settings.SESSION_ENGINE)
                    request.session = engine.SessionStore(guest_session_key)
                    request.session.load()
                request.session.modified = False

                form.add_error(
                    None,
                    "Hệ thống đang bận xử lý giao dịch khác, chưa thể hoàn tất đăng nhập lúc này. "
                    "Vui lòng thử lại sau ít giây. Giỏ hàng của bạn vẫn được bảo toàn nguyên vẹn."
                )
                context = {
                    'form': form,
                    'next_url': next_url,
                }
                response = render(request, 'accounts/login.html', context, status=503)
                response['Retry-After'] = '3'
                if guest_session_key:
                    response.set_cookie(
                        settings.SESSION_COOKIE_NAME,
                        guest_session_key,
                        domain=settings.SESSION_COOKIE_DOMAIN,
                        path=settings.SESSION_COOKIE_PATH,
                        secure=settings.SESSION_COOKIE_SECURE or None,
                        httponly=settings.SESSION_COOKIE_HTTPONLY or None,
                        samesite=settings.SESSION_COOKIE_SAMESITE,
                    )
                return response

            if has_pending_merge:
                messages.warning(
                    request,
                    "Đăng nhập thành công! Hệ thống đang bận nên giỏ hàng trước đăng nhập được lưu tạm "
                    "và bạn có thể nhấn đồng bộ tại trang Giỏ hàng."
                )
            else:
                messages.success(request, f'Đăng nhập thành công! Chào mừng bạn quay trở lại, {user.username}.')

            redirect_target = get_post_login_redirect_url(user, next_url, request)
            return redirect(redirect_target)
    else:
        form = LoginForm(request=request)

    context = {
        'form': form,
        'next_url': next_url,
    }
    return render(request, 'accounts/login.html', context)


def logout_view(request):

    if request.method != 'POST':
        return HttpResponseNotAllowed(
            ['POST'],
            'Phương thức yêu cầu không hợp lệ. Đăng xuất yêu cầu gửi request POST có CSRF token.'
        )

    logout(request)
    messages.info(request, 'Bạn đã đăng xuất khỏi hệ thống thành công.')
    return redirect(reverse('accounts:login'))


def landing_view(request):

    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    roles = get_user_roles(request.user)

    context = {
        'user': request.user,
        'roles': sorted(list(roles)),
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
        'is_customer': is_customer(request.user),
    }
    return render(request, 'accounts/landing.html', context)


def profile_view(request):

    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_customer(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    profile = getattr(request.user, 'customer_profile', None)
    if profile is None:
        profile = CustomerProfile.objects.filter(user=request.user).first()

    context = {
        'user': request.user,
        'profile': profile,
    }
    return render(request, 'accounts/profile.html', context)


def profile_edit_view(request):

    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_customer(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method == 'POST':
        form = CustomerProfileForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cập nhật thông tin cá nhân thành công!')
            return redirect('accounts:profile')
    else:
        form = CustomerProfileForm(user=request.user)

    context = {
        'form': form,
        'user': request.user,
    }
    return render(request, 'accounts/profile_edit.html', context)


def get_target_staff_user(user_id, current_user):

    target = get_object_or_404(User, id=user_id)

    if target.id == current_user.id:
        raise PermissionDenied("Không thể tự thao tác trên chính tài khoản của bạn qua chức năng quản lý nhân viên.")

    if target.is_superuser or target.is_staff:
        raise PermissionDenied("Tài khoản có đặc quyền quản trị hệ thống được bảo vệ.")

    if target.groups.filter(name=ROLE_ADMIN).exists():
        raise PermissionDenied("Tài khoản Quản trị viên (Admin) được bảo vệ.")

    if target.groups.filter(name=ROLE_CUSTOMER).exists() or CustomerProfile.objects.filter(user=target).exists():
        raise PermissionDenied("Tài khoản Khách hàng được bảo vệ, không thuộc phạm vi quản lý nhân viên.")

    if not target.groups.filter(name__in=[ROLE_SALES_STAFF, ROLE_INVENTORY_STAFF]).exists():
        raise PermissionDenied("Tài khoản không thuộc danh mục nhân viên được phép quản lý.")

    return target


def staff_list_view(request):

    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_admin(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    staff_users = (
        User.objects.filter(groups__name__in=[ROLE_SALES_STAFF, ROLE_INVENTORY_STAFF])
        .exclude(is_superuser=True)
        .exclude(is_staff=True)
        .exclude(groups__name=ROLE_ADMIN)
        .exclude(groups__name=ROLE_CUSTOMER)
        .exclude(customer_profile__isnull=False)
        .distinct()
        .prefetch_related('groups')
        .order_by('-date_joined')
    )

    query = request.GET.get('q', '').strip()
    if query:
        staff_users = staff_users.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )

    role_filter = request.GET.get('role', '').strip()
    if role_filter in [ROLE_SALES_STAFF, ROLE_INVENTORY_STAFF]:
        staff_users = staff_users.filter(groups__name=role_filter)

    status_filter = request.GET.get('status', '').strip()
    if status_filter == 'active':
        staff_users = staff_users.filter(is_active=True)
    elif status_filter == 'inactive':
        staff_users = staff_users.filter(is_active=False)

    staff_list = []
    for u in staff_users:
        user_group_names = set(u.groups.values_list('name', flat=True))
        staff_roles = []
        if ROLE_SALES_STAFF in user_group_names:
            staff_roles.append('Nhân viên bán hàng')
        if ROLE_INVENTORY_STAFF in user_group_names:
            staff_roles.append('Nhân viên kho')
        staff_list.append({
            'user': u,
            'roles_display': ', '.join(staff_roles) if staff_roles else 'Chưa gán vai trò',
            'is_sales': ROLE_SALES_STAFF in user_group_names,
            'is_inventory': ROLE_INVENTORY_STAFF in user_group_names,
        })

    context = {
        'staff_list': staff_list,
        'search_query': query,
        'role_filter': role_filter,
        'status_filter': status_filter,
        'active_nav': 'staff',
    }
    return render(request, 'accounts/staff_list.html', context)


def staff_create_view(request):

    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_admin(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method == 'POST':
        form = StaffCreationForm(request.POST)
        if form.is_valid():
            new_user = form.save()
            messages.success(request, f'Tạo mới tài khoản nhân viên "{new_user.username}" thành công!')
            return redirect('accounts:staff_list')
    else:
        form = StaffCreationForm()

    context = {
        'form': form,
        'active_nav': 'staff',
    }
    return render(request, 'accounts/staff_create.html', context)


def staff_edit_view(request, user_id):

    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_admin(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    target_user = get_target_staff_user(user_id, request.user)

    if request.method == 'POST':
        form = StaffEditForm(request.POST, target_user=target_user)
        if form.is_valid():
            form.save()
            messages.success(request, f'Cập nhật thông tin nhân viên "{target_user.username}" thành công!')
            return redirect('accounts:staff_list')
    else:
        form = StaffEditForm(target_user=target_user)

    context = {
        'form': form,
        'target_user': target_user,
        'active_nav': 'staff',
    }
    return render(request, 'accounts/staff_edit.html', context)


def staff_change_status_view(request, user_id):

    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={reverse('accounts:staff_list')}")

    if not is_admin(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Chức năng này chỉ chấp nhận phương thức POST an toàn.")

    target_user = get_target_staff_user(user_id, request.user)

    action = request.POST.get('action', '').strip().lower()
    if action == 'activate':
        target_user.is_active = True
        msg = f'Kích hoạt tài khoản nhân viên "{target_user.username}" thành công!'
    elif action == 'deactivate':
        target_user.is_active = False
        msg = f'Vô hiệu hóa tài khoản nhân viên "{target_user.username}" thành công!'
    else:
        return HttpResponseBadRequest("Hành động không hợp lệ. Chỉ chấp nhận 'activate' hoặc 'deactivate'.")

    target_user.save(update_fields=['is_active'])
    messages.success(request, msg)
    return redirect('accounts:staff_list')


