import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0006_productimage_variant'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Cart',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(blank=True, db_index=True, help_text='Khóa phiên Django session dành cho khách vãng lai.', max_length=64, null=True, verbose_name='Khóa phiên')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Thời điểm tạo')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Cập nhật gần nhất')),
                ('user', models.ForeignKey(blank=True, help_text='Tài khoản Khách hàng (Customer) sở hữu giỏ hàng này.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='carts', to=settings.AUTH_USER_MODEL, verbose_name='Khách hàng sở hữu')),
            ],
            options={
                'verbose_name': 'Giỏ hàng',
                'verbose_name_plural': 'Danh sách giỏ hàng',
                'ordering': ['-updated_at', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='CartItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(help_text='Số lượng sản phẩm muốn mua (nguyên dương > 0).', validators=[django.core.validators.MinValueValidator(1)], verbose_name='Số lượng')),
                ('price_at_addition', models.DecimalField(blank=True, decimal_places=2, help_text='Đơn giá tham chiếu lúc đưa vào giỏ để nhận biết thay đổi giá theo BR-CA04-CA06.', max_digits=14, null=True, verbose_name='Đơn giá lúc thêm vào giỏ')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Thời điểm tạo')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Cập nhật lúc')),
                ('cart', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='cart.cart', verbose_name='Giỏ hàng')),
                ('variant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cart_items', to='catalog.productvariant', verbose_name='Biến thể sản phẩm')),
            ],
            options={
                'verbose_name': 'Dòng giỏ hàng',
                'verbose_name_plural': 'Các dòng giỏ hàng',
                'ordering': ['id'],
            },
        ),
        migrations.AddConstraint(
            model_name='cart',
            constraint=models.CheckConstraint(condition=models.Q(('user__isnull', False), ('session_key__isnull', False), _connector='OR'), name='cart_user_or_session_required'),
        ),
        migrations.AddConstraint(
            model_name='cartitem',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='cartitem_quantity_gt_0'),
        ),
        migrations.AddConstraint(
            model_name='cartitem',
            constraint=models.UniqueConstraint(fields=('cart', 'variant'), name='unique_cart_variant'),
        ),
    ]
