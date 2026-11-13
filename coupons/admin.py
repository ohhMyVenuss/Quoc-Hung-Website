from django.contrib import admin
from django.contrib.admin import helpers
from django.contrib.admin.exceptions import DisallowedModelAdminToField
from django.contrib.admin.options import (
    TO_FIELD_VAR, IS_POPUP_VAR, SOURCE_MODEL_VAR, flatten_fieldsets, all_valid, _
)
from django.contrib.admin.utils import unquote, display_for_value
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import OperationalError, IntegrityError, transaction
from django.forms.models import BaseInlineFormSet
from django.forms.widgets import CheckboxInput

from accounts.permissions import is_admin
from coupons.models import Coupon, CouponProduct
from coupons.services import execute_sqlite_safe_save, update_coupon_admin_fields


class CouponProductInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if self.instance and hasattr(self.instance, 'applies_to_all') and self.instance.applies_to_all is not None:
            applies_to_all = bool(self.instance.applies_to_all)
        elif self.data:
            applies_to_all = bool(CheckboxInput().value_from_datadict(self.data, self.files, 'applies_to_all'))
        else:
            applies_to_all = True

        active_count = 0
        for form in self.forms:
            if getattr(form, 'cleaned_data', None):
                if not form.cleaned_data.get('DELETE', False) and form.cleaned_data.get('product'):
                    active_count += 1

        if not applies_to_all and active_count == 0:
            raise ValidationError("Khi không áp dụng cho toàn bộ sản phẩm (Sản phẩm chỉ định), bắt buộc phải có ít nhất 1 sản phẩm áp dụng.")


