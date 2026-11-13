import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0002_paymenttransaction'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentReconciliation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason_code', models.CharField(choices=[('UNDERPAYMENT', 'Chuyển khoản thiếu tiền'), ('OVERPAYMENT', 'Chuyển khoản thừa tiền'), ('WRONG_REFERENCE', 'Sai nội dung / mã tham chiếu'), ('UNCLEAR_OR_EXPIRED', 'Giao dịch chưa rõ hoặc đơn hết hạn chưa xác nhận'), ('TRANSFER_AFTER_CANCELLATION', 'Phát hiện tiền chuyển sau khi đơn đã hủy'), ('OTHER', 'Ngoại lệ khác cần đối soát')], help_text='Mã lý do phát sinh đối soát (NOT NULL, tối đa 40 ký tự).', max_length=40, verbose_name='Mã lý do đối soát')),
                ('status', models.CharField(choices=[('OPEN', 'Chờ xử lý đối soát'), ('RESOLVED', 'Đã đối soát giải quyết'), ('ESCALATED', 'Đã chuyển cấp quản trị')], default='OPEN', help_text='Trạng thái xử lý hồ sơ đối soát (NOT NULL, mặc định OPEN).', max_length=30, verbose_name='Trạng thái đối soát')),
                ('notes', models.TextField(blank=True, help_text='Ghi chú chi tiết, mô tả ngoại lệ hoặc thông tin liên lạc đối soát.', null=True, verbose_name='Ghi chú đối soát')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Thời điểm tạo hồ sơ đối soát trong hệ thống.', verbose_name='Thời điểm tạo hồ sơ')),
                ('resolved_at', models.DateTimeField(blank=True, help_text='Thời điểm giải quyết xong hồ sơ đối soát (NULL khi chưa giải quyết).', null=True, verbose_name='Thời điểm giải quyết')),
                ('sla_due_at', models.DateTimeField(blank=True, help_text='Hạn 12 giờ làm việc từ created_at dựa vào lịch vận hành cấu hình (NULL khi chưa kích hoạt).', null=True, verbose_name='Hạn chót SLA')),
                ('escalated_at', models.DateTimeField(blank=True, help_text='Thời điểm chuyển cấp quản trị khi quá SLA chưa xử lý (cho phép NULL, không auto cancel).', null=True, verbose_name='Thời điểm chuyển cấp quản trị')),
                ('handled_by', models.ForeignKey(blank=True, help_text='Tài khoản nhân viên bán hàng hoặc admin mở hoặc xử lý hồ sơ đối soát (on_delete=SET_NULL).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='handled_reconciliations', to=settings.AUTH_USER_MODEL, verbose_name='Nhân sự phụ trách / mở hồ sơ')),
                ('payment', models.ForeignKey(help_text='Bản ghi thanh toán phát sinh ngoại lệ cần đối soát (NOT NULL, on_delete=PROTECT bảo toàn hồ sơ tài chính).', on_delete=django.db.models.deletion.PROTECT, related_name='reconciliations', to='payments.payment', verbose_name='Bản ghi thanh toán')),
                ('transaction', models.ForeignKey(blank=True, help_text='Giao dịch ngân hàng liên quan (cho phép NULL; chỉ liên kết nếu thuộc cùng Payment, không chế tạo giao dịch giả).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reconciliations', to='payments.paymenttransaction', verbose_name='Giao dịch liên quan')),
            ],
            options={
                'verbose_name': 'Hồ sơ đối soát thanh toán',
                'verbose_name_plural': 'Hồ sơ đối soát thanh toán',
                'ordering': ['-created_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('reason_code__in', ['UNDERPAYMENT', 'OVERPAYMENT', 'WRONG_REFERENCE', 'UNCLEAR_OR_EXPIRED', 'TRANSFER_AFTER_CANCELLATION', 'OTHER'])), name='reconciliation_reason_valid'), models.CheckConstraint(condition=models.Q(('status__in', ['OPEN', 'RESOLVED', 'ESCALATED'])), name='reconciliation_status_valid'), models.UniqueConstraint(condition=models.Q(('status', 'OPEN')), fields=('payment',), name='unique_open_reconciliation_per_payment')],
            },
        ),
    ]
