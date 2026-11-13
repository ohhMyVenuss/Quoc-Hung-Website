from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.db import transaction
from catalog.models import (
    Category, Product, ProductImage, normalize_category_name,
    Attribute, AttributeValue, normalize_attribute_text,
    ProductVariant, VariantAttributeValue, derive_combination_key,
)


class CategoryForm(forms.ModelForm):

    class Meta:
        model = Category
        fields = ['name', 'description', 'is_visible']
        labels = {
            'name': 'Tên danh mục',
            'description': 'Mô tả danh mục',
            'is_visible': 'Hiển thị trên hệ thống',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Nhập tên danh mục (ví dụ: Điện thoại, Máy tính bảng, Phụ kiện...)',
                'maxlength': '100',
                'autocomplete': 'off',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-input',
                'placeholder': 'Mô tả tóm tắt danh mục (tùy chọn)',
                'rows': 3,
            }),
            'is_visible': forms.CheckboxInput(attrs={
                'class': 'form-checkbox',
            }),
        }

    def clean_name(self):
        raw_name = self.cleaned_data.get('name', '')
        normalized = normalize_category_name(raw_name)
        if not normalized:
            raise forms.ValidationError('Tên danh mục không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 100:
            raise forms.ValidationError('Tên danh mục không được vượt quá 100 ký tự.')

        norm_key = normalized.casefold()
        qs = Category.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        for other in qs:
            if normalize_category_name(other.name).casefold() == norm_key:
                raise forms.ValidationError(
                    f"Tên danh mục '{normalized}' đã tồn tại trong hệ thống (trùng lặp sau khi chuẩn hóa)."
                )

        return normalized


class ProductForm(forms.ModelForm):

    class Meta:
        model = Product
        fields = ['category', 'name', 'brand', 'description', 'is_active']
        labels = {
            'category': 'Danh mục sản phẩm',
            'name': 'Tên sản phẩm',
            'brand': 'Thương hiệu',
            'description': 'Mô tả chi tiết',
            'is_active': 'Kích hoạt kinh doanh (Đang bán)',
        }
        widgets = {
            'category': forms.Select(attrs={
                'class': 'form-input',
            }),
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: iPhone 15 Pro Max 256GB',
                'maxlength': '150',
                'autocomplete': 'off',
            }),
            'brand': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: Apple, Samsung, Xiaomi...',
                'maxlength': '100',
                'autocomplete': 'off',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-input',
                'placeholder': 'Mô tả chi tiết sản phẩm, cấu hình tổng quan...',
                'rows': 4,
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.all().order_by('name')
        self.fields['category'].empty_label = "-- Chọn danh mục sản phẩm --"

    def clean_name(self):
        raw_name = self.cleaned_data.get('name', '')
        normalized = " ".join(raw_name.strip().split())
        if not normalized:
            raise forms.ValidationError('Tên sản phẩm không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 150:
            raise forms.ValidationError('Tên sản phẩm không được vượt quá 150 ký tự.')
        return normalized

    def clean_brand(self):
        raw_brand = self.cleaned_data.get('brand', '')
        if raw_brand:
            raw_brand = " ".join(raw_brand.strip().split())
            if len(raw_brand) > 100:
                raise forms.ValidationError('Tên thương hiệu không được vượt quá 100 ký tự.')
        return raw_brand or None


def validate_product_image_file(img_file):

    if not img_file:
        raise forms.ValidationError("Vui lòng chọn tập tin ảnh để tải lên.")

    max_size = 5 * 1024 * 1024
    if hasattr(img_file, 'size') and img_file.size > max_size:
        raise forms.ValidationError(
            f"Dung lượng tập tin quá lớn ({img_file.size / 1024 / 1024:.1f}MB). Tối đa cho phép là 5MB."
        )

    from PIL import Image
    try:
        img = Image.open(img_file)
        img.verify()
        image_format = (img.format or '').upper()
    except Exception:
        raise forms.ValidationError("Tập tin hình ảnh không hợp lệ hoặc bị hỏng.")
    finally:
        if hasattr(img_file, 'seek'):
            img_file.seek(0)

    allowed_formats = ['JPEG', 'PNG', 'WEBP', 'GIF']
    if image_format not in allowed_formats:
        raise forms.ValidationError(
            f"Định dạng ảnh '{image_format}' không được hỗ trợ. Chỉ chấp nhận các định dạng: JPEG, PNG, WEBP, GIF."
        )

    return img_file


class ProductImageForm(forms.ModelForm):

    class Meta:
        model = ProductImage
        fields = ['image', 'variant', 'sort_order']
        labels = {
            'image': 'Tập tin hình ảnh',
            'variant': 'Biến thể liên kết',
            'sort_order': 'Thứ tự hiển thị',
        }
        widgets = {
            'image': forms.FileInput(attrs={
                'class': 'form-input',
                'accept': 'image/jpeg,image/png,image/webp,image/gif',
                'id': 'product-image-file-input',
            }),
            'variant': forms.Select(attrs={
                'class': 'form-input',
                'id': 'product-image-variant-select',
            }),
            'sort_order': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': '0',
                'placeholder': '0',
                'id': 'product-image-sort-order',
            }),
        }

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        if 'variant' in self.fields:
            self.fields['variant'].empty_label = "-- Ảnh chung của sản phẩm --"
            self.fields['variant'].required = False
            if product is not None:
                self.fields['variant'].queryset = product.variants.all().order_by('id')
            else:
                self.fields['variant'].queryset = ProductVariant.objects.all().order_by('id')
            self.fields['variant'].label_from_instance = lambda obj: f"{obj.sku}" + (f" ({obj.attribute_summary})" if obj.attribute_summary != "Mặc định" else "")
        if 'sort_order' in self.fields:
            self.fields['sort_order'].required = False
            self.fields['sort_order'].initial = 0

    def clean_sort_order(self):
        val = self.cleaned_data.get('sort_order')
        return val if val is not None else 0

    def clean_image(self):
        img_file = self.cleaned_data.get('image')
        return validate_product_image_file(img_file)

    def clean(self):
        cleaned_data = super().clean()
        variant = cleaned_data.get('variant')
        prod = self.product or (self.instance.product if hasattr(self.instance, 'product') else None)
        if prod and prod.is_deleted:
            raise forms.ValidationError('Không thể thêm hoặc chỉnh sửa ảnh cho sản phẩm đã bị xóa mềm.')
        if prod and variant and variant.product_id != prod.id:
            self.add_error('variant', 'Biến thể được chọn không thuộc sản phẩm này.')
        return cleaned_data