class CouponProductInline(admin.TabularInline):
    model = CouponProduct
    formset = CouponProductInlineFormSet
    extra = 1
    autocomplete_fields = ['product']

    def has_module_permission(self, request):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_add_permission(self, request, obj=None):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'discount_type',
        'value_display',
        'min_order_amount',
        'max_discount',
        'max_uses',
        'used_count',
        'reserved_count',
        'remaining_uses',
        'starts_at',
        'ends_at',
        'is_active',
        'applies_to_all',
    )
    list_filter = ('discount_type', 'is_active', 'applies_to_all')
    search_fields = ('code',)
    readonly_fields = ('used_count', 'reserved_count')
    inlines = [CouponProductInline]

    def has_module_permission(self, request):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_add_permission(self, request):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_authenticated and is_admin(request.user))

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if obj.code:
            obj.code = obj.code.strip().upper()
        if change:
            def _admin_save():
                target_fields = {
                    'code': obj.code,
                    'discount_type': obj.discount_type,
                    'value': obj.value,
                    'max_discount': obj.max_discount,
                    'min_order_amount': obj.min_order_amount,
                    'max_uses': obj.max_uses,
                    'starts_at': obj.starts_at,
                    'ends_at': obj.ends_at,
                    'is_active': obj.is_active,
                    'applies_to_all': obj.applies_to_all,
                }
                update_coupon_admin_fields(obj.pk, target_fields)
                obj.refresh_from_db()
            execute_sqlite_safe_save(_admin_save)
        else:
            obj.used_count = 0
            obj.reserved_count = 0
            def _admin_create():
                super(CouponAdmin, self).save_model(request, obj, form, change)
            execute_sqlite_safe_save(_admin_create)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.instance.applies_to_all:
            CouponProduct.objects.filter(coupon=form.instance).delete()

    def _reset_instances_after_rollback(self, new_object, formsets, add):
        if add:
            new_object.pk = None
            new_object._state.adding = True
            for formset in (formsets or []):
                for f in getattr(formset, 'forms', []):
                    if hasattr(f, 'instance') and f.instance:
                        f.instance.pk = None
                        f.instance._state.adding = True
                        if hasattr(formset, 'fk') and hasattr(f.instance, formset.fk.name):
                            setattr(f.instance, formset.fk.name, new_object)
                if hasattr(formset, 'new_objects'):
                    formset.new_objects = []
                if hasattr(formset, 'changed_objects'):
                    formset.changed_objects = []
                if hasattr(formset, 'deleted_objects'):
                    formset.deleted_objects = []
        else:
            try:
                new_object.refresh_from_db()
            except Exception:
                pass
            for formset in (formsets or []):
                for f in getattr(formset, 'initial_forms', []):
                    if hasattr(f, 'instance') and f.instance and f.instance.pk:
                        try:
                            f.instance.refresh_from_db()
                        except Exception:
                            pass
                for f in getattr(formset, 'extra_forms', []):
                    if hasattr(f, 'instance') and f.instance:
                        f.instance.pk = None
                        f.instance._state.adding = True
                if hasattr(formset, 'new_objects'):
                    formset.new_objects = []
                if hasattr(formset, 'changed_objects'):
                    formset.changed_objects = []
                if hasattr(formset, 'deleted_objects'):
                    formset.deleted_objects = []

    def _changeform_view(self, request, object_id, form_url, extra_context):
        to_field = request.POST.get(TO_FIELD_VAR, request.GET.get(TO_FIELD_VAR))
        if to_field and not self.to_field_allowed(request, to_field):
            raise DisallowedModelAdminToField("The field %s cannot be referenced." % to_field)

        if request.method == "POST" and "_saveasnew" in request.POST:
            object_id = None

        add = object_id is None
        if add:
            if not self.has_add_permission(request):
                raise PermissionDenied
            obj = None
        else:
            obj = self.get_object(request, unquote(object_id), to_field)
            if not self.has_view_or_change_permission(request, obj):
                raise PermissionDenied
            if obj is None:
                return self._get_obj_does_not_exist_redirect(request, self.opts, object_id)

        fieldsets = self.get_fieldsets(request, obj)
        ModelForm = self.get_form(request, obj, change=not add, fields=flatten_fieldsets(fieldsets))

        if request.method == "POST":
            if not add and not self.has_change_permission(request, obj):
                raise PermissionDenied

            form = ModelForm(request.POST, request.FILES, instance=obj)
            formsets, inline_instances = self._create_formsets(request, form.instance, change=not add)
            form_validated = form.is_valid()
            if form_validated:
                new_object = self.save_form(request, form, change=not add)
            else:
                new_object = form.instance

            if all_valid(formsets) and form_validated:
                try:
                    with transaction.atomic():
                        self.save_model(request, new_object, form, not add)
                        self.save_related(request, form, formsets, not add)
                        change_message = self.construct_change_message(request, form, formsets, add)
                        if add:
                            self.log_addition(request, new_object, change_message)
                        else:
                            self.log_change(request, new_object, change_message)
                except ValidationError as exc:
                    form.add_error(None, exc)
                    form_validated = False
                    self._reset_instances_after_rollback(new_object, formsets, add)
                except (OperationalError, IntegrityError) as exc:
                    err_msg = str(exc).lower()
                    if 'locked' in err_msg or 'busy' in err_msg:
                        form.add_error(None, "Hệ thống đang bận do có thao tác ghi đồng thời hoặc dữ liệu đang được cập nhật. Vui lòng thử lại sau giây lát.")
                    elif 'code' in err_msg or 'unique' in err_msg:
                        form.add_error('code', f"Mã voucher '{new_object.code}' đã tồn tại trong hệ thống.")
                    else:
                        form.add_error(None, f"Xảy ra xung đột dữ liệu khi cập nhật: {exc}")
                    form_validated = False
                    self._reset_instances_after_rollback(new_object, formsets, add)
                else:
                    if add:
                        return self.response_add(request, new_object)
                    else:
                        return self.response_change(request, new_object)
            else:
                form_validated = False
        else:
            if add:
                initial = self.get_changeform_initial_data(request)
                form = ModelForm(initial=initial)
                formsets, inline_instances = self._create_formsets(request, form.instance, change=False)
            else:
                form = ModelForm(instance=obj)
                formsets, inline_instances = self._create_formsets(request, obj, change=True)

        if not add and not self.has_change_permission(request, obj):
            readonly_fields = flatten_fieldsets(fieldsets)
        else:
            readonly_fields = self.get_readonly_fields(request, obj)

        admin_form = helpers.AdminForm(
            form,
            list(fieldsets),
            self.get_prepopulated_fields(request, obj) if (add or self.has_change_permission(request, obj)) else {},
            readonly_fields,
            model_admin=self,
        )
        media = self.media + admin_form.media

        inline_admin_formsets = self.get_inline_formsets(request, formsets, inline_instances, obj)
        for inline_formset in inline_admin_formsets:
            media += inline_formset.media

        title = _("Add %s") % self.opts.verbose_name if add else _("Change %s") % self.opts.verbose_name
        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "subtitle": display_for_value(str(obj), "-") if obj else None,
            "adminform": admin_form,
            "object_id": object_id,
            "original": obj,
            "is_popup": False,
            "to_field": to_field,
            "media": media,
            "inline_admin_formsets": inline_admin_formsets,
            "errors": helpers.AdminErrorList(form, formsets),
            "preserved_filters": self.get_preserved_filters(request),
        }
        context.update(extra_context or {})
        return self.render_change_form(request, context, add=add, change=not add, obj=obj, form_url=form_url)
