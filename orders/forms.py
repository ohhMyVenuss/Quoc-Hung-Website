from django import forms
from django.core.exceptions import ValidationError

from orders.models import CancellationReasonCode, validate_delivery_phone
from payments.models import PaymentMethod
from payments.qr import is_qr_transfer_available


class CheckoutForm(forms.Form):
    recipient_name = forms.CharField(
        max_length=120,
        required=True,
        label="Họ và tên người nhận",
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'id': 'id_recipient_name',
            'placeholder': 'Nhập họ và tên người nhận hàng',
            'autocomplete': 'name',
            'maxlength': '120',
        }),
        error_messages={
            'required': 'Họ và tên người nhận không được để trống.',
            'max_length': 'Họ và tên người nhận không được vượt quá 120 ký tự.',
        }
    )

    recipient_phone = forms.CharField(
        max_length=20,
        required=True,
        label="Số điện thoại liên hệ",
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'id': 'id_recipient_phone',
            'placeholder': 'Ví dụ: 0982805704',
            'autocomplete': 'tel',
            'maxlength': '20',
        }),
        error_messages={
            'required': 'Số điện thoại người nhận không được để trống.',
        }
    )

    shipping_address = forms.CharField(
        required=True,
        label="Địa chỉ chi tiết giao hàng",
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-textarea',
            'id': 'id_shipping_address',
            'placeholder': 'Số nhà, tên đường, phường/xã, quận/huyện, tỉnh/thành phố...',
            'rows': 3,
        }),
        error_messages={
            'required': 'Địa chỉ giao hàng không được để trống.',
        }
    )

    coupon_code = forms.CharField(
        max_length=40,
        required=False,
        label="Mã giảm giá",
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input qhm-input-coupon',
            'id': 'id_coupon_code',
            'placeholder': 'Nhập mã giảm giá (nếu có)',
            'maxlength': '40',
        }),
    )

    payment_method = forms.ChoiceField(
        choices=[(PaymentMethod.COD, 'Thanh toán khi nhận hàng (COD)')],
        required=True,
        initial=PaymentMethod.COD,
        label="Phương thức thanh toán",
        widget=forms.RadioSelect(attrs={
            'class': 'qhm-radio-input',
            'id': 'id_payment_method',
        }),
        error_messages={
            'required': 'Vui lòng chọn phương thức thanh toán.',
            'invalid_choice': 'Phương thức thanh toán không hợp lệ hoặc hiện chưa sẵn sàng.',
        }
    )

    checkout_token = forms.CharField(
        widget=forms.HiddenInput(attrs={'id': 'id_checkout_token'}),
        required=True,
        error_messages={
            'required': 'Phiên đặt hàng không hợp lệ. Vui lòng tải lại trang.',
        }
    )

    quote_token = forms.CharField(
        widget=forms.HiddenInput(attrs={'id': 'id_quote_token'}),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(PaymentMethod.COD, 'Thanh toán khi nhận hàng (COD)')]
        if is_qr_transfer_available():
            choices.append((PaymentMethod.QR, 'Chuyển khoản ngân hàng qua mã QR'))
        self.fields['payment_method'].choices = choices

    def clean_recipient_name(self):
        name = self.cleaned_data.get('recipient_name', '').strip()
        if not name:
            raise ValidationError('Họ và tên người nhận không được để trống.')
        if len(name) > 120:
            raise ValidationError('Họ và tên người nhận không được vượt quá 120 ký tự.')
        return name

    def clean_recipient_phone(self):
        raw_phone = self.cleaned_data.get('recipient_phone', '')
        try:
            return validate_delivery_phone(raw_phone)
        except ValidationError as e:
            raise ValidationError(e.message)

    def clean_shipping_address(self):
        address = self.cleaned_data.get('shipping_address', '').strip()
        if not address:
            raise ValidationError('Địa chỉ giao hàng không được để trống.')
        return address

    def clean_coupon_code(self):
        code = self.cleaned_data.get('coupon_code', '')
        if code and str(code).strip():
            cleaned = str(code).strip().upper()
            if len(cleaned) > 40:
                raise ValidationError('Mã giảm giá không hợp lệ (vượt quá 40 ký tự).')
            return cleaned
        return ''

    def clean_payment_method(self):
        method = self.cleaned_data.get('payment_method')
        allowed_methods = [choice[0] for choice in self.fields['payment_method'].choices]
        if method not in allowed_methods:
            raise ValidationError('Phương thức thanh toán không hợp lệ hoặc hiện chưa sẵn sàng.')
        return method


