from django.db import migrations, models
import django.db.models.deletion


def reconcile_duplicate_carts(apps, schema_editor):
    Cart = apps.get_model('cart', 'Cart')
    CartItem = apps.get_model('cart', 'CartItem')
    ProductVariant = apps.get_model('catalog', 'ProductVariant')

    def _reconcile_items(primary_cart, sec_cart):
        for s_item in sec_cart.items.all():
            p_item = primary_cart.items.filter(variant_id=s_item.variant_id).first()
            if p_item:
                p_item.quantity += s_item.quantity

                variant = ProductVariant.objects.filter(id=s_item.variant_id).first()
                curr_price = variant.price if variant else None

                if curr_price is not None:
                    if p_item.price_at_addition is not None and p_item.price_at_addition != curr_price:
                        pass
                    elif s_item.price_at_addition is not None and s_item.price_at_addition != curr_price:
                        p_item.price_at_addition = s_item.price_at_addition
                    elif p_item.price_at_addition is None and s_item.price_at_addition is not None:
                        p_item.price_at_addition = s_item.price_at_addition
                else:
                    if p_item.price_at_addition is None and s_item.price_at_addition is not None:
                        p_item.price_at_addition = s_item.price_at_addition

                p_item.save(update_fields=['quantity', 'price_at_addition'])
                s_item.delete()
            else:
                s_item.cart = primary_cart
                s_item.save(update_fields=['cart'])
        sec_cart.delete()

    from django.db.models import Count

    user_dups = (
        Cart.objects.filter(user__isnull=False)
        .values('user')
        .annotate(cnt=Count('id'))
        .filter(cnt__gt=1)
    )
    for entry in user_dups:
        user_id = entry['user']
        carts = list(Cart.objects.filter(user_id=user_id).order_by('id'))
        primary_cart = carts[0]
        for sec_cart in carts[1:]:
            _reconcile_items(primary_cart, sec_cart)

    session_dups = (
        Cart.objects.filter(user__isnull=True, session_key__isnull=False)
        .values('session_key')
        .annotate(cnt=Count('id'))
        .filter(cnt__gt=1)
    )
    for entry in session_dups:
        s_key = entry['session_key']
        carts = list(Cart.objects.filter(user__isnull=True, session_key=s_key).order_by('id'))
        primary_cart = carts[0]
        for sec_cart in carts[1:]:
            _reconcile_items(primary_cart, sec_cart)


class Migration(migrations.Migration):

    dependencies = [
        ('cart', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(reconcile_duplicate_carts, reverse_code=migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='cart',
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=False),
                fields=('user',),
                name='unique_active_user_cart'
            ),
        ),
        migrations.AddConstraint(
            model_name='cart',
            constraint=models.UniqueConstraint(
                condition=models.Q(session_key__isnull=False, user__isnull=True),
                fields=('session_key',),
                name='unique_active_session_cart'
            ),
        ),
    ]