class ProductImageVariantAssignForm(forms.ModelForm):

    class Meta:
        model = ProductImage
        fields = ['variant']
        labels = {
            'variant': 'Biến thể liên kết',
        }

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        if 'variant' in self.fields:
            self.fields['variant'].empty_label = "-- Ảnh chung của sản phẩm --"
            self.fields['variant'].required = False
            if product is not None:
                self.fields['variant'].queryset = product.variants.all().order_by('id')
            else:
                self.fields['variant'].queryset = ProductVariant.objects.all().order_by('id')
            self.fields['variant'].label_from_instance = lambda obj: f"{obj.sku}" + (f" ({obj.attribute_summary})" if obj.attribute_summary != "Mặc định" else "")
            self.fields['variant'].widget.attrs.update({'class': 'form-input form-input-sm'})

    def clean(self):
        cleaned_data = super().clean()
        variant = cleaned_data.get('variant')
        prod = self.product or (self.instance.product if hasattr(self.instance, 'product') else None)
        if prod and prod.is_deleted:
            raise forms.ValidationError('Không thể chỉnh sửa ảnh của sản phẩm đã bị xóa mềm.')
        if prod and variant and variant.product_id != prod.id:
            self.add_error('variant', 'Biến thể được chọn không thuộc sản phẩm này.')
        return cleaned_data


