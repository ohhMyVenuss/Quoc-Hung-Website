from accounts.permissions import (
    is_admin,
    is_sales_staff,
    is_inventory_staff,
    is_customer,
    get_user_roles,
)


def auth_roles(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not user.is_active:
        return {
            'is_customer': False,
            'is_sales_staff': False,
            'is_inventory_staff': False,
            'is_admin': False,
            'can_access_django_admin': False,
            'user_roles': set(),
        }

    return {
        'is_customer': is_customer(user),
        'is_sales_staff': is_sales_staff(user),
        'is_inventory_staff': is_inventory_staff(user),
        'is_admin': is_admin(user),
        'can_access_django_admin': bool(user.is_staff),
        'user_roles': get_user_roles(user),
    }
