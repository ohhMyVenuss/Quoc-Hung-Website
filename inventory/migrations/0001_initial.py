import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0005_product_variant'),
    ]

    operations = [
        migrations.CreateModel(
            name='Inventory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('physical_sellable', models.IntegerField(default=0, help_text='Số lượng hàng thực tế trong kho đủ điều kiện bán (không âm).', verbose_name='Tồn thực tế')),
                ('reserved', models.IntegerField(default=0, help_text='Số lượng hàng đã được giữ cho các đơn hàng chưa xuất kho (0 <= reserved <= physical_sellable).', verbose_name='Tồn đã giữ')),
                ('min_threshold', models.IntegerField(default=0, help_text='Ngưỡng số lượng kích hoạt cảnh báo tồn kho thấp / cần nhập hàng (>= 0).', verbose_name='Ngưỡng cảnh báo tối thiểu')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Cập nhật lúc')),
                ('variant', models.OneToOneField(help_text='Biến thể sản phẩm được quản lý tồn kho (quan hệ 1-1).', on_delete=django.db.models.deletion.PROTECT, related_name='inventory', to='catalog.productvariant', verbose_name='Biến thể sản phẩm')),
            ],
            options={
                'verbose_name': 'Tồn kho',
                'verbose_name_plural': 'Tồn kho',
                'ordering': ['id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('physical_sellable__gte', 0)), name='inventory_physical_sellable_gte_0'), models.CheckConstraint(condition=models.Q(('reserved__gte', 0)), name='inventory_reserved_gte_0'), models.CheckConstraint(condition=models.Q(('min_threshold__gte', 0)), name='inventory_min_threshold_gte_0'), models.CheckConstraint(condition=models.Q(('reserved__lte', models.F('physical_sellable'))), name='inventory_reserved_lte_physical_sellable')],
            },
        ),
    ]
