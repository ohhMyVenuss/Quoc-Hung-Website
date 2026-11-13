import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0001_initial'),
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CouponUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('discount_amount', models.DecimalField(decimal_places=2, help_text='Số tiền giảm giá thực tế snapshot tại thời điểm áp dụng (>= 0 đ). Tuyệt đối không tính toán lại từ Coupon khi đọc hoặc lưu.', max_digits=14, verbose_name='Mức giảm giá snapshot (VND)')),
                ('status', models.CharField(choices=[('RESERVED', 'Giữ chỗ'), ('USED', 'Đã sử dụng'), ('RELEASED', 'Đã giải phóng')], help_text='RESERVED (giữ chỗ QR), USED (đã áp dụng thành công), RELEASED (đã giải phóng do hủy/hết hạn). Không có giá trị mặc định để tránh đồng nhất QR và COD.', max_length=12, verbose_name='Trạng thái sử dụng')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Thời điểm ghi nhận lượt sử dụng/giữ chỗ voucher.', verbose_name='Thời điểm tạo')),
                ('finalized_at', models.DateTimeField(blank=True, help_text='Thời điểm hoàn tất sử dụng (USED) hoặc giải phóng (RELEASED). Bắt buộc là NULL khi còn RESERVED.', null=True, verbose_name='Thời điểm hoàn tất/giải phóng')),
                ('coupon', models.ForeignKey(help_text='Mã voucher được sử dụng. on_delete=PROTECT bảo toàn lịch sử áp dụng voucher.', on_delete=django.db.models.deletion.PROTECT, related_name='usages', to='coupons.coupon', verbose_name='Mã giảm giá')),
                ('order', models.OneToOneField(help_text='Đơn hàng áp dụng mã voucher. Ràng buộc OneToOne: mỗi đơn hàng chỉ được áp dụng tối đa 01 voucher.', on_delete=django.db.models.deletion.PROTECT, related_name='coupon_usage', to='orders.order', verbose_name='Đơn hàng áp dụng')),
            ],
            options={
                'verbose_name': 'Lượt sử dụng mã giảm giá (CouponUsage)',
                'verbose_name_plural': 'Danh sách lượt sử dụng mã giảm giá (CouponUsages)',
                'db_table': 'coupons_coupon_usage',
                'ordering': ['-created_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('discount_amount__gte', 0)), name='coupon_usage_discount_amount_gte_0'), models.CheckConstraint(condition=models.Q(('status__in', ['RESERVED', 'USED', 'RELEASED'])), name='coupon_usage_status_valid')],
            },
        ),
    ]
