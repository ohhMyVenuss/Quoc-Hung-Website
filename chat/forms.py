from django import forms

from .services import MAX_MESSAGE_LENGTH


class ChatContentForm(forms.Form):

    content = forms.CharField(
        label='Nội dung',
        max_length=MAX_MESSAGE_LENGTH,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Nhập câu hỏi của bạn...',
            'maxlength': MAX_MESSAGE_LENGTH,
        }),
        error_messages={
            'required': 'Vui lòng nhập nội dung tin nhắn.',
            'max_length': f'Tin nhắn tối đa {MAX_MESSAGE_LENGTH} ký tự.',
        },
    )