class ProductImageAdminForm(forms.ModelForm):

    class Meta:
        model = ProductImage
        fields = '__all__'

    def __init__(self, *args, parent_product=None, **kwargs):
        super().__init__(*args, **kwargs)
        if parent_product is not None:
            self.parent_product = parent_product

        target_product_id = None
        if hasattr(self, 'parent_product') and self.parent_product:
            target_product_id = self.parent_product.pk
        elif self.instance and getattr(self.instance, 'product_id', None):
            target_product_id = self.instance.product_id

        if 'product' in self.fields:
            if not self.is_bound:
                self.fields['product'].queryset = Product.objects.filter(is_deleted=False)
            else:
                self.fields['product'].queryset = Product.objects.all()

        if 'variant' in self.fields:
            self.fields['variant'].empty_label = "-- Ảnh chung của sản phẩm --"
            self.fields['variant'].required = False
            self.fields['variant'].label_from_instance = lambda obj: f"{obj.sku}" + (f" ({obj.attribute_summary})" if obj.attribute_summary != "Mặc định" else "")

            if target_product_id:
                self.fields['variant'].queryset = ProductVariant.objects.filter(product_id=target_product_id).order_by('id')
            elif 'product' in self.fields:
                if self.is_bound and self.data.get('product'):
                    try:
                        self.fields['variant'].queryset = ProductVariant.objects.filter(product_id=int(self.data.get('product'))).order_by('id')
                    except (ValueError, TypeError):
                        self.fields['variant'].queryset = ProductVariant.objects.none()
                else:
                    self.fields['variant'].queryset = ProductVariant.objects.none()
            else:
                pass

    def clean_product(self):
        product = self.cleaned_data.get('product')
        if product and product.is_deleted:
            raise forms.ValidationError('Không thể thêm hoặc chuyển ảnh cho sản phẩm đã bị xóa mềm.')
        return product

    def clean_image(self):
        img_file = self.cleaned_data.get('image')
        if img_file and (hasattr(img_file, 'file') or hasattr(img_file, 'chunks')):
            return validate_product_image_file(img_file)
        return img_file

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product') or getattr(self, 'parent_product', None) or (self.instance.product if hasattr(self.instance, 'product') else None)
        variant = cleaned_data.get('variant')

        if product and product.is_deleted:
            msg = 'Không thể thêm hoặc chuyển ảnh cho sản phẩm đã bị xóa mềm.'
            if 'product' in self.fields:
                self.add_error('product', msg)
            else:
                self.add_error(None, msg)

        if product and variant and variant.product_id != product.id:
            msg = 'Biến thể được chọn không thuộc sản phẩm này.'
            if 'variant' in self.fields:
                self.add_error('variant', msg)
            else:
                self.add_error(None, msg)

        return cleaned_data


class AttributeForm(forms.ModelForm):

    class Meta:
        model = Attribute
        fields = ['name']
        labels = {
            'name': 'Tên thuộc tính',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: Màu sắc, Dung lượng, RAM...',
                'maxlength': '80',
                'autocomplete': 'off',
            }),
        }

    def clean_name(self):
        raw_name = self.cleaned_data.get('name', '')
        normalized = normalize_attribute_text(raw_name)
        if not normalized:
            raise forms.ValidationError('Tên thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Tên thuộc tính không được vượt quá 80 ký tự.')

        norm_key = normalized.casefold()
        qs = Attribute.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        for other in qs:
            if normalize_attribute_text(other.name).casefold() == norm_key:
                raise forms.ValidationError(
                    f"Tên thuộc tính '{normalized}' đã tồn tại trong hệ thống (trùng lặp sau khi chuẩn hóa)."
                )

        return normalized


class AttributeValueForm(forms.ModelForm):

    class Meta:
        model = AttributeValue
        fields = ['value']
        labels = {
            'value': 'Giá trị thuộc tính',
        }
        widgets = {
            'value': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: Đen Titanium, 256GB, 8GB...',
                'maxlength': '80',
                'autocomplete': 'off',
            }),
        }

    def __init__(self, *args, attribute=None, **kwargs):
        super().__init__(*args, **kwargs)
        if attribute is not None:
            self.attribute = attribute
        elif self.instance and getattr(self.instance, 'attribute_id', None):
            self.attribute = self.instance.attribute
        else:
            self.attribute = None

    def clean_value(self):
        raw_val = self.cleaned_data.get('value', '')
        normalized = normalize_attribute_text(raw_val)
        if not normalized:
            raise forms.ValidationError('Giá trị thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Giá trị thuộc tính không được vượt quá 80 ký tự.')

        target_attr = self.attribute or (self.instance.attribute if self.instance and getattr(self.instance, 'attribute_id', None) else None)
        if target_attr:
            norm_key = normalized.casefold()
            qs = AttributeValue.objects.filter(attribute=target_attr)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            for other in qs:
                if normalize_attribute_text(other.value).casefold() == norm_key:
                    raise forms.ValidationError(
                        f"Giá trị '{normalized}' đã tồn tại trong thuộc tính '{target_attr.name}'."
                    )

        return normalized

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.attribute:
            instance.attribute = self.attribute
        if commit:
            instance.save()
        return instance


