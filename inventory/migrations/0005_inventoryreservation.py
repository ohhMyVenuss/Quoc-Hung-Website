import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0005_product_variant'),
        ('inventory', '0004_inventoryadjustment'),
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.IntegerField(help_text='Số lượng sản phẩm được giữ (> 0).', verbose_name='Số lượng giữ')),
                ('status', models.CharField(choices=[('ACTIVE', 'Đang giữ'), ('RELEASED', 'Đã giải phóng'), ('SHIPPED', 'Đã xuất giao')], help_text='Trạng thái giữ hàng (ACTIVE, RELEASED, SHIPPED).', max_length=15, verbose_name='Trạng thái giữ hàng')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Thời điểm tạo bản ghi giữ hàng.', verbose_name='Thời gian tạo')),
                ('finished_at', models.DateTimeField(blank=True, help_text='Thời điểm kết thúc giữ hàng (bắt buộc NULL khi còn ACTIVE).', null=True, verbose_name='Thời gian kết thúc')),
                ('order_item', models.ForeignKey(help_text='Dòng đơn hàng nguồn giữ hàng (on_delete=PROTECT bảo toàn liên kết giữ hàng).', on_delete=django.db.models.deletion.PROTECT, related_name='reservations', to='orders.orderitem', verbose_name='Dòng đơn hàng')),
                ('variant', models.ForeignKey(help_text='Biến thể sản phẩm được giữ hàng (on_delete=PROTECT bảo toàn liên kết giữ hàng).', on_delete=django.db.models.deletion.PROTECT, related_name='reservations', to='catalog.productvariant', verbose_name='Biến thể sản phẩm')),
            ],
            options={
                'verbose_name': 'Giữ hàng tồn kho',
                'verbose_name_plural': 'Dòng giữ hàng tồn kho',
                'ordering': ['-created_at', '-id'],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='inv_reservation_quantity_gt_0'),
                    models.CheckConstraint(condition=models.Q(('status__in', ['ACTIVE', 'RELEASED', 'SHIPPED'])), name='inv_reservation_status_valid'),
                ],
            },
        ),
    ]

