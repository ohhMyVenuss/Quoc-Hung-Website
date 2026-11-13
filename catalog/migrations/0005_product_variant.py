import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_attribute_and_attribute_value'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductVariant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sku', models.CharField(help_text='Mã SKU duy nhất toàn hệ thống (tối đa 60 ký tự).', max_length=60, unique=True, verbose_name='Mã SKU')),
                ('price', models.DecimalField(decimal_places=2, help_text='Giá bán hiện tại của biến thể (không âm).', max_digits=14, verbose_name='Giá bán')),
                ('original_price', models.DecimalField(blank=True, decimal_places=2, help_text='Giá gốc/niêm yết ban đầu (tùy chọn, nếu có phải >= giá bán).', max_digits=14, null=True, verbose_name='Giá gốc')),
                ('combination_key', models.CharField(blank=True, default='', editable=False, help_text='Khóa tổ hợp sắp xếp theo ID giá trị thuộc tính (tự động tạo).', max_length=255, verbose_name='Khóa tổ hợp thuộc tính')),
                ('is_active', models.BooleanField(default=True, help_text='Mặc định True (1). Nếu False (0), biến thể tạm ngưng kinh doanh.', verbose_name='Kích hoạt kinh doanh')),
                ('product', models.ForeignKey(help_text='Sản phẩm cơ sở định nghĩa biến thể này.', on_delete=django.db.models.deletion.CASCADE, related_name='variants', to='catalog.product', verbose_name='Sản phẩm cơ sở')),
            ],
            options={
                'verbose_name': 'Biến thể sản phẩm',
                'verbose_name_plural': 'Biến thể sản phẩm',
                'db_table': 'catalog_productvariant',
                'ordering': ['product', 'id'],
            },
        ),
        migrations.CreateModel(
            name='VariantAttributeValue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('attribute', models.ForeignKey(help_text='Thuộc tính định nghĩa (Màu sắc, Dung lượng...).', on_delete=django.db.models.deletion.PROTECT, related_name='variant_links', to='catalog.attribute', verbose_name='Thuộc tính')),
                ('attribute_value', models.ForeignKey(help_text='Giá trị thuộc tính cụ thể gắn với biến thể.', on_delete=django.db.models.deletion.PROTECT, related_name='variant_value_links', to='catalog.attributevalue', verbose_name='Giá trị thuộc tính')),
                ('variant', models.ForeignKey(help_text='Biến thể sở hữu giá trị thuộc tính này.', on_delete=django.db.models.deletion.CASCADE, related_name='attribute_values', to='catalog.productvariant', verbose_name='Biến thể')),
            ],
            options={
                'verbose_name': 'Giá trị thuộc tính của biến thể',
                'verbose_name_plural': 'Giá trị thuộc tính của biến thể',
                'db_table': 'catalog_variantattributevalue',
                'ordering': ['variant', 'attribute'],
            },
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.UniqueConstraint(fields=('product', 'combination_key'), name='unique_combination_per_product'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('price__gte', 0)), name='check_variant_price_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('original_price__isnull', True), ('original_price__gte', models.F('price')), _connector='OR'), name='check_variant_original_price_gte_price'),
        ),
        migrations.AddConstraint(
            model_name='variantattributevalue',
            constraint=models.UniqueConstraint(fields=('variant', 'attribute'), name='unique_attribute_per_variant'),
        ),
    ]
