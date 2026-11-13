import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0002_backfill_inventory'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transaction_type', models.CharField(choices=[('PURCHASE_IN', 'Nhập hàng')], default='PURCHASE_IN', help_text='Loại biến động kho (PURCHASE_IN).', max_length=25, verbose_name='Loại giao dịch')),
                ('quantity_delta', models.IntegerField(help_text='Số lượng thay đổi tồn kho có dấu (dương khi nhập, khác 0).', verbose_name='Số lượng biến động')),
                ('physical_before', models.IntegerField(help_text='Số lượng tồn thực tế trong kho trước thời điểm giao dịch (>= 0).', verbose_name='Tồn thực tế trước')),
                ('physical_after', models.IntegerField(help_text='Số lượng tồn thực tế trong kho sau thời điểm giao dịch (>= 0).', verbose_name='Tồn thực tế sau')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Thời điểm ghi nhận giao dịch vào sổ cái kho.', verbose_name='Thời gian tạo')),
                ('request_key', models.UUIDField(editable=False, help_text='Mã UUID kỹ thuật định danh duy nhất yêu cầu nhập hàng (Idempotency Key).', unique=True, verbose_name='Khóa yêu cầu')),
                ('inventory', models.ForeignKey(help_text='Bản ghi tồn kho của biến thể sản phẩm phát sinh giao dịch.', on_delete=django.db.models.deletion.PROTECT, related_name='transactions', to='inventory.inventory', verbose_name='Tồn kho biến thể')),
                ('performed_by', models.ForeignKey(blank=True, help_text='Tài khoản nhân viên kho hoặc quản trị viên thực hiện thao tác.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_transactions', to=settings.AUTH_USER_MODEL, verbose_name='Người thực hiện')),
            ],
            options={
                'verbose_name': 'Biến động tồn kho',
                'verbose_name_plural': 'Sổ cái biến động kho',
                'ordering': ['-created_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('quantity_delta', 0), _negated=True), name='inv_tx_quantity_delta_nonzero'), models.CheckConstraint(condition=models.Q(('physical_before__gte', 0)), name='inv_tx_physical_before_gte_0'), models.CheckConstraint(condition=models.Q(('physical_after__gte', 0)), name='inv_tx_physical_after_gte_0')],
            },
        ),
    ]
