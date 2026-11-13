import re
from django import forms
from django.core.exceptions import ValidationError
from inventory.models import Inventory

MAX_INTEGER_LIMIT = 2147483647


class StrictNonNegativeIntegerField(forms.IntegerField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str):
            val_str = value.strip()
            if not (val_str.isascii() and re.fullmatch(r'(?:0|[1-9][0-9]*)', val_str)):
                raise ValidationError(
                    self.error_messages.get('invalid', 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'),
                    code='invalid',
                )
        elif isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(
                self.error_messages.get('invalid', 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'),
                code='invalid',
            )
        return super().to_python(value)


class InventoryThresholdForm(forms.ModelForm):
    min_threshold = forms.IntegerField(
        label='Ngưỡng cảnh báo tối thiểu',
        min_value=0,
        max_value=MAX_INTEGER_LIMIT,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-control',
            'min': '0',
            'step': '1',
            'placeholder': 'Nhập số nguyên >= 0',
            'autocomplete': 'off',
            'id': 'id_min_threshold',
        }),
        error_messages={
            'required': 'Vui lòng nhập ngưỡng cảnh báo tồn kho tối thiểu.',
            'invalid': 'Ngưỡng cảnh báo phải là một số nguyên hợp lệ.',
            'min_value': 'Ngưỡng cảnh báo không được là số âm (phải >= 0).',
            'max_value': f'Ngưỡng cảnh báo không được vượt quá {MAX_INTEGER_LIMIT:,}.',
        },
    )

    class Meta:
        model = Inventory
        fields = ['min_threshold']

    def clean_min_threshold(self):
        val = self.cleaned_data.get('min_threshold')
        if val is None:
            raise ValidationError('Vui lòng nhập số nguyên hợp lệ cho ngưỡng cảnh báo.')
        if val < 0:
            raise ValidationError('Ngưỡng cảnh báo không được là số âm (phải >= 0).')
        if val > MAX_INTEGER_LIMIT:
            raise ValidationError(f'Ngưỡng cảnh báo không được vượt quá {MAX_INTEGER_LIMIT:,}.')
        return val

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save(update_fields=['min_threshold', 'updated_at'])
        return instance


class PurchaseReceivingForm(forms.Form):
    quantity = forms.IntegerField(
        label='Số lượng nhập',
        min_value=1,
        max_value=MAX_INTEGER_LIMIT,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-control',
            'min': '1',
            'max': str(MAX_INTEGER_LIMIT),
            'step': '1',
            'placeholder': 'Nhập số lượng hàng thực tế cần thêm...',
            'autocomplete': 'off',
            'id': 'id_quantity',
        }),
        error_messages={
            'required': 'Vui lòng nhập số lượng hàng cần nhập kho.',
            'invalid': 'Số lượng nhập phải là một số nguyên hợp lệ.',
            'min_value': 'Số lượng nhập phải là số nguyên dương lớn hơn 0.',
            'max_value': f'Số lượng nhập không được vượt quá {MAX_INTEGER_LIMIT:,}.',
        },
    )
    request_token = forms.CharField(
        widget=forms.HiddenInput(attrs={'id': 'id_request_token'}),
        required=True,
        error_messages={
            'required': 'Mã xác thực phiên làm việc bị thiếu. Vui lòng tải lại trang.',
        },
    )

    def clean_quantity(self):
        val = self.cleaned_data.get('quantity')
        if val is None:
            raise ValidationError('Vui lòng nhập số nguyên hợp lệ cho số lượng nhập.')
        if val <= 0:
            raise ValidationError('Số lượng nhập phải là số nguyên dương lớn hơn 0.')
        if val > MAX_INTEGER_LIMIT:
            raise ValidationError(f'Số lượng nhập không được vượt quá {MAX_INTEGER_LIMIT:,}.')
        return val


class StocktakeAdjustmentForm(forms.Form):
    counted_quantity = StrictNonNegativeIntegerField(
        label='Số lượng kiểm kê thực tế',
        min_value=0,
        max_value=MAX_INTEGER_LIMIT,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-control',
            'min': '0',
            'max': str(MAX_INTEGER_LIMIT),
            'step': '1',
            'placeholder': 'Nhập số lượng hàng đếm được thực tế trong kho...',
            'autocomplete': 'off',
            'id': 'id_counted_quantity',
        }),
        error_messages={
            'required': 'Vui lòng nhập số lượng kiểm kê thực tế.',
            'invalid': 'Số lượng kiểm kê phải là một số nguyên hợp lệ.',
            'min_value': 'Số lượng kiểm kê không được là số âm (phải >= 0).',
            'max_value': f'Số lượng kiểm kê không được vượt quá {MAX_INTEGER_LIMIT:,}.',
        },
    )
    reason = forms.CharField(
        label='Lý do điều chỉnh',
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-control',
            'rows': '3',
            'placeholder': 'Ghi rõ nguyên nhân chênh lệch tồn kho (ví dụ: phát hiện hư hỏng, kiểm đếm thực tế định kỳ, thất thoát sau kiểm kho)...',
            'id': 'id_reason',
        }),
        error_messages={
            'required': 'Vui lòng nhập lý do điều chỉnh tồn kho.',
        },
    )
    request_token = forms.CharField(
        widget=forms.HiddenInput(attrs={'id': 'id_request_token'}),
        required=True,
        error_messages={
            'required': 'Mã xác thực phiên làm việc bị thiếu. Vui lòng tải lại trang.',
        },
    )

    def clean_counted_quantity(self):
        val = self.cleaned_data.get('counted_quantity')
        if val is None or isinstance(val, bool) or not isinstance(val, int):
            raise ValidationError('Vui lòng nhập số nguyên hợp lệ cho số lượng kiểm kê.')
        if val < 0:
            raise ValidationError('Số lượng kiểm kê không được là số âm (phải >= 0).')
        if val > MAX_INTEGER_LIMIT:
            raise ValidationError(f'Số lượng kiểm kê không được vượt quá {MAX_INTEGER_LIMIT:,}.')
        return val

    def clean_reason(self):
        val = self.cleaned_data.get('reason')
        if not val or not val.strip():
            raise ValidationError('Lý do điều chỉnh không được để trống hoặc chỉ chứa khoảng trắng.')
        return val.strip()