class AttributeValueAdminForm(forms.ModelForm):

    class Meta:
        model = AttributeValue
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and 'attribute' in self.fields:
            self.fields['attribute'].disabled = True

    def clean_attribute(self):
        attr = self.cleaned_data.get('attribute')
        if self.instance and self.instance.pk:
            return self.instance.attribute
        return attr

    def clean_value(self):
        raw_val = self.cleaned_data.get('value', '')
        normalized = normalize_attribute_text(raw_val)
        if not normalized:
            raise forms.ValidationError('Giá trị thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Giá trị thuộc tính không được vượt quá 80 ký tự.')
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        attr = self.instance.attribute if (self.instance and self.instance.pk) else cleaned_data.get('attribute')
        val = cleaned_data.get('value')
        if attr and val:
            norm_key = normalize_attribute_text(val).casefold()
            qs = AttributeValue.objects.filter(attribute=attr)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            for other in qs:
                if normalize_attribute_text(other.value).casefold() == norm_key:
                    self.add_error(
                        'value',
                        f"Giá trị '{val}' đã tồn tại trong thuộc tính '{attr.name}'."
                    )
        return cleaned_data


class AttributeValueInlineForm(forms.ModelForm):

    class Meta:
        model = AttributeValue
        fields = ('value',)

    def clean_value(self):
        raw_val = self.cleaned_data.get('value', '')
        normalized = normalize_attribute_text(raw_val)
        if not normalized:
            raise forms.ValidationError('Giá trị thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Giá trị thuộc tính không được vượt quá 80 ký tự.')
        return normalized


class AttributeValueInlineFormSet(forms.BaseInlineFormSet):

    def clean(self):
        seen_values = {}
        managed_pks = set()
        for form in self.forms:
            if getattr(form, 'instance', None) and form.instance.pk:
                managed_pks.add(form.instance.pk)

        existing_db_keys = {}
        if self.instance and self.instance.pk:
            for ev in AttributeValue.objects.filter(attribute=self.instance).exclude(pk__in=managed_pks):
                k = normalize_attribute_text(ev.value).casefold()
                existing_db_keys[k] = ev.value

        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            if form.cleaned_data.get('DELETE', False):
                continue

            raw_val = form.cleaned_data.get('value')
            if not raw_val:
                continue

            normalized = normalize_attribute_text(raw_val)
            norm_key = normalized.casefold()

            if norm_key in seen_values:
                form.add_error(
                    'value',
                    f"Giá trị '{raw_val}' bị trùng lặp với dòng khác trong cùng thuộc tính."
                )
            else:
                seen_values[norm_key] = raw_val

            if norm_key in existing_db_keys:
                form.add_error(
                    'value',
                    f"Giá trị '{raw_val}' đã tồn tại trong thuộc tính '{self.instance.name}'."
                )

        super().clean()


