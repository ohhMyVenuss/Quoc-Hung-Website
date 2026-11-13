from django.conf import settings
from django.db import models
from django.db.models import Q


class ChatFAQ(models.Model):

    question = models.TextField('Câu hỏi')
    answer = models.TextField('Câu trả lời')
    keywords = models.CharField(
        'Từ khóa', max_length=255, null=True, blank=True,
        help_text='Các từ khóa cách nhau bằng dấu phẩy. Ví dụ: bảo hành, đổi trả',
    )
    is_active = models.BooleanField('Đang sử dụng', default=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Câu hỏi thường gặp'
        verbose_name_plural = 'Câu hỏi thường gặp'

    def __str__(self):
        return self.question[:60]


class ChatConversation(models.Model):

    class Status(models.TextChoices):
        BOT = 'BOT', 'Trả lời tự động'
        WAITING_STAFF = 'WAITING_STAFF', 'Chờ nhân viên'
        STAFF = 'STAFF', 'Nhân viên đang hỗ trợ'
        CLOSED = 'CLOSED', 'Đã kết thúc'

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='chat_conversations',
    )
    guest_session = models.CharField(max_length=64, null=True, blank=True)
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='assigned_chat_conversations',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOT)
    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at', '-id']
        verbose_name = 'Hội thoại chat'
        verbose_name_plural = 'Hội thoại chat'
        constraints = [
            models.CheckConstraint(
                condition=Q(customer__isnull=False)
                | (Q(guest_session__isnull=False) & ~Q(guest_session='')),
                name='chat_conversation_has_customer_or_guest',
            ),
        ]

    def __str__(self):
        return f'Hội thoại #{self.pk} ({self.status})'

    @property
    def customer_label(self):
        if self.customer_id:
            return self.customer.get_full_name() or self.customer.get_username()
        return 'Khách vãng lai'

    @property
    def accepts_staff_reply(self):
        return self.status in (self.Status.WAITING_STAFF, self.Status.STAFF)


class ChatMessage(models.Model):

    class SenderType(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Khách hàng'
        BOT = 'BOT', 'Trợ lý tự động'
        STAFF = 'STAFF', 'Nhân viên'

    conversation = models.ForeignKey(
        ChatConversation, on_delete=models.CASCADE, related_name='messages',
    )
    sender_type = models.CharField(max_length=10, choices=SenderType.choices)
    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='chat_messages',
    )
    faq = models.ForeignKey(
        ChatFAQ, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='messages',
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        verbose_name = 'Tin nhắn chat'
        verbose_name_plural = 'Tin nhắn chat'
        constraints = [
            models.CheckConstraint(
                condition=~Q(sender_type='STAFF') | Q(sender_user__isnull=False),
                name='chat_message_staff_has_sender',
            ),
        ]

    def __str__(self):
        return f'{self.sender_type}: {self.content[:40]}'
