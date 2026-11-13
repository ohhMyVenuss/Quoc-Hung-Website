import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_inventoryreservation'),
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='inventorytransaction',
            name='order',
            field=models.ForeignKey(blank=True, help_text='Đơn hàng phát sinh giao dịch xuất kho (nếu có).', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_transactions', to='orders.order', verbose_name='Đơn hàng liên kết'),
        ),
        migrations.AlterField(
            model_name='inventorytransaction',
            name='transaction_type',
            field=models.CharField(choices=[('PURCHASE_IN', 'Nhập hàng'), ('ADJUSTMENT', 'Kiểm kê kho'), ('SHIP', 'Xuất kho giao hàng')], default='PURCHASE_IN', help_text='Loại biến động kho (PURCHASE_IN).', max_length=25, verbose_name='Loại giao dịch'),
        ),
    ]
