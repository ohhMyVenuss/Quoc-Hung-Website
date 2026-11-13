import django.db.models.deletion
import django.db.models.expressions
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0006_productimage_variant'),
    ]

    operations = [
        migrations.CreateModel(
            name='Coupon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(help_text='Tối đa 40 ký tự. Bắt buộc nhập, duy nhất, chuẩn hóa chữ hoa và cắt khoảng trắng viền.', max_length=40, unique=True, verbose_name='Mã Voucher Code')),
                ('discount_type', models.CharField(choices=[('PERCENT', 'Phần trăm (%)'), ('FIXED', 'Số tiền cố định (VND)')], help_text='PERCENT (Phần trăm) hoặc FIXED (Số tiền cố định).', max_length=10, verbose_name='Loại hình giảm giá')),
                ('value', models.DecimalField(decimal_places=2, help_text='PERCENT: > 0 và <= 100; FIXED: > 0 (VND).', max_digits=14, verbose_name='Mức giá trị giảm')),
                ('max_discount', models.DecimalField(blank=True, decimal_places=2, help_text='Chỉ áp dụng khi giảm theo %, để trống (NULL) nếu không giới hạn trần. Giá trị >= 0.', max_digits=14, null=True, verbose_name='Trần giảm tối đa (VND)')),
                ('min_order_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Mặc định 0 đ. Giá trị >= 0.', max_digits=14, verbose_name='Giá trị đơn hàng tối thiểu (VND)')),
                ('max_uses', models.PositiveIntegerField(help_text='Tổng số lượt sử dụng tối đa của mã. Bắt buộc > 0.', verbose_name='Tổng số lượt phát hành')),
                ('used_count', models.PositiveIntegerField(default=0, help_text='Server-managed: Tổng số lượt đã tiêu thụ thành công. Khởi tạo 0, chỉ đọc.', verbose_name='Số lượt đã dùng')),
                ('reserved_count', models.PositiveIntegerField(default=0, help_text='Server-managed: Số lượt đang tạm giữ cho đơn hàng chờ thanh toán. Khởi tạo 0, chỉ đọc.', verbose_name='Số lượt đang tạm giữ')),
                ('starts_at', models.DateTimeField(help_text='Thời điểm bắt đầu có hiệu lực (aware datetime).', verbose_name='Thời điểm bắt đầu')),
                ('ends_at', models.DateTimeField(help_text='Thời điểm hết hiệu lực (aware datetime). Bắt buộc sau thời điểm bắt đầu.', verbose_name='Thời điểm kết thúc')),
                ('is_active', models.BooleanField(default=True, help_text='Mặc định True. Nếu False, voucher bị tạm ngưng áp dụng.', verbose_name='Kích hoạt hoạt động')),
                ('applies_to_all', models.BooleanField(default=True, help_text='Mặc định True: áp dụng cho tất cả sản phẩm. False: chỉ áp dụng cho sản phẩm trong danh sách CouponProduct.', verbose_name='Áp dụng toàn bộ sản phẩm')),
            ],
            options={
                'verbose_name': 'Mã giảm giá (Coupon)',
                'verbose_name_plural': 'Danh sách mã giảm giá (Coupons)',
                'db_table': 'coupons_coupon',
                'ordering': ['-starts_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('discount_type__in', ['PERCENT', 'FIXED'])), name='check_coupon_discount_type_valid'), models.CheckConstraint(condition=models.Q(models.Q(('discount_type', 'PERCENT'), ('value__gt', 0), ('value__lte', 100)), models.Q(('discount_type', 'FIXED'), ('value__gt', 0)), _connector='OR'), name='check_coupon_value_valid'), models.CheckConstraint(condition=models.Q(('max_discount__isnull', True), ('max_discount__gte', 0), _connector='OR'), name='check_coupon_max_discount_nonnegative'), models.CheckConstraint(condition=models.Q(('min_order_amount__gte', 0)), name='check_coupon_min_order_amount_nonnegative'), models.CheckConstraint(condition=models.Q(('max_uses__gt', 0)), name='check_coupon_max_uses_positive'), models.CheckConstraint(condition=models.Q(('used_count__gte', 0), ('reserved_count__gte', 0), ('used_count__lte', django.db.models.expressions.CombinedExpression(models.F('max_uses'), '-', models.F('reserved_count')))), name='check_coupon_counter_quota'), models.CheckConstraint(condition=models.Q(('ends_at__gt', models.F('starts_at'))), name='check_coupon_time_ordering'), models.UniqueConstraint(fields=('code',), name='unique_coupon_code')],
            },
        ),
        migrations.CreateModel(
            name='CouponProduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('coupon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='coupon_products', to='coupons.coupon', verbose_name='Mã giảm giá')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='coupon_products', to='catalog.product', verbose_name='Sản phẩm áp dụng')),
            ],
            options={
                'verbose_name': 'Sản phẩm áp dụng mã giảm giá',
                'verbose_name_plural': 'Các sản phẩm áp dụng mã giảm giá',
                'db_table': 'coupons_coupon_product',
                'constraints': [models.UniqueConstraint(fields=('coupon', 'product'), name='unique_coupon_product')],
            },
        ),
    ]
