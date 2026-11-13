import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0006_productimage_variant'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Order',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order_code', models.CharField(help_text='Mã đơn hàng duy nhất hệ thống (NOT NULL, UNIQUE; tối đa 30 ký tự; dùng cho QR thanh toán).', max_length=30, unique=True, verbose_name='Mã đơn hàng')),
                ('recipient_name', models.CharField(help_text='Họ và tên người nhận hàng (tối đa 120 ký tự).', max_length=120, verbose_name='Tên người nhận')),
                ('recipient_phone', models.CharField(help_text='Số điện thoại liên hệ giao hàng (tối đa 15 ký tự).', max_length=15, verbose_name='Số điện thoại người nhận')),
                ('shipping_address', models.TextField(help_text='Địa chỉ chi tiết nhận hàng.', verbose_name='Địa chỉ giao hàng')),
                ('order_status', models.CharField(choices=[('PENDING_PAYMENT', 'Chờ thanh toán'), ('PENDING_RECONCILIATION', 'Chờ đối soát'), ('PENDING_CONFIRMATION', 'Chờ xác nhận'), ('CONFIRMED', 'Đã xác nhận'), ('SHIPPING', 'Đang giao hàng'), ('DELIVERED', 'Giao hàng thành công'), ('CANCELLED', 'Đã hủy'), ('EXPIRED', 'Hết hạn thanh toán'), ('DELIVERY_FAILED', 'Giao hàng thất bại'), ('RETURNING_TO_WAREHOUSE', 'Đang chuyển hoàn về kho'), ('FAILED_CLOSED', 'Đóng đơn giao thất bại')], help_text='Trạng thái đơn hàng theo quy trình nghiệp vụ (không gán giá trị mặc định để tránh đồng nhất QR và COD).', max_length=30, verbose_name='Trạng thái đơn hàng')),
                ('subtotal', models.DecimalField(decimal_places=2, help_text='Tổng tiền hàng trước khi áp dụng chiết khấu (>= 0).', max_digits=14, verbose_name='Tiền hàng trước giảm')),
                ('discount_total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Tổng số tiền giảm giá từ voucher/khuyến mãi (>= 0, mặc định 0.00).', max_digits=14, verbose_name='Tổng giảm giá')),
                ('grand_total', models.DecimalField(decimal_places=2, help_text='Số tiền thực tế khách hàng phải thanh toán (>= 0).', max_digits=14, verbose_name='Tổng thanh toán')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Thời điểm tạo đơn hàng.', verbose_name='Thời gian tạo')),
                ('delivered_at', models.DateTimeField(blank=True, help_text='Thời điểm giao hàng thành công (NULL đến khi hoàn tất giao hàng).', null=True, verbose_name='Thời gian giao thành công')),
                ('failed_delivery_at', models.DateTimeField(blank=True, help_text='Thời điểm ghi nhận giao hàng thất bại sau khi đã xuất kho (NULL đến khi phát sinh sự cố).', null=True, verbose_name='Thời gian giao thất bại')),
                ('customer', models.ForeignKey(help_text='Tài khoản người dùng đặt hàng (on_delete=PROTECT bảo toàn lịch sử giao dịch).', on_delete=django.db.models.deletion.PROTECT, related_name='orders', to=settings.AUTH_USER_MODEL, verbose_name='Khách hàng')),
            ],
            options={
                'verbose_name': 'Đơn hàng',
                'verbose_name_plural': 'Đơn hàng',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='OrderItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name_snapshot', models.CharField(help_text='Snapshot tên sản phẩm tại thời điểm đặt hàng (tối đa 150 ký tự).', max_length=150, verbose_name='Tên sản phẩm (snapshot)')),
                ('variant_snapshot', models.CharField(help_text='Snapshot tổ hợp thuộc tính biến thể (ví dụ: Màu: Đen, Dung lượng: 128GB; tối đa 255 ký tự).', max_length=255, verbose_name='Thuộc tính biến thể (snapshot)')),
                ('sku_snapshot', models.CharField(help_text='Snapshot mã SKU biến thể tại thời điểm đặt hàng (tối đa 60 ký tự).', max_length=60, verbose_name='Mã SKU (snapshot)')),
                ('quantity', models.PositiveIntegerField(help_text='Số lượng đặt mua (> 0).', verbose_name='Số lượng')),
                ('unit_price', models.DecimalField(decimal_places=2, help_text='Snapshot đơn giá niêm yết/bán tại thời điểm đặt hàng (>= 0).', max_digits=14, verbose_name='Đơn giá (snapshot)')),
                ('discount_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Số tiền giảm giá phân bổ cho dòng này (>= 0, mặc định 0.00).', max_digits=14, verbose_name='Tiền giảm giá (snapshot)')),
                ('line_total', models.DecimalField(decimal_places=2, help_text='Tổng tiền thanh toán của dòng sản phẩm sau khi trừ giảm giá (>= 0).', max_digits=14, verbose_name='Thành tiền dòng')),
                ('order', models.ForeignKey(help_text='Đơn hàng cha chứa dòng sản phẩm này.', on_delete=django.db.models.deletion.CASCADE, related_name='items', to='orders.order', verbose_name='Đơn hàng')),
                ('variant', models.ForeignKey(help_text='Biến thể sản phẩm được mua (on_delete=PROTECT chống xóa sản phẩm đã bán).', on_delete=django.db.models.deletion.PROTECT, related_name='order_items', to='catalog.productvariant', verbose_name='Biến thể sản phẩm')),
            ],
            options={
                'verbose_name': 'Dòng đơn hàng',
                'verbose_name_plural': 'Dòng đơn hàng',
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='OrderStatusHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_status', models.CharField(blank=True, help_text='Trạng thái trước khi chuyển (đối với sự kiện tạo đơn ban đầu, giá trị là chuỗi rỗng biểu thị không có trạng thái trước đó).', max_length=30, verbose_name='Trạng thái cũ')),
                ('new_status', models.CharField(help_text='Trạng thái sau khi chuyển (NOT NULL, tối đa 30 ký tự).', max_length=30, verbose_name='Trạng thái mới')),
                ('reason', models.TextField(blank=True, help_text='Lý do hoặc ghi chú giải trình cho việc chuyển trạng thái (tùy chọn).', null=True, verbose_name='Lý do thay đổi')),
                ('changed_at', models.DateTimeField(default=django.utils.timezone.now, help_text='Thời điểm ghi nhận sự kiện chuyển trạng thái.', verbose_name='Thời điểm thay đổi')),
                ('changed_by', models.ForeignKey(blank=True, help_text='Tài khoản nhân viên/quản trị viên thực hiện chuyển trạng thái (NULL nếu do hệ thống tự động đổi).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_status_history_entries', to=settings.AUTH_USER_MODEL, verbose_name='Người thực hiện')),
                ('order', models.ForeignKey(help_text='Đơn hàng phát sinh sự kiện chuyển trạng thái.', on_delete=django.db.models.deletion.CASCADE, related_name='status_history', to='orders.order', verbose_name='Đơn hàng')),
            ],
            options={
                'verbose_name': 'Lịch sử trạng thái đơn hàng',
                'verbose_name_plural': 'Lịch sử trạng thái đơn hàng',
                'ordering': ['changed_at', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='order',
            constraint=models.CheckConstraint(condition=models.Q(('subtotal__gte', 0)), name='order_subtotal_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='order',
            constraint=models.CheckConstraint(condition=models.Q(('discount_total__gte', 0)), name='order_discount_total_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='order',
            constraint=models.CheckConstraint(condition=models.Q(('grand_total__gte', 0)), name='order_grand_total_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='order_item_quantity_gt_0'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.CheckConstraint(condition=models.Q(('unit_price__gte', 0)), name='order_item_unit_price_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.CheckConstraint(condition=models.Q(('discount_amount__gte', 0)), name='order_item_discount_amount_gte_0'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.CheckConstraint(condition=models.Q(('line_total__gte', 0)), name='order_item_line_total_gte_0'),
        ),
    ]
