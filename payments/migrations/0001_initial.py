import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('orders', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('method', models.CharField(choices=[('COD', 'Thanh toán khi nhận hàng (COD)'), ('QR', 'Chuyển khoản ngân hàng qua mã QR')], help_text='Phương thức thanh toán (COD hoặc QR, không gán mặc định ngầm định).', max_length=10, verbose_name='Phương thức thanh toán')),
                ('status', models.CharField(choices=[('UNPAID', 'Chưa thanh toán'), ('PENDING_VERIFY', 'Chờ xác minh'), ('PAID', 'Đã thanh toán'), ('FAILED', 'Thanh toán thất bại'), ('EXPIRED', 'Hết hạn thanh toán'), ('PENDING_REFUND', 'Chờ hoàn tiền'), ('REFUNDED', 'Đã hoàn tiền'), ('PARTIALLY_REFUNDED', 'Hoàn tiền một phần')], help_text='Trạng thái thanh toán độc lập (yêu cầu gán tường minh, không gán mặc định ngầm định).', max_length=30, verbose_name='Trạng thái thanh toán')),
                ('expected_amount', models.DecimalField(decimal_places=2, help_text='Số tiền cần thanh toán snapshot tại thời điểm khởi tạo (>= 0, không tự động tính lại).', max_digits=14, verbose_name='Số tiền dự kiến')),
                ('qr_reference', models.CharField(blank=True, help_text='Mã tham chiếu chuyển khoản hoặc mã đơn hàng phục vụ đối soát QR (tối đa 60 ký tự, cho phép NULL).', max_length=60, null=True, verbose_name='Mã tham chiếu QR')),
                ('expires_at', models.DateTimeField(blank=True, help_text='Thời điểm hết hạn thanh toán đối với đơn hàng QR (NULL cho COD hoặc khi chưa kích hoạt thời hạn).', null=True, verbose_name='Thời hạn thanh toán')),
                ('customer_notified_at', models.DateTimeField(blank=True, help_text='Thời điểm khách hàng bấm thông báo đã chuyển khoản (NULL khi khách chưa thông báo).', null=True, verbose_name='Thời điểm khách thông báo')),
                ('verified_at', models.DateTimeField(blank=True, help_text='Thời điểm giao dịch được xác nhận tiền vào thực tế (NULL khi chưa xác minh).', null=True, verbose_name='Thời điểm xác minh')),
                ('notes', models.TextField(blank=True, help_text='Ghi chú đối soát, lý do từ chối hoặc thông tin bổ sung.', null=True, verbose_name='Ghi chú thanh toán')),
                ('order', models.OneToOneField(help_text='Đơn hàng gắn liền với bản ghi thanh toán (OneToOne, on_delete=PROTECT bảo toàn lịch sử tài chính).', on_delete=django.db.models.deletion.PROTECT, related_name='payment', to='orders.order', verbose_name='Đơn hàng')),
                ('verified_by', models.ForeignKey(blank=True, help_text='Tài khoản nhân viên hoặc admin thực hiện xác minh giao dịch thực tế (on_delete=SET_NULL bảo toàn lịch sử thanh toán).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='verified_payments', to=settings.AUTH_USER_MODEL, verbose_name='Nhân sự xác minh')),
            ],
            options={
                'verbose_name': 'Thanh toán',
                'verbose_name_plural': 'Thanh toán',
                'ordering': ['-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('expected_amount__gte', 0)), name='payment_expected_amount_gte_0'), models.CheckConstraint(condition=models.Q(('method__in', ['COD', 'QR'])), name='payment_method_valid'), models.CheckConstraint(condition=models.Q(('status__in', ['UNPAID', 'PENDING_VERIFY', 'PAID', 'FAILED', 'EXPIRED', 'PENDING_REFUND', 'REFUNDED', 'PARTIALLY_REFUNDED'])), name='payment_status_valid')],
            },
        ),
    ]
