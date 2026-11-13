from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import ROLE_SALES_STAFF, is_customer, is_sales_staff, role_required

from . import services
from .forms import ChatContentForm
from .models import ChatConversation, ChatFAQ
from .services import ChatError

Status = ChatConversation.Status


def _customer_side_guard(request):
    user = request.user
    if not user.is_authenticated or is_customer(user):
        return None
    if is_sales_staff(user):
        return redirect('chat:staff_inbox')
    raise PermissionDenied('Tài khoản của bạn không sử dụng chức năng chat với khách.')


def chat_view(request):
    blocked = _customer_side_guard(request)
    if blocked:
        return blocked
    conversation = services.get_open_conversation(request)
    context = {
        'conversation': conversation,
        'chat_messages': conversation.messages.select_related('sender_user') if conversation else [],
        'faqs': ChatFAQ.objects.filter(is_active=True).order_by('id'),
        'show_faqs': conversation is None or conversation.status == Status.BOT,
        'form': ChatContentForm(),
        'active_nav': 'chat',
    }
    return render(request, 'chat/chat.html', context)


@require_POST
def chat_ask_faq_view(request, faq_id):
    blocked = _customer_side_guard(request)
    if blocked:
        return blocked
    faq = get_object_or_404(ChatFAQ, pk=faq_id, is_active=True)
    conversation = services.get_or_start_conversation(request)
    try:
        services.customer_ask_faq(conversation, faq)
    except ChatError as error:
        messages.error(request, str(error))
    return redirect('chat:chat')


@require_POST
def chat_send_view(request):
    blocked = _customer_side_guard(request)
    if blocked:
        return blocked
    form = ChatContentForm(request.POST)
    if not form.is_valid():
        for error in form.errors.get('content', []):
            messages.error(request, error)
        return redirect('chat:chat')
    conversation = services.get_or_start_conversation(request)
    try:
        services.customer_send_text(conversation, form.cleaned_data['content'])
    except ChatError as error:
        messages.error(request, str(error))
    return redirect('chat:chat')


@require_POST
def chat_close_view(request):
    blocked = _customer_side_guard(request)
    if blocked:
        return blocked
    conversation = services.get_open_conversation(request)
    if conversation is None:
        messages.info(request, 'Hiện không có cuộc trò chuyện nào đang mở.')
    else:
        services.close_conversation(conversation.pk)
        messages.success(request, 'Đã kết thúc cuộc trò chuyện.')
    return redirect('chat:chat')


@login_required
@role_required(ROLE_SALES_STAFF)
def staff_inbox_view(request):
    valid_statuses = {value for value, _label in Status.choices}
    status = request.GET.get('status', Status.WAITING_STAFF)
    if status not in valid_statuses:
        status = Status.WAITING_STAFF

    queryset = ChatConversation.objects.filter(status=status).select_related('customer', 'assigned_staff')
    if status == Status.WAITING_STAFF:
        queryset = queryset.order_by('started_at', 'id')
    else:
        queryset = queryset.order_by('-started_at', '-id')

    rows = []
    for conversation in queryset[:100]:
        rows.append({
            'conversation': conversation,
            'last_message': conversation.messages.order_by('-created_at', '-id').first(),
        })
    context = {
        'rows': rows,
        'current_status': status,
        'status_tabs': Status.choices,
        'active_nav': 'chat_staff',
    }
    return render(request, 'chat/staff_inbox.html', context)


@login_required
@role_required(ROLE_SALES_STAFF)
def staff_conversation_view(request, conversation_id):
    conversation = get_object_or_404(
        ChatConversation.objects.select_related('customer', 'assigned_staff'),
        pk=conversation_id,
    )
    if request.method == 'POST':
        form = ChatContentForm(request.POST)
        if form.is_valid():
            try:
                services.staff_reply(conversation.pk, request.user, form.cleaned_data['content'])
            except ChatError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, 'Đã gửi phản hồi cho khách.')
                return redirect('chat:staff_conversation', conversation_id=conversation.pk)
            conversation.refresh_from_db()
    else:
        form = ChatContentForm()
    context = {
        'conversation': conversation,
        'chat_messages': conversation.messages.select_related('sender_user'),
        'form': form,
        'active_nav': 'chat_staff',
    }
    return render(request, 'chat/staff_conversation.html', context)


@login_required
@role_required(ROLE_SALES_STAFF)
@require_POST
def staff_close_view(request, conversation_id):
    conversation = get_object_or_404(ChatConversation, pk=conversation_id)
    if services.close_conversation(conversation.pk):
        messages.success(request, 'Đã kết thúc hội thoại.')
    else:
        messages.info(request, 'Hội thoại này đã được kết thúc trước đó.')
    return redirect('chat:staff_inbox')
