import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_product'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(help_text='Tập tin ảnh hợp lệ (.jpg, .jpeg, .png, .webp). Tối đa 5MB.', upload_to='products/%Y/%m/', verbose_name='Tập tin hình ảnh')),
                ('sort_order', models.PositiveIntegerField(default=0, help_text='Thứ tự sắp xếp (số nhỏ hơn hiển thị trước, 0 là ảnh đại diện chính).', verbose_name='Thứ tự hiển thị')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='catalog.product', verbose_name='Sản phẩm')),
            ],
            options={
                'verbose_name': 'Ảnh sản phẩm',
                'verbose_name_plural': 'Ảnh sản phẩm',
                'db_table': 'catalog_productimage',
                'ordering': ['sort_order', 'id'],
            },
        ),
    ]