class OrderConfirmationForm(forms.Form):
    confirmation_note = forms.CharField(
        label="Ghi chú xác nhận",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập ghi chú hoặc lý do xác nhận đơn hàng (tùy chọn)...',
            'id': 'id_confirmation_note',
            'rows': 3,
        }),
        help_text="Ghi chú nội bộ giải trình việc xác nhận đơn hàng (tùy chọn, tối đa 500 ký tự).",
    )

    def clean_confirmation_note(self):
        val = self.cleaned_data.get('confirmation_note', '')
        return str(val).strip()


class OrderShipForm(forms.Form):
    shipping_note = forms.CharField(
        label="Ghi chú vận chuyển & xuất kho",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập thông tin giao nhận, đơn vị vận chuyển hoặc mã vận đơn (tùy chọn)...',
            'id': 'id_shipping_note',
            'rows': 3,
        }),
        help_text="Ghi chú nội bộ cho việc xuất kho và bắt đầu giao hàng (tùy chọn, tối đa 500 ký tự).",
    )

    def clean_shipping_note(self):
        val = self.cleaned_data.get('shipping_note', '')
        return str(val).strip()


class OrderDeliverForm(forms.Form):
    delivery_note = forms.CharField(
        label="Ghi chú hoàn tất giao hàng",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập ghi chú giao hàng thành công hoặc xác nhận người nhận (tùy chọn)...',
            'id': 'id_delivery_note',
            'rows': 3,
        }),
        help_text="Ghi chú nội bộ cho việc xác nhận giao hàng thành công (tùy chọn, tối đa 500 ký tự).",
    )

    def clean_delivery_note(self):
        val = self.cleaned_data.get('delivery_note', '')
        return str(val).strip()


class OrderCancelForm(forms.Form):
    reason_code = forms.ChoiceField(
        choices=CancellationReasonCode.choices,
        required=True,
        label="Lý do hủy đơn",
        widget=forms.Select(attrs={
            'class': 'qhm-form-input',
            'id': 'id_cancel_reason_code',
        }),
        error_messages={
            'required': 'Vui lòng chọn lý do hủy đơn hàng.',
            'invalid_choice': 'Lý do hủy đơn không hợp lệ.',
        }
    )
    description = forms.CharField(
        required=False,
        max_length=500,
        label="Mô tả chi tiết",
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'id': 'id_cancel_description',
            'placeholder': 'Vui lòng cung cấp thêm chi tiết (bắt buộc nếu chọn "Khác")...',
            'rows': 3,
        }),
        help_text="Bắt buộc nhập khi chọn lý do 'Khác'. Tối đa 500 ký tự.",
        error_messages={
            'max_length': 'Mô tả chi tiết không được vượt quá 500 ký tự.',
        }
    )

    def clean(self):
        cleaned_data = super().clean()
        reason_code = cleaned_data.get('reason_code')
        desc = cleaned_data.get('description', '')
        desc_stripped = str(desc or '').strip()
        cleaned_data['description'] = desc_stripped

        if reason_code == CancellationReasonCode.OTHER:
            if not desc_stripped:
                self.add_error('description', 'Vui lòng nhập mô tả chi tiết khi chọn lý do "Khác".')
        return cleaned_data


