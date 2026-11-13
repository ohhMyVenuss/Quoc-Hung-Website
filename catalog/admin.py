from django import forms
from django.contrib import admin
from django.db import transaction
from django.core.exceptions import PermissionDenied
from catalog.models import (
    Category, Product, ProductImage, Attribute, AttributeValue,
    ProductVariant, VariantAttributeValue,
)
from catalog.forms import (
    ProductImageAdminForm, AttributeValueAdminForm,
    AttributeValueInlineForm, AttributeValueInlineFormSet,
    ProductVariantAdminForm, VariantAttributeValueInlineForm,
    VariantAttributeValueInlineFormSet,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'is_visible')
    list_filter = ('is_visible',)
    search_fields = ('name', 'description')
    ordering = ('id',)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


class ProductImageInlineFormSet(forms.BaseInlineFormSet):

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        if self.instance and self.instance.pk:
            form.parent_product = self.instance
            if 'variant' in form.fields:
                form.fields['variant'].queryset = ProductVariant.objects.filter(product_id=self.instance.pk).order_by('id')
                form.fields['variant'].empty_label = "-- Ảnh chung của sản phẩm --"
                form.fields['variant'].label_from_instance = lambda obj: f"{obj.sku}" + (f" ({obj.attribute_summary})" if obj.attribute_summary != "Mặc định" else "")
        return form


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    form = ProductImageAdminForm
    formset = ProductImageInlineFormSet
    extra = 1
    fields = ('image', 'variant', 'sort_order')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'variant':
            object_id = request.resolver_match.kwargs.get('object_id') if hasattr(request, 'resolver_match') and request.resolver_match else None
            if object_id:
                kwargs['queryset'] = ProductVariant.objects.filter(product_id=object_id).order_by('id')
            else:
                kwargs['queryset'] = ProductVariant.objects.none()
            kwargs['empty_label'] = "-- Ảnh chung của sản phẩm --"
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request, obj):
        if obj is not None and obj.is_deleted:
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_deleted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_deleted:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'product_code', 'name', 'category', 'brand', 'is_active', 'is_deleted')
    list_filter = ('is_active', 'is_deleted', 'category')
    search_fields = ('name', 'brand', 'description')
    readonly_fields = ('product_code', 'is_deleted', 'deleted_at')
    inlines = [ProductImageInline]
    ordering = ('-id',)

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_deleted:
            return False
        return super().has_change_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    form = ProductImageAdminForm
    list_display = ('id', 'product', 'variant_display', 'sort_order', 'image')
    list_filter = ('product__category', 'variant__is_active')
    ordering = ('product', 'sort_order', 'id')

    @admin.display(description='Biến thể liên kết')
    def variant_display(self, obj):
        return obj.variant.sku if obj.variant else "Ảnh chung"

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.product and obj.product.is_deleted:
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.product and obj.product.is_deleted:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if obj.product and obj.product.is_deleted:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied('Không thể thêm hoặc chuyển ảnh cho sản phẩm đã bị xóa mềm.')
        if obj.variant and obj.product and obj.variant.product_id != obj.product_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'variant': 'Biến thể được chọn không thuộc sản phẩm này.'})
        super().save_model(request, obj, form, change)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


class AttributeValueInline(admin.TabularInline):
    model = AttributeValue
    form = AttributeValueInlineForm
    formset = AttributeValueInlineFormSet
    extra = 1
    fields = ('value',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)
    ordering = ('name',)
    inlines = [AttributeValueInline]

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


@admin.register(AttributeValue)
class AttributeValueAdmin(admin.ModelAdmin):
    form = AttributeValueAdminForm
    list_display = ('id', 'attribute', 'value')
    list_filter = ('attribute',)
    search_fields = ('value', 'attribute__name')
    ordering = ('attribute', 'id')

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


class VariantAttributeValueInline(admin.TabularInline):

    model = VariantAttributeValue
    form = VariantAttributeValueInlineForm
    formset = VariantAttributeValueInlineFormSet
    extra = 1
    fields = ('attribute', 'attribute_value')

    def has_delete_permission(self, request, obj=None):
        return True


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):

    form = ProductVariantAdminForm
    inlines = [VariantAttributeValueInline]
    list_display = ('id', 'product', 'sku', 'price', 'original_price', 'combination_key', 'is_active')
    list_filter = ('is_active', 'product__category')
    search_fields = ('sku', 'product__name', 'combination_key')
    ordering = ('product', 'id')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('combination_key', 'product')
        return ('combination_key',)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        with transaction.atomic():
            return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        if obj.product and obj.product.is_deleted:
            raise PermissionDenied('Không thể thêm hoặc chỉnh sửa biến thể của sản phẩm đã bị xóa mềm.')
        if change:
            orig_prod_id = ProductVariant.objects.filter(pk=obj.pk).values_list('product_id', flat=True).first()
            if orig_prod_id and orig_prod_id != obj.product_id:
                raise PermissionDenied('Không được phép chuyển đổi sản phẩm cha của biến thể.')
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        with transaction.atomic():
            super().save_related(request, form, formsets, change)
