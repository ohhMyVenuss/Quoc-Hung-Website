import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0005_product_variant'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='variant',
            field=models.ForeignKey(blank=True, help_text='Biến thể cụ thể gắn với ảnh này (để trống nếu là ảnh chung của sản phẩm).', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='images', to='catalog.productvariant', verbose_name='Biến thể liên kết'),
        ),
    ]