class ProductVariantForm(forms.ModelForm):

    class Meta:
        model = ProductVariant
        fields = ['sku', 'price', 'original_price', 'is_active']
        labels = {
            'sku': 'Mã SKU',
            'price': 'Giá bán (VNĐ)',
            'original_price': 'Giá gốc / niêm yết (VNĐ)',
            'is_active': 'Kích hoạt kinh doanh (Đang bán)',
        }
        widgets = {
            'sku': forms.TextInput(attrs={
                'class': 'qhm-form-control',
                'placeholder': 'Ví dụ: IP15PM-256-BLK',
                'maxlength': '60',
                'autocomplete': 'off',
                'style': 'text-transform: uppercase;',
            }),
            'price': forms.NumberInput(attrs={
                'class': 'qhm-form-control',
                'placeholder': '0.00',
                'step': '0.01',
                'min': '0',
            }),
            'original_price': forms.NumberInput(attrs={
                'class': 'qhm-form-control',
                'placeholder': '0.00 (tùy chọn)',
                'step': '0.01',
                'min': '0',
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox',
            }),
        }

    def _update_errors(self, errors):
        if isinstance(errors, forms.ValidationError) and hasattr(errors, 'error_dict'):
            new_error_dict = {}
            for field, error_list in errors.error_dict.items():
                if field in self.fields or field == NON_FIELD_ERRORS:
                    new_error_dict[field] = error_list
                else:
                    new_error_dict.setdefault(NON_FIELD_ERRORS, []).extend(error_list)
            errors = forms.ValidationError(new_error_dict)
        super()._update_errors(errors)

    def __init__(self, product, *args, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)

        self.available_attributes = list(Attribute.objects.all().order_by('name'))
        self.attribute_fields = []

        initial_attr_values = {}
        if self.instance and self.instance.pk:
            for link in self.instance.attribute_values.select_related('attribute', 'attribute_value'):
                initial_attr_values[link.attribute_id] = link.attribute_value_id

        for attr in self.available_attributes:
            field_name = f'attr_{attr.id}'
            self.attribute_fields.append((field_name, attr))
            self.fields[field_name] = forms.ModelChoiceField(
                queryset=AttributeValue.objects.filter(attribute=attr).order_by('value'),
                required=False,
                empty_label="-- Không chọn --",
                label=attr.name,
                initial=initial_attr_values.get(attr.id),
                widget=forms.Select(attrs={'class': 'qhm-form-control'})
            )

    def get_attribute_fields(self):
        for field_name, attr in self.attribute_fields:
            yield attr, self[field_name]

    def clean_sku(self):
        raw_sku = self.cleaned_data.get('sku', '')
        normalized = raw_sku.strip().upper()
        if not normalized:
            raise forms.ValidationError('Mã SKU không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 60:
            raise forms.ValidationError('Mã SKU không được vượt quá 60 ký tự.')

        qs = ProductVariant.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        for other in qs:
            if (other.sku or '').strip().upper() == normalized:
                raise forms.ValidationError(f"Mã SKU '{normalized}' đã tồn tại trong hệ thống.")
        return normalized

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is None:
            raise forms.ValidationError('Giá bán không được để trống.')
        if price < 0:
            raise forms.ValidationError('Giá bán không được âm.')
        return price

    def clean_original_price(self):
        original_price = self.cleaned_data.get('original_price')
        if original_price is not None and original_price < 0:
            raise forms.ValidationError('Giá gốc không được âm.')
        return original_price

    def clean(self):
        cleaned_data = super().clean()

        if not self.product:
            raise forms.ValidationError('Thiếu thông tin sản phẩm cơ sở.')
        if self.product.is_deleted:
            raise forms.ValidationError('Không thể tạo hoặc chỉnh sửa biến thể của sản phẩm đã bị xóa mềm.')
        if self.instance and self.instance.pk:
            if self.instance.product_id != self.product.id:
                raise forms.ValidationError('Không được phép chuyển biến thể sang sản phẩm khác.')

        price = cleaned_data.get('price')
        original_price = cleaned_data.get('original_price')
        if price is not None and original_price is not None:
            if original_price < price:
                self.add_error('original_price', 'Giá gốc phải lớn hơn hoặc bằng giá bán.')

        selected_values = []
        for field_name, attr in self.attribute_fields:
            val = cleaned_data.get(field_name)
            if val:
                if val.attribute_id != attr.id:
                    self.add_error(field_name, f"Giá trị '{val.value}' không thuộc về thuộc tính '{attr.name}'.")
                    continue
                selected_values.append(val)

        val_ids = [v.id for v in selected_values]
        derived_key = derive_combination_key(val_ids)
        cleaned_data['derived_combination_key'] = derived_key
        cleaned_data['selected_values'] = selected_values

        siblings = ProductVariant.objects.filter(product_id=self.product.id)
        if self.instance and self.instance.pk:
            siblings = siblings.exclude(pk=self.instance.pk)

        if derived_key == "":
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError('Sản phẩm đã có biến thể mặc định không thuộc tính.')
            if siblings.exclude(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể tạo biến thể không có thuộc tính vì sản phẩm đã có các biến thể theo thuộc tính. '
                    'Không được pha trộn giữa biến thể có thuộc tính và không có thuộc tính trong cùng một sản phẩm.'
                )
        else:
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể tạo biến thể có thuộc tính vì sản phẩm đang sử dụng biến thể mặc định không thuộc tính. '
                    'Vui lòng chuyển đổi hoặc xử lý biến thể hiện có trước khi thêm biến thể theo thuộc tính.'
                )
            if siblings.filter(combination_key=derived_key).exists():
                raise forms.ValidationError(
                    f"Tổ hợp thuộc tính này đã tồn tại cho sản phẩm '{self.product.name}'."
                )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.product = self.product
        instance.sku = self.cleaned_data['sku']
        instance.price = self.cleaned_data['price']
        instance.original_price = self.cleaned_data.get('original_price')
        instance.is_active = self.cleaned_data.get('is_active', True)
        instance.combination_key = self.cleaned_data['derived_combination_key']

        if commit:
            with transaction.atomic():
                siblings = ProductVariant.objects.filter(product_id=self.product.id)
                if instance.pk:
                    siblings = siblings.exclude(pk=instance.pk)

                if instance.combination_key == "":
                    if siblings.exists():
                        raise forms.ValidationError('Xung đột quy tắc không pha trộn khi lưu biến thể không thuộc tính.')
                else:
                    if siblings.filter(combination_key="").exists() or siblings.filter(combination_key=instance.combination_key).exists():
                        raise forms.ValidationError('Xung đột tổ hợp thuộc tính hoặc quy tắc không pha trộn.')

                instance.save()

                if instance.pk:
                    instance.attribute_values.all().delete()

                for val in self.cleaned_data.get('selected_values', []):
                    VariantAttributeValue.objects.create(
                        variant=instance,
                        attribute=val.attribute,
                        attribute_value=val
                    )

        return instance


