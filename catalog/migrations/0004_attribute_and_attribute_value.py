import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0003_productimage'),
    ]

    operations = [
        migrations.CreateModel(
            name='Attribute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Tối đa 80 ký tự. Tên thuộc tính duy nhất trong hệ thống.', max_length=80, unique=True, verbose_name='Tên thuộc tính')),
            ],
            options={
                'verbose_name': 'Thuộc tính sản phẩm',
                'verbose_name_plural': 'Thuộc tính sản phẩm',
                'db_table': 'catalog_attribute',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='AttributeValue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.CharField(help_text='Tối đa 80 ký tự. Duy nhất trong phạm vi thuộc tính cha.', max_length=80, verbose_name='Giá trị thuộc tính')),
                ('attribute', models.ForeignKey(help_text='Thuộc tính cha định nghĩa giá trị này.', on_delete=django.db.models.deletion.PROTECT, related_name='values', to='catalog.attribute', verbose_name='Thuộc tính cha')),
            ],
            options={
                'verbose_name': 'Giá trị thuộc tính',
                'verbose_name_plural': 'Giá trị thuộc tính',
                'db_table': 'catalog_attributevalue',
                'ordering': ['attribute', 'id'],
                'constraints': [models.UniqueConstraint(fields=('attribute', 'value'), name='unique_attribute_value_per_attribute')],
            },
        ),
    ]
