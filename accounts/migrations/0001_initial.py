import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone', models.CharField(blank=True, max_length=15, null=True, verbose_name='Số điện thoại')),
                ('default_address', models.TextField(blank=True, null=True, verbose_name='Địa chỉ mặc định')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='customer_profile', to=settings.AUTH_USER_MODEL, verbose_name='Tài khoản người dùng')),
            ],
            options={
                'verbose_name': 'Hồ sơ khách hàng',
                'verbose_name_plural': 'Hồ sơ khách hàng',
            },
        ),
    ]