class ProductVariantAdminForm(forms.ModelForm):

    class Meta:
        model = ProductVariant
        fields = ('product', 'sku', 'price', 'original_price', 'is_active')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'product' in self.fields:
            if not self.instance.pk:
                self.fields['product'].queryset = Product.objects.all().order_by('name')
            else:
                self.fields['product'].disabled = True

    def _update_errors(self, errors):

        if isinstance(errors, forms.ValidationError) and hasattr(errors, 'error_dict'):
            new_error_dict = {}
            for field, error_list in errors.error_dict.items():
                if field in self.fields or field == NON_FIELD_ERRORS:
                    new_error_dict[field] = error_list
                else:
                    new_error_dict.setdefault(NON_FIELD_ERRORS, []).extend(error_list)
            errors = forms.ValidationError(new_error_dict)
        super()._update_errors(errors)

    def clean_product(self):
        product = self.cleaned_data.get('product')
        if product and product.is_deleted:
            raise forms.ValidationError('Không thể tạo hoặc sửa biến thể của sản phẩm đã bị xóa mềm.')
        return product

    def clean_sku(self):
        raw_sku = self.cleaned_data.get('sku', '')
        normalized = raw_sku.strip().upper()
        if not normalized:
            raise forms.ValidationError('Mã SKU không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 60:
            raise forms.ValidationError('Mã SKU không được vượt quá 60 ký tự.')
        qs = ProductVariant.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        for other in qs:
            if (other.sku or '').strip().upper() == normalized:
                raise forms.ValidationError(f"Mã SKU '{normalized}' đã tồn tại trong hệ thống.")
        return normalized

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is None:
            raise forms.ValidationError('Giá bán không được để trống.')
        if price < 0:
            raise forms.ValidationError('Giá bán không được âm.')
        return price

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product') or getattr(self.instance, 'product', None)
        if product and product.is_deleted:
            if 'product' in self.fields:
                self.add_error('product', 'Không thể tạo hoặc sửa biến thể của sản phẩm đã bị xóa mềm.')
            else:
                self.add_error(None, 'Không thể tạo hoặc sửa biến thể của sản phẩm đã bị xóa mềm.')

        price = cleaned_data.get('price')
        orig_price = cleaned_data.get('original_price')
        if price is not None and orig_price is not None and orig_price < price:
            self.add_error('original_price', 'Giá gốc phải lớn hơn hoặc bằng giá bán.')

        if self.data:
            if self.instance and self.instance.pk:
                try:
                    initial_forms_val = int(self.data.get('attribute_values-INITIAL_FORMS', -1))
                    actual_count = self.instance.attribute_values.count()
                    if initial_forms_val != actual_count:
                        return cleaned_data
                except (ValueError, TypeError):
                    return cleaned_data

                expected_pks = set(self.instance.attribute_values.values_list('pk', flat=True))
                try:
                    total_forms = int(self.data.get('attribute_values-TOTAL_FORMS', 0))
                    seen_pks = []
                    for i in range(total_forms):
                        rid_str = self.data.get(f'attribute_values-{i}-id')
                        if rid_str is not None and str(rid_str).strip() != '':
                            if not str(rid_str).strip().isdigit():
                                self.add_error(None, "Mã hàng liên kết thuộc tính không hợp lệ.")
                                return cleaned_data
                            rid = int(str(rid_str).strip())
                            if rid not in expected_pks:
                                self.add_error(None, f"Dữ liệu hàng liên kết thuộc tính (ID: {rid}) không thuộc về biến thể này hoặc bị giả mạo.")
                                return cleaned_data
                            if i >= initial_forms_val:
                                self.add_error(None, f"Dòng thêm mới không được sử dụng lại ID của hàng thuộc tính hiện có (ID: {rid}).")
                                return cleaned_data
                            if rid in seen_pks:
                                self.add_error(None, f"Mã hàng liên kết thuộc tính (ID: {rid}) bị lặp lại trong biểu mẫu.")
                                return cleaned_data
                            seen_pks.append(rid)
                except (ValueError, TypeError):
                    return cleaned_data

            staged_val_ids = []
            for k, v in self.data.items():
                if k.startswith('attribute_values-') and k.endswith('-attribute_value') and v:
                    prefix = k[:-len('-attribute_value')]
                    if self.data.get(f"{prefix}-DELETE") in ('1', 'true', 'True', 'on'):
                        continue
                    try:
                        staged_val_ids.append(int(v))
                    except (ValueError, TypeError):
                        pass
            if staged_val_ids or any(k.startswith('attribute_values-') for k in self.data):
                staged_key = derive_combination_key(staged_val_ids)
                self.instance.combination_key = staged_key

        return cleaned_data


class VariantAttributeValueInlineForm(forms.ModelForm):

    class Meta:
        model = VariantAttributeValue
        fields = ('attribute', 'attribute_value')

    def validate_unique(self):

        pass

    def validate_constraints(self):

        pass

    def clean(self):
        cleaned_data = super().clean()
        attr = cleaned_data.get('attribute')
        val = cleaned_data.get('attribute_value')
        if attr and val:
            if val.attribute_id != attr.id:
                self.add_error(
                    'attribute_value',
                    f"Giá trị '{val.value}' không thuộc về thuộc tính '{attr.name}'."
                )
        return cleaned_data


class VariantAttributeValueInlineFormSet(forms.BaseInlineFormSet):

    def _extract_form_row_id(self, form):
        row_id = None
        if hasattr(form, 'cleaned_data') and form.cleaned_data:
            raw_id = form.cleaned_data.get('id')
            if raw_id:
                if getattr(raw_id, 'pk', None):
                    row_id = raw_id.pk
                elif isinstance(raw_id, int):
                    row_id = raw_id

        if row_id is None and getattr(form, 'instance', None) and form.instance.pk:
            row_id = form.instance.pk

        if row_id is None and form.is_bound:
            pk_val = form[self.model._meta.pk.name].value()
            if pk_val is None and hasattr(self, 'data') and self.data:
                pk_val = self.data.get(f"{form.prefix}-{self.model._meta.pk.name}")
            if pk_val is not None and str(pk_val).strip() != '':
                if str(pk_val).strip().isdigit():
                    row_id = int(str(pk_val).strip())
                else:
                    raise forms.ValidationError('Mã hàng liên kết thuộc tính không hợp lệ.')
        return row_id

    def _validate_management_and_row_identities(self, expected_existing_pks):

        if not self.management_form.is_valid():
            raise forms.ValidationError('Dữ liệu quản lý biểu mẫu thuộc tính không hợp lệ.')

        expected_initial_count = len(expected_existing_pks)
        submitted_initial_forms = self.management_form.cleaned_data.get('INITIAL_FORMS')
        if submitted_initial_forms != expected_initial_count:
            raise forms.ValidationError(
                'Dữ liệu quản lý biểu mẫu (INITIAL_FORMS) không khớp với số lượng thuộc tính hiện có trong cơ sở dữ liệu.'
            )

        seen_all_pks = []
        seen_initial_pks = []
        for i, form in enumerate(self.forms):
            row_id = self._extract_form_row_id(form)
            is_initial = (i < expected_initial_count)

            if is_initial:
                if row_id is None:
                    raise forms.ValidationError('Thiếu thông tin nhận diện dòng thuộc tính ban đầu.')
                if self.instance and self.instance.pk:
                    if row_id not in expected_existing_pks:
                        raise forms.ValidationError(
                            f"Dữ liệu hàng liên kết thuộc tính (ID: {row_id}) không thuộc về biến thể này hoặc bị giả mạo."
                        )
                if row_id in seen_all_pks:
                    raise forms.ValidationError(
                        f"Mã hàng liên kết thuộc tính (ID: {row_id}) bị lặp lại trong biểu mẫu."
                    )
                seen_all_pks.append(row_id)
                seen_initial_pks.append(row_id)
            else:
                if row_id is not None:
                    if self.instance and self.instance.pk and row_id in expected_existing_pks:
                        raise forms.ValidationError(
                            f"Dòng thêm mới không được sử dụng lại ID của hàng thuộc tính hiện có (ID: {row_id})."
                        )
                    if row_id in seen_all_pks:
                        raise forms.ValidationError(
                            f"Mã hàng liên kết thuộc tính (ID: {row_id}) bị lặp lại trong biểu mẫu."
                        )
                    raise forms.ValidationError(
                        f"Dữ liệu hàng liên kết thuộc tính (ID: {row_id}) không thuộc về biến thể này hoặc bị giả mạo."
                    )

        if self.instance and self.instance.pk:
            if set(seen_initial_pks) != expected_existing_pks or len(seen_initial_pks) != len(expected_existing_pks):
                raise forms.ValidationError('Thiếu hoặc sai lệch danh sách các dòng thuộc tính ban đầu.')

    def _collect_and_validate_attribute_rows(self):

        seen_attributes = set()
        selected_value_ids = []

        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue

            if self.can_delete and self._should_delete_form(form):
                continue
            if form.cleaned_data.get('DELETE', False):
                continue

            attr = form.cleaned_data.get('attribute')
            val = form.cleaned_data.get('attribute_value')

            if not attr and not val:
                continue
            if not attr and val:
                form.add_error('attribute', 'Vui lòng chọn thuộc tính tương ứng với giá trị này.')
                continue
            if attr and not val:
                form.add_error('attribute_value', 'Vui lòng chọn giá trị cho thuộc tính này.')
                continue

            if attr.id in seen_attributes:
                form.add_error('attribute', f"Thuộc tính '{attr.name}' đã được chọn ở một dòng khác.")
            else:
                seen_attributes.add(attr.id)

            if val.attribute_id != attr.id:
                form.add_error('attribute_value', f"Giá trị '{val.value}' không thuộc về thuộc tính '{attr.name}'.")

            selected_value_ids.append(val.id)

        return selected_value_ids

    def _validate_combination_and_siblings(self, selected_value_ids):

        derived_key = derive_combination_key(selected_value_ids)
        self.instance.combination_key = derived_key

        product = getattr(self.instance, 'product', None)
        if not product:
            return

        if product.is_deleted:
            raise forms.ValidationError('Không thể lưu biến thể của sản phẩm đã bị xóa mềm.')

        siblings = ProductVariant.objects.filter(product_id=product.id)
        if self.instance and self.instance.pk:
            siblings = siblings.exclude(pk=self.instance.pk)

        if derived_key == "":
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError('Sản phẩm đã có biến thể mặc định không thuộc tính.')
            if siblings.exclude(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể để trống thuộc tính vì sản phẩm đã có các biến thể theo thuộc tính (quy tắc không pha trộn).'
                )
        else:
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể gán thuộc tính vì sản phẩm đang sử dụng biến thể mặc định không thuộc tính (quy tắc không pha trộn).'
                )
            if siblings.filter(combination_key=derived_key).exists():
                raise forms.ValidationError(
                    f"Tổ hợp thuộc tính này đã tồn tại cho sản phẩm '{product.name}'."
                )

    def clean(self):
        super().clean()

        expected_existing_pks = set()
        if self.instance and self.instance.pk:
            expected_existing_pks = set(self.instance.attribute_values.values_list('pk', flat=True))

        self._validate_management_and_row_identities(expected_existing_pks)

        if any(self.errors):
            return

        selected_value_ids = self._collect_and_validate_attribute_rows()

        if any(self.errors):
            return

        self._validate_combination_and_siblings(selected_value_ids)

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)

        with transaction.atomic():
            self.instance.attribute_values.all().delete()
            saved_instances = []
            created_pks = set()
            for i, form in enumerate(self.forms):
                if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                    continue
                if self.can_delete and self._should_delete_form(form):
                    continue
                if form.cleaned_data.get('DELETE', False):
                    continue
                attr = form.cleaned_data.get('attribute')
                val = form.cleaned_data.get('attribute_value')
                if attr and val:
                    row_id = None
                    if i < self.initial_form_count():
                        raw_id = form.cleaned_data.get('id')
                        if raw_id and getattr(raw_id, 'pk', None):
                            row_id = raw_id.pk
                        elif getattr(form, 'instance', None) and form.instance.pk:
                            row_id = form.instance.pk

                    create_kwargs = {
                        'variant': self.instance,
                        'attribute': attr,
                        'attribute_value': val
                    }
                    if row_id is not None and row_id not in created_pks:
                        create_kwargs['id'] = row_id
                        created_pks.add(row_id)

                    link = VariantAttributeValue.objects.create(**create_kwargs)
                    form.instance = link
                    saved_instances.append(link)
            self.new_objects = saved_instances
            self.changed_objects = []
            self.deleted_objects = []
            return saved_instances
