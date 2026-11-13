from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Tối đa 100 ký tự. Phải là duy nhất sau khi chuẩn hóa khoảng trắng và chữ hoa/thường.', max_length=100, unique=True, verbose_name='Tên danh mục')),
                ('description', models.TextField(blank=True, null=True, verbose_name='Mô tả danh mục')),
                ('is_visible', models.BooleanField(default=True, help_text='Mặc định True (1). Nếu False (0), danh mục bị ẩn khỏi khách hàng nhưng vẫn hiển thị với nhân viên quản lý.', verbose_name='Hiển thị danh mục')),
            ],
            options={
                'verbose_name': 'Danh mục sản phẩm',
                'verbose_name_plural': 'Danh mục sản phẩm',
                'db_table': 'catalog_category',
                'ordering': ['id'],
            },
        ),
    ]
