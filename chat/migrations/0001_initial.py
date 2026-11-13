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
            name='ChatFAQ',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.TextField(verbose_name='Câu hỏi')),
                ('answer', models.TextField(verbose_name='Câu trả lời')),
                ('keywords', models.CharField(blank=True, help_text='Các từ khóa cách nhau bằng dấu phẩy. Ví dụ: bảo hành, đổi trả', max_length=255, null=True, verbose_name='Từ khóa')),
                ('is_active', models.BooleanField(default=True, verbose_name='Đang sử dụng')),
            ],
            options={
                'verbose_name': 'Câu hỏi thường gặp',
                'verbose_name_plural': 'Câu hỏi thường gặp',
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='ChatConversation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('guest_session', models.CharField(blank=True, max_length=64, null=True)),
                ('status', models.CharField(choices=[('BOT', 'Trả lời tự động'), ('WAITING_STAFF', 'Chờ nhân viên'), ('STAFF', 'Nhân viên đang hỗ trợ'), ('CLOSED', 'Đã kết thúc')], default='BOT', max_length=20)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('assigned_staff', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='assigned_chat_conversations', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='chat_conversations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Hội thoại chat',
                'verbose_name_plural': 'Hội thoại chat',
                'ordering': ['-started_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sender_type', models.CharField(choices=[('CUSTOMER', 'Khách hàng'), ('BOT', 'Trợ lý tự động'), ('STAFF', 'Nhân viên')], max_length=10)),
                ('content', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='chat.chatconversation')),
                ('faq', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='messages', to='chat.chatfaq')),
                ('sender_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='chat_messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Tin nhắn chat',
                'verbose_name_plural': 'Tin nhắn chat',
                'ordering': ['created_at', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='chatconversation',
            constraint=models.CheckConstraint(condition=models.Q(('customer__isnull', False), models.Q(('guest_session__isnull', False), models.Q(('guest_session', ''), _negated=True)), _connector='OR'), name='chat_conversation_has_customer_or_guest'),
        ),
        migrations.AddConstraint(
            model_name='chatmessage',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('sender_type', 'STAFF'), _negated=True), ('sender_user__isnull', False), _connector='OR'), name='chat_message_staff_has_sender'),
        ),
    ]
