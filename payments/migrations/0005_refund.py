import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0001_initial'),
        ('payments', '0004_update_reconciliation_constraint'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Refund',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason_type', models.CharField(choices=[('CANCEL', 'Khách hàng hủy đơn hàng'), ('RETURN', 'Trả hàng hoàn tiền'), ('EXCHANGE', 'Đổi hàng chênh lệch'), ('LATE_PAYMENT', 'Chuyển khoản muộn sau khi đơn hết hạn hoặc đã hủy'), ('FAILED_DELIVERY', 'Giao hàng thất bại sau khi xuất kho')], help_text='Phân loại lý do hoàn tiền theo Bảng 20 (CANCEL, RETURN, EXCHANGE, LATE_PAYMENT, FAILED_DELIVERY).', max_length=20, verbose_name='Lý do hoàn tiền')),
                ('amount', models.DecimalField(decimal_places=2, help_text='Số tiền hoàn thực tế (NOT NULL, > 0, tối đa 14 chữ số và 2 chữ số thập phân).', max_digits=14, verbose_name='Số tiền hoàn')),
                ('status', models.CharField(choices=[('PENDING', 'Chờ xử lý'), ('PROCESSING', 'Đang xử lý'), ('COMPLETED', 'Đã hoàn tất'), ('REJECTED', 'Từ chối')], default='PENDING', help_text='Trạng thái xử lý hoàn tiền theo Bảng 20 (PENDING, PROCESSING, COMPLETED, REJECTED).', max_length=25, verbose_name='Trạng thái hoàn tiền')),
                ('bank_reference', models.CharField(blank=True, help_text='Mã giao dịch ngân hàng chuyển tiền hoàn thực tế (UNIQUE nếu có, cho phép NULL).', max_length=100, null=True, unique=True, verbose_name='Mã tham chiếu ngân hàng')),
                ('completed_at', models.DateTimeField(blank=True, help_text='Thời điểm giao dịch chuyển tiền hoàn thành công theo xác nhận ngân hàng (NULL khi chưa hoàn).', null=True, verbose_name='Thời điểm hoàn tất')),
                ('confirmed_by', models.ForeignKey(blank=True, help_text='Tài khoản Admin xác nhận việc hoàn tiền (SRS v2.3 BR-PAY-09: chỉ Admin có thẩm quyền).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='confirmed_refunds', to=settings.AUTH_USER_MODEL, verbose_name='Quản trị viên xác nhận')),
                ('order', models.ForeignKey(help_text='Đơn hàng phát sinh hoàn tiền (NOT NULL -> Order, on_delete=PROTECT bảo toàn chứng từ kế toán).', on_delete=django.db.models.deletion.PROTECT, related_name='refunds', to='orders.order', verbose_name='Đơn hàng')),
            ],
            options={
                'verbose_name': 'Bản ghi hoàn tiền',
                'verbose_name_plural': 'Danh sách hoàn tiền',
                'ordering': ['-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='refund_amount_gt_0'), models.CheckConstraint(condition=models.Q(('reason_type__in', ['CANCEL', 'RETURN', 'EXCHANGE', 'LATE_PAYMENT', 'FAILED_DELIVERY'])), name='refund_reason_type_valid'), models.CheckConstraint(condition=models.Q(('status__in', ['PENDING', 'PROCESSING', 'COMPLETED', 'REJECTED'])), name='refund_status_valid'), models.UniqueConstraint(django.db.models.functions.text.Upper('bank_reference'), condition=models.Q(('bank_reference__isnull', False)), name='unique_refund_bank_ref_upper')],
            },
        ),
    ]
