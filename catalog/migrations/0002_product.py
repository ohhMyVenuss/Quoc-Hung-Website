import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Tối đa 150 ký tự. Bắt buộc nhập.', max_length=150, verbose_name='Tên sản phẩm')),
                ('brand', models.CharField(blank=True, help_text='Thương hiệu sản phẩm (ví dụ: Apple, Samsung, Xiaomi...). Tùy chọn.', max_length=100, null=True, verbose_name='Thương hiệu')),
                ('description', models.TextField(blank=True, help_text='Mô tả chi tiết đặc điểm và thông số cơ bản của sản phẩm. Tùy chọn.', null=True, verbose_name='Mô tả sản phẩm')),
                ('is_active', models.BooleanField(default=True, help_text='Mặc định True (1). Nếu False (0), sản phẩm tạm ngưng bán và bị ẩn khỏi giao diện khách hàng.', verbose_name='Kích hoạt kinh doanh')),
                ('is_deleted', models.BooleanField(default=False, help_text='Mặc định False (0). Đánh dấu sản phẩm đã bị xóa mềm bởi Quản trị viên.', verbose_name='Đã xóa mềm')),
                ('deleted_at', models.DateTimeField(blank=True, help_text='Thời điểm Quản trị viên thực hiện xóa mềm sản phẩm.', null=True, verbose_name='Thời điểm xóa mềm')),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='products', to='catalog.category', verbose_name='Danh mục sản phẩm')),
            ],
            options={
                'verbose_name': 'Sản phẩm',
                'verbose_name_plural': 'Sản phẩm',
                'db_table': 'catalog_product',
                'ordering': ['-id'],
            },
        ),
    ]
