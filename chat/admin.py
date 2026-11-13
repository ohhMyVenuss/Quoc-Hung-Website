from django.contrib import admin

from .models import ChatConversation, ChatFAQ, ChatMessage


@admin.register(ChatFAQ)
class ChatFAQAdmin(admin.ModelAdmin):
    list_display = ('id', 'question', 'keywords', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('question', 'answer', 'keywords')


class ReadOnlyHistoryAdmin(admin.ModelAdmin):

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    can_delete = False
    fields = ('created_at', 'sender_type', 'sender_user', 'content')
    readonly_fields = ('created_at', 'sender_type', 'sender_user', 'content')

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChatConversation)
class ChatConversationAdmin(ReadOnlyHistoryAdmin):
    list_display = ('id', 'customer', 'guest_label', 'assigned_staff', 'status', 'started_at', 'closed_at')
    list_filter = ('status',)
    fields = ('id', 'customer', 'guest_label', 'assigned_staff', 'status', 'started_at', 'closed_at')
    readonly_fields = ('id', 'customer', 'guest_label', 'assigned_staff', 'status', 'started_at', 'closed_at')
    inlines = [ChatMessageInline]

    @admin.display(description='Khách vãng lai')
    def guest_label(self, obj):
        return 'Khách vãng lai' if obj.guest_session else '—'


@admin.register(ChatMessage)
class ChatMessageAdmin(ReadOnlyHistoryAdmin):
    list_display = ('id', 'conversation', 'sender_type', 'sender_user', 'created_at')
    list_filter = ('sender_type',)
