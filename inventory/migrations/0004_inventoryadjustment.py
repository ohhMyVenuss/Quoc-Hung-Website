import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0003_inventorytransaction'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='inventorytransaction',
            name='transaction_type',
            field=models.CharField(choices=[('PURCHASE_IN', 'Nhập hàng'), ('ADJUSTMENT', 'Kiểm kê kho')], default='PURCHASE_IN', help_text='Loại biến động kho (PURCHASE_IN).', max_length=25, verbose_name='Loại giao dịch'),
        ),
        migrations.CreateModel(
            name='InventoryAdjustment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_quantity', models.IntegerField(help_text='Số lượng tồn thực tế trong kho trước khi kiểm kê điều chỉnh (>= 0).', verbose_name='Tồn thực tế trước')),
                ('new_quantity', models.IntegerField(help_text='Số lượng tồn thực tế sau khi kiểm kê điều chỉnh (>= 0, >= reserved).', verbose_name='Tồn thực tế sau')),
                ('reason', models.TextField(help_text='Lý do thực hiện điều chỉnh tồn kho (bắt buộc).', verbose_name='Lý do điều chỉnh')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Thời điểm ghi nhận bản ghi điều chỉnh kiểm kê.', verbose_name='Thời gian tạo')),
                ('adjusted_by', models.ForeignKey(help_text='Tài khoản nhân viên kho hoặc quản trị viên thực hiện điều chỉnh.', on_delete=django.db.models.deletion.PROTECT, related_name='inventory_adjustments', to=settings.AUTH_USER_MODEL, verbose_name='Người điều chỉnh')),
                ('inventory', models.ForeignKey(help_text='Bản ghi tồn kho của biến thể sản phẩm được kiểm kê điều chỉnh.', on_delete=django.db.models.deletion.PROTECT, related_name='adjustments', to='inventory.inventory', verbose_name='Tồn kho biến thể')),
                ('transaction', models.OneToOneField(blank=True, help_text='Bản ghi giao dịch sổ cái tương ứng phát sinh từ lần kiểm kê này (1-1).', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='adjustment', to='inventory.inventorytransaction', verbose_name='Giao dịch sổ cái liên kết')),
            ],
            options={
                'verbose_name': 'Điều chỉnh kiểm kê',
                'verbose_name_plural': 'Lịch sử điều chỉnh kiểm kê',
                'ordering': ['-created_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('old_quantity__gte', 0)), name='inv_adj_old_quantity_gte_0'), models.CheckConstraint(condition=models.Q(('new_quantity__gte', 0)), name='inv_adj_new_quantity_gte_0')],
            },
        ),
    ]
