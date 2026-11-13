import re
import unicodedata

from django.db import transaction
from django.utils import timezone

from .models import ChatConversation, ChatFAQ, ChatMessage

Status = ChatConversation.Status
Sender = ChatMessage.SenderType

MAX_MESSAGE_LENGTH = 1000
MIN_KEYWORD_LENGTH = 2

ESCALATE_NOTICE = (
    'Câu hỏi của bạn đã được ghi nhận và chuyển cho nhân viên bán hàng. '
    'Nhân viên sẽ trả lời tại đây khi sẵn sàng, vui lòng quay lại trang này để xem phản hồi.'
)


class ChatError(Exception):
    pass


CASE_SPECIFIC_PHRASES = [
    'doi so dien thoai',
    'thay doi so dien thoai',
    'sua so dien thoai',
    'cap nhat so dien thoai',
    'doi dia chi',
    'thay doi dia chi',
    'sua dia chi',
    'cap nhat dia chi',
    'doi thong tin giao hang',
    'thay doi thong tin giao hang',
    'doi thong tin nhan hang',
    'thay doi thong tin nhan hang',
    'doi nguoi nhan',
    'thay doi nguoi nhan',
    'doi gio giao',
    'hen gio giao',
    'nhung don van chua',
    'sao don chua',
    'don van chua duoc',
    'don chua duoc xac nhan',
    'chua thay xac nhan',
    'sao chua xac nhan',
    'da chuyen khoan nhung',
    'da thanh toan nhung',
    'chua thay giao hang',
    'sao lau giao',
    'kiem tra giup don',
    'kiem tra don hang giup',
    'tra cuu don hang cua toi',
    'tinh trang don hang cua toi',
]


def normalize_text(text):
    text = (text or '').replace('đ', 'd').replace('Đ', 'D')
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return ' '.join(text.casefold().split())


def split_keywords(raw):
    result = []
    for part in (raw or '').replace('\n', ',').split(','):
        keyword = normalize_text(part)
        if len(keyword) >= MIN_KEYWORD_LENGTH:
            result.append(keyword)
    return result


def keyword_matches_text(keyword, normalized_text):
    if not keyword or not normalized_text:
        return False
    pattern = r'\b' + re.escape(keyword) + r'\b'
    return bool(re.search(pattern, normalized_text))


def is_case_specific_request(normalized_text):
    return any(keyword_matches_text(phrase, normalized_text) for phrase in CASE_SPECIFIC_PHRASES)


def find_matching_faq(text):
    normalized = normalize_text(text)
    if not normalized or is_case_specific_request(normalized):
        return None

    matching_faqs = []
    for faq in ChatFAQ.objects.filter(is_active=True).order_by('id'):
        keywords = split_keywords(faq.keywords)
        if any(keyword_matches_text(kw, normalized) for kw in keywords):
            matching_faqs.append(faq)

    if len(matching_faqs) == 1:
        return matching_faqs[0]

    return None


def get_guest_key(request, create=False):
    key = request.session.session_key
    if not key and create:
        request.session.create()
        key = request.session.session_key
    return key


def get_open_conversation(request):
    user = request.user
    if user.is_authenticated:
        queryset = ChatConversation.objects.filter(customer=user)
    else:
        key = get_guest_key(request)
        if not key:
            return None
        queryset = ChatConversation.objects.filter(customer__isnull=True, guest_session=key)
    return queryset.exclude(status=Status.CLOSED).order_by('-started_at', '-id').first()


def start_conversation(request):
    user = request.user
    if user.is_authenticated:
        return ChatConversation.objects.create(customer=user)
    return ChatConversation.objects.create(guest_session=get_guest_key(request, create=True))


def get_or_start_conversation(request):
    return get_open_conversation(request) or start_conversation(request)


def clean_content(content):
    content = (content or '').strip()
    if not content:
        raise ChatError('Vui lòng nhập nội dung tin nhắn.')
    if len(content) > MAX_MESSAGE_LENGTH:
        raise ChatError(f'Tin nhắn tối đa {MAX_MESSAGE_LENGTH} ký tự.')
    return content


def _add_message(conversation, sender_type, content, sender_user=None, faq=None):
    return ChatMessage.objects.create(
        conversation=conversation, sender_type=sender_type,
        sender_user=sender_user, faq=faq, content=content,
    )


def customer_ask_faq(conversation, faq):
    if not faq.is_active:
        raise ChatError('Câu hỏi này hiện không còn được sử dụng.')
    with transaction.atomic():
        conversation.refresh_from_db()
        if conversation.status != Status.BOT:
            raise ChatError('Cuộc trò chuyện đang chờ hoặc do nhân viên hỗ trợ, vui lòng nhắn trực tiếp.')
        _add_message(conversation, Sender.CUSTOMER, faq.question, sender_user=conversation.customer)
        _add_message(conversation, Sender.BOT, faq.answer, faq=faq)


def customer_send_text(conversation, content):
    content = clean_content(content)
    with transaction.atomic():
        conversation.refresh_from_db()
        if conversation.status == Status.CLOSED:
            raise ChatError('Cuộc trò chuyện này đã kết thúc.')
        _add_message(conversation, Sender.CUSTOMER, content, sender_user=conversation.customer)
        if conversation.status != Status.BOT:
            return
        faq = find_matching_faq(content)
        if faq:
            _add_message(conversation, Sender.BOT, faq.answer, faq=faq)
            return
        changed = ChatConversation.objects.filter(
            pk=conversation.pk, status=Status.BOT,
        ).update(status=Status.WAITING_STAFF)
        if changed:
            _add_message(conversation, Sender.BOT, ESCALATE_NOTICE)


def staff_reply(conversation_id, staff_user, content):
    content = clean_content(content)
    with transaction.atomic():
        ChatConversation.objects.filter(
            pk=conversation_id, status=Status.WAITING_STAFF,
        ).update(status=Status.STAFF, assigned_staff=staff_user)
        conversation = ChatConversation.objects.get(pk=conversation_id)
        if conversation.status != Status.STAFF:
            raise ChatError('Chỉ trả lời được hội thoại đang chờ nhân viên hoặc đang được hỗ trợ.')
        return _add_message(conversation, Sender.STAFF, content, sender_user=staff_user)


def close_conversation(conversation_id):
    return ChatConversation.objects.filter(pk=conversation_id).exclude(
        status=Status.CLOSED,
    ).update(status=Status.CLOSED, closed_at=timezone.now())
