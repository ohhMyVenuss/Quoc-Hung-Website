import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bank_transaction_ref', models.CharField(blank=True, help_text='Mã giao dịch từ sao kê/sổ phụ ngân hàng (UNIQUE khi có mã; không tái sử dụng cho nhiều đơn).', max_length=100, null=True, unique=True, verbose_name='Mã giao dịch ngân hàng')),
                ('received_amount', models.DecimalField(decimal_places=2, help_text='Số tiền thực tế ghi có vào tài khoản ngân hàng (NOT NULL, >= 0, không tự động làm tròn).', max_digits=14, verbose_name='Số tiền thực nhận')),
                ('received_at', models.DateTimeField(blank=True, help_text='Thời điểm giao dịch ngân hàng phát sinh thực tế (cho phép NULL).', null=True, verbose_name='Thời điểm nhận tiền')),
                ('recipient_account_last4', models.CharField(blank=True, help_text='4 số cuối tài khoản thụ hưởng phục vụ kiểm tra đối soát (cho phép NULL).', max_length=4, null=True, verbose_name='4 số cuối tài khoản nhận')),
                ('transfer_content', models.CharField(blank=True, help_text='Nội dung chuyển khoản thực tế ghi nhận trên sao kê/sổ phụ ngân hàng (cho phép NULL).', max_length=255, null=True, verbose_name='Nội dung chuyển khoản')),
                ('verification_status', models.CharField(help_text='Trạng thái xác minh đối soát (NOT NULL, ví dụ: VERIFIED).', max_length=30, verbose_name='Trạng thái xác minh')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Thời điểm bản ghi giao dịch được lưu trữ vào hệ thống.', verbose_name='Thời điểm tạo bản ghi')),
                ('payment', models.ForeignKey(help_text='Bản ghi thanh toán gắn liền (OneToMany, on_delete=PROTECT bảo toàn chứng từ tài chính).', on_delete=django.db.models.deletion.PROTECT, related_name='transactions', to='payments.payment', verbose_name='Bản ghi thanh toán')),
                ('verified_by', models.ForeignKey(blank=True, help_text='Tài khoản nhân viên bán hàng hoặc admin thực hiện đối chiếu xác minh (on_delete=SET_NULL bảo toàn lịch sử).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='verified_payment_transactions', to=settings.AUTH_USER_MODEL, verbose_name='Nhân sự xác minh')),
            ],
            options={
                'verbose_name': 'Giao dịch thanh toán',
                'verbose_name_plural': 'Giao dịch thanh toán',
                'ordering': ['-id'],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('received_amount__gte', 0)), name='payment_transaction_received_amount_gte_0'),
                    models.UniqueConstraint(models.functions.Upper('bank_transaction_ref'), name='unique_payment_tx_bank_ref_upper'),
                ],
            },
        ),
    ]

