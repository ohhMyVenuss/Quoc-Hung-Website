from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from catalog.models import Product
from coupons.models import Coupon, CouponProduct, DiscountType
from coupons.services import execute_sqlite_safe_save, update_coupon_admin_fields


class CouponForm(forms.ModelForm):
    products = forms.ModelMultipleChoiceField(
        queryset=Product.objects.all().select_related('category').order_by('category__name', 'name'),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'qhm-product-checkbox'}),
        label="Danh sách sản phẩm áp dụng",
        help_text="Chỉ áp dụng khi chọn phạm vi sản phẩm chỉ định. Yêu cầu chọn ít nhất 1 sản phẩm."
    )

    class Meta:
        model = Coupon
        fields = [
            'code',
            'discount_type',
            'value',
            'max_discount',
            'min_order_amount',
            'max_uses',
            'starts_at',
            'ends_at',
            'is_active',
            'applies_to_all',
        ]
        widgets = {
            'code': forms.TextInput(attrs={
                'class': 'qhm-form-input',
                'placeholder': 'VD: IPHONE16VIP',
                'maxlength': '40',
                'autocomplete': 'off',
                'style': 'text-transform: uppercase;',
            }),
            'discount_type': forms.RadioSelect(attrs={
                'class': 'qhm-radio-group',
            }),
            'value': forms.NumberInput(attrs={
                'class': 'qhm-form-input',
                'placeholder': 'VD: 10 hoặc 500000',
                'step': 'any',
                'min': '0',
            }),
            'max_discount': forms.NumberInput(attrs={
                'class': 'qhm-form-input',
                'placeholder': 'Bỏ trống nếu không giới hạn trần giảm tối đa',
                'step': 'any',
                'min': '0',
            }),
            'min_order_amount': forms.NumberInput(attrs={
                'class': 'qhm-form-input',
                'placeholder': '0',
                'step': 'any',
                'min': '0',
            }),
            'max_uses': forms.NumberInput(attrs={
                'class': 'qhm-form-input',
                'placeholder': 'VD: 500',
                'min': '1',
            }),
            'starts_at': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M',
                attrs={
                    'class': 'qhm-form-input',
                    'type': 'datetime-local',
                }
            ),
            'ends_at': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M',
                attrs={
                    'class': 'qhm-form-input',
                    'type': 'datetime-local',
                }
            ),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'qhm-form-checkbox',
            }),
            'applies_to_all': forms.RadioSelect(
                choices=[
                    (True, 'Toàn bộ sản phẩm'),
                    (False, 'Sản phẩm chỉ định'),
                ],
                attrs={'class': 'qhm-radio-scope'}
            ),
        }
        labels = {
            'code': 'Mã Voucher Code *',
            'discount_type': 'Loại hình giảm giá *',
            'value': 'Mức giá trị giảm *',
            'max_discount': 'Trần giảm tối đa khi áp dụng (VND)',
            'min_order_amount': 'Giá trị đơn hàng tối thiểu (VND)',
            'max_uses': 'Tổng số lượt phát hành *',
            'starts_at': 'Thời điểm bắt đầu *',
            'ends_at': 'Thời điểm kết thúc *',
            'is_active': 'Kích hoạt hiệu lực và hiển thị trong danh sách Voucher',
            'applies_to_all': 'Phạm vi sản phẩm áp dụng *',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['min_order_amount'].required = False
        self.fields['applies_to_all'].required = False

        if self.instance and self.instance.pk:
            selected_ids = list(self.instance.coupon_products.values_list('product_id', flat=True))
            self.fields['products'].initial = selected_ids
            if self.instance.starts_at:
                local_start = timezone.localtime(self.instance.starts_at)
                self.initial['starts_at'] = local_start.strftime('%Y-%m-%dT%H:%M')
            if self.instance.ends_at:
                local_end = timezone.localtime(self.instance.ends_at)
                self.initial['ends_at'] = local_end.strftime('%Y-%m-%dT%H:%M')
        else:
            self.fields['min_order_amount'].initial = Decimal('0.00')
            self.fields['is_active'].initial = True
            self.fields['applies_to_all'].initial = True

    def clean_code(self):
        code = self.cleaned_data.get('code', '')
        if code:
            code = code.strip().upper()
        if not code:
            raise ValidationError("Mã Voucher Code không được để trống hoặc chỉ chứa khoảng trắng.")
        if len(code) > 40:
            raise ValidationError("Mã Voucher Code không được vượt quá 40 ký tự.")

        qs = Coupon.objects.filter(code=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(f"Mã voucher '{code}' đã tồn tại trong hệ thống.")
        return code

    def clean_value(self):
        val = self.cleaned_data.get('value')
        if val is None:
            raise ValidationError("Vui lòng nhập mức giá trị giảm.")
        if val <= Decimal('0'):
            raise ValidationError("Mức giá trị giảm phải lớn hơn 0.")
        return val

    def clean_max_discount(self):
        val = self.cleaned_data.get('max_discount')
        if val is not None and val < Decimal('0'):
            raise ValidationError("Trần giảm tối đa không được âm (>= 0 đ hoặc để trống nếu không giới hạn).")
        return val

    def clean_min_order_amount(self):
        val = self.cleaned_data.get('min_order_amount')
        if val is None or val == '':
            return Decimal('0.00')
        if val < Decimal('0'):
            raise ValidationError("Giá trị đơn hàng tối thiểu không được âm (>= 0 đ).")
        return val

    def clean_max_uses(self):
        max_uses = self.cleaned_data.get('max_uses')
        if max_uses is None:
            raise ValidationError("Vui lòng nhập tổng số lượt phát hành.")
        if max_uses <= 0:
            raise ValidationError("Tổng số lượt phát hành phải lớn hơn 0.")

        if self.instance and self.instance.pk:
            current_total = (self.instance.used_count or 0) + (self.instance.reserved_count or 0)
            if max_uses < current_total:
                raise ValidationError(
                    f"Tổng lượt phát hành ({max_uses}) không thể nhỏ hơn tổng số lượt đã dùng và đang giữ hiện tại ({current_total})."
                )
        return max_uses

    def clean_applies_to_all(self):
        val = self.cleaned_data.get('applies_to_all')
        if isinstance(val, str):
            return val.lower() in ('true', '1')
        if val is None:
            return True
        return bool(val)

    def clean(self):
        cleaned_data = super().clean()
        discount_type = cleaned_data.get('discount_type')
        value = cleaned_data.get('value')
        starts_at = cleaned_data.get('starts_at')
        ends_at = cleaned_data.get('ends_at')
        applies_to_all = cleaned_data.get('applies_to_all')
        products = cleaned_data.get('products')

        if discount_type == DiscountType.PERCENT and value is not None:
            if value <= Decimal('0') or value > Decimal('100'):
                self.add_error('value', "Giá trị giảm theo phần trăm phải lớn hơn 0% và không vượt quá 100%.")

        if starts_at and ends_at:
            if timezone.is_naive(starts_at):
                starts_at = timezone.make_aware(starts_at)
                cleaned_data['starts_at'] = starts_at
            if timezone.is_naive(ends_at):
                ends_at = timezone.make_aware(ends_at)
                cleaned_data['ends_at'] = ends_at

            if ends_at <= starts_at:
                self.add_error('ends_at', "Thời điểm kết thúc phải sau thời điểm bắt đầu hiệu lực.")

        if applies_to_all is False:
            if not products or len(products) == 0:
                self.add_error('products', "Vui lòng chọn ít nhất 1 sản phẩm áp dụng khi chọn phạm vi sản phẩm chỉ định.")

        return cleaned_data

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)

        def _do_save():
            if self.instance and self.instance.pk:
                cleaned = self.cleaned_data
                target_fields = {
                    'code': cleaned.get('code', self.instance.code),
                    'discount_type': cleaned.get('discount_type', self.instance.discount_type),
                    'value': cleaned.get('value', self.instance.value),
                    'max_discount': cleaned.get('max_discount', self.instance.max_discount),
                    'min_order_amount': cleaned.get('min_order_amount', self.instance.min_order_amount),
                    'max_uses': cleaned.get('max_uses', self.instance.max_uses),
                    'starts_at': cleaned.get('starts_at', self.instance.starts_at),
                    'ends_at': cleaned.get('ends_at', self.instance.ends_at),
                    'is_active': cleaned.get('is_active', self.instance.is_active),
                    'applies_to_all': cleaned.get('applies_to_all', self.instance.applies_to_all),
                }
                selected_products = self.cleaned_data.get('products') or []
                updated = update_coupon_admin_fields(self.instance.pk, target_fields, selected_products)
                self.instance = updated
                return updated
            else:
                coupon = super(CouponForm, self).save(commit=False)
                coupon.code = coupon.code.strip().upper()
                coupon.used_count = 0
                coupon.reserved_count = 0
                coupon.save()

                applies_to_all = self.cleaned_data.get('applies_to_all')
                selected_products = self.cleaned_data.get('products') or []
                if not applies_to_all and selected_products:
                    mappings = [
                        CouponProduct(coupon=coupon, product_id=p.id)
                        for p in selected_products
                    ]
                    CouponProduct.objects.bulk_create(mappings)
                return coupon

        return execute_sqlite_safe_save(_do_save)
