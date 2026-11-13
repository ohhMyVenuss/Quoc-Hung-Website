from functools import wraps
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden

ROLE_CUSTOMER = 'Customer'
ROLE_SALES_STAFF = 'Sales Staff'
ROLE_INVENTORY_STAFF = 'Inventory Staff'
ROLE_ADMIN = 'Admin'

ALL_ROLES = (
    ROLE_CUSTOMER,
    ROLE_SALES_STAFF,
    ROLE_INVENTORY_STAFF,
    ROLE_ADMIN,
)


def _is_valid_user(user):
    return user is not None and user.is_authenticated and user.is_active


def is_admin(user):

    if not _is_valid_user(user):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=ROLE_ADMIN).exists()


def is_sales_staff(user):

    if not _is_valid_user(user):
        return False
    if is_admin(user):
        return True
    return user.groups.filter(name=ROLE_SALES_STAFF).exists()


def is_inventory_staff(user):

    if not _is_valid_user(user):
        return False
    if is_admin(user):
        return True
    return user.groups.filter(name=ROLE_INVENTORY_STAFF).exists()


def is_customer(user):

    if not _is_valid_user(user):
        return False
    return user.groups.filter(name=ROLE_CUSTOMER).exists()


def get_user_roles(user):

    roles = set()
    if not _is_valid_user(user):
        return roles

    if is_customer(user):
        roles.add(ROLE_CUSTOMER)
    if is_admin(user):
        roles.add(ROLE_ADMIN)
        roles.add(ROLE_SALES_STAFF)
        roles.add(ROLE_INVENTORY_STAFF)
    else:
        if is_sales_staff(user):
            roles.add(ROLE_SALES_STAFF)
        if is_inventory_staff(user):
            roles.add(ROLE_INVENTORY_STAFF)

    return roles


def has_role(user, role_name):
    if role_name == ROLE_CUSTOMER:
        return is_customer(user)
    elif role_name == ROLE_SALES_STAFF:
        return is_sales_staff(user)
    elif role_name == ROLE_INVENTORY_STAFF:
        return is_inventory_staff(user)
    elif role_name == ROLE_ADMIN:
        return is_admin(user)
    return False


def has_any_role(user, *role_names):
    return any(has_role(user, r) for r in role_names)


def role_required(*allowed_roles, raise_exception=True):

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not _is_valid_user(request.user):
                if raise_exception:
                    raise PermissionDenied("Yêu cầu đăng nhập để truy cập.")
                return HttpResponseForbidden("Yêu cầu đăng nhập để truy cập.")

            if not has_any_role(request.user, *allowed_roles):
                if raise_exception:
                    raise PermissionDenied("Bạn không có quyền truy cập chức năng này.")
                return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator
