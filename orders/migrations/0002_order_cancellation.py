import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderCancellation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason_code', models.CharField(choices=[('WRONG_ORDER', 'Đặt nhầm'), ('CHANGE_VARIANT', 'Muốn đổi SP/biến thể'), ('FOUND_BETTER', 'Tìm sản phẩm phù hợp hơn'), ('NO_LONGER_NEEDED', 'Không còn nhu cầu'), ('SLOW_PROCESSING', 'Xử lý lâu'), ('OTHER', 'Khác')], help_text='Mã lý do hủy đơn (NOT NULL, choices, có OTHER).', max_length=40, verbose_name='Lý do hủy')),
                ('description', models.TextField(blank=True, help_text='Mô tả bổ sung (bắt buộc nếu reason_code=OTHER, tối đa 500 ký tự).', null=True, verbose_name='Mô tả chi tiết')),
                ('cancelled_at', models.DateTimeField(default=django.utils.timezone.now, help_text='Thời điểm ghi nhận sự kiện hủy đơn.', verbose_name='Thời điểm hủy')),
                ('reservation_released_at', models.DateTimeField(blank=True, help_text='Thời điểm hoàn tất giải phóng giữ kho (ghi đúng 1 lần).', null=True, verbose_name='Thời điểm giải phóng giữ tồn')),
                ('order', models.OneToOneField(help_text='Đơn hàng bị hủy (OneToOne, on_delete=PROTECT bảo toàn lịch sử hủy đơn).', on_delete=django.db.models.deletion.PROTECT, related_name='cancellation', to='orders.order', verbose_name='Đơn hàng')),
                ('requested_by', models.ForeignKey(blank=True, help_text='Tài khoản người dùng yêu cầu hủy (NULL nếu do hệ thống tự động đổi).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_cancellations', to=settings.AUTH_USER_MODEL, verbose_name='Người yêu cầu hủy')),
            ],
            options={
                'verbose_name': 'Bản ghi hủy đơn hàng',
                'verbose_name_plural': 'Bản ghi hủy đơn hàng',
                'ordering': ['-cancelled_at', '-id'],
            },
        ),
    ]

