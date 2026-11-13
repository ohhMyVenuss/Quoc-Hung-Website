from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q, Exists, OuterRef
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone
from urllib.parse import urlencode, quote

from accounts.permissions import is_sales_staff, is_admin
from catalog.models import (
    Category, Product, ProductImage, Attribute, AttributeValue,
    ProductVariant, VariantAttributeValue,
)
from catalog.forms import (
    CategoryForm, ProductForm, ProductImageForm, ProductImageVariantAssignForm,
    AttributeForm, AttributeValueForm, ProductVariantForm,
)


def parse_positive_id(val, max_digits=12):
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val or len(val) > max_digits:
        return None
    if not (val.isascii() and val.isdigit()):
        return None
    try:
        parsed = int(val)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def category_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    categories = Category.objects.all().order_by('id')

    context = {
        'categories': categories,
        'active_nav': 'catalog_categories',
    }
    return render(request, 'catalog/category_list.html', context)


def category_create_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            messages.success(request, f"Đã tạo mới danh mục '{category.name}' thành công.")
            return redirect('catalog:category_list')
    else:
        form = CategoryForm()

    context = {
        'form': form,
        'title': 'Thêm mới danh mục sản phẩm',
        'active_nav': 'catalog_categories',
    }
    return render(request, 'catalog/category_form.html', context)


def category_edit_view(request, category_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    category = get_object_or_404(Category, id=category_id)

    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, f"Đã cập nhật danh mục '{category.name}' thành công.")
            return redirect('catalog:category_list')
    else:
        form = CategoryForm(instance=category)

    context = {
        'form': form,
        'category': category,
        'title': f"Chỉnh sửa danh mục: {category.name}",
        'active_nav': 'catalog_categories',
    }
    return render(request, 'catalog/category_form.html', context)


def category_toggle_visibility_view(request, category_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Phương thức không được hỗ trợ. Thao tác yêu cầu POST.")

    category = get_object_or_404(Category, id=category_id)
    action = request.POST.get('action', '').strip().lower()

    if action not in ['hide', 'show']:
        return HttpResponseBadRequest("Hành động không hợp lệ. Chỉ chấp nhận 'hide' hoặc 'show'.")

    if action == 'hide':
        category.is_visible = False
        action_text = "ẩn khỏi giao diện khách hàng"
    else:
        category.is_visible = True
        action_text = "kích hoạt hiển thị cho khách hàng"

    category.save(update_fields=['is_visible'])
    messages.success(request, f"Danh mục '{category.name}' đã được {action_text}.")
    return redirect('catalog:category_list')


def product_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    queryset = Product.objects.select_related('category').all()

    q = request.GET.get('q', '').strip()
    if q:
        q_filter = Q(name__icontains=q) | Q(brand__icontains=q)
        clean_q = q.upper()
        if clean_q.startswith('SP'):
            parsed_id = parse_positive_id(clean_q[2:])
            if parsed_id is not None:
                q_filter |= Q(id=parsed_id)
        else:
            parsed_id = parse_positive_id(q)
            if parsed_id is not None:
                q_filter |= Q(id=parsed_id)
        queryset = queryset.filter(q_filter)

    category_id = request.GET.get('category', '').strip()
    if category_id:
        cat_id = parse_positive_id(category_id)
        if cat_id is not None:
            queryset = queryset.filter(category_id=cat_id)
        else:
            queryset = queryset.none()

    status = request.GET.get('status', '').strip().lower()
    if status == 'active':
        queryset = queryset.filter(is_active=True, is_deleted=False)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False, is_deleted=False)
    elif status == 'deleted':
        queryset = queryset.filter(is_deleted=True)
    else:
        queryset = queryset.filter(is_deleted=False)

    categories = Category.objects.all().order_by('name')

    context = {
        'products': queryset,
        'categories': categories,
        'selected_category': category_id,
        'selected_status': status,
        'search_query': q,
        'active_nav': 'catalog_products',
    }
    return render(request, 'catalog/product_list.html', context)


def product_create_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            messages.success(request, f"Đã thêm mới sản phẩm '{product.name}' ({product.product_code}) thành công.")
            submit_action = request.POST.get('submit_action')
            if submit_action == 'save':
                return redirect('catalog:product_list')
            elif submit_action == 'save_and_upload':
                return redirect(f"{reverse('catalog:product_detail', args=[product.id])}#gallery")
            else:
                return redirect('catalog:product_detail', product_id=product.id)
    else:
        form = ProductForm()

    context = {
        'form': form,
        'title': 'Thêm mới sản phẩm',
        'active_nav': 'catalog_products',
    }
    return render(request, 'catalog/product_form.html', context)


def product_edit_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), id=product_id)

    if product.is_deleted:
        messages.error(request, f"Sản phẩm {product.product_code} đã bị xóa mềm, không thể chỉnh sửa.")
        return redirect('catalog:product_list')

    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f"Đã cập nhật thông tin sản phẩm '{product.name}' ({product.product_code}) thành công.")
            submit_action = request.POST.get('submit_action')
            if submit_action == 'save':
                return redirect('catalog:product_list')
            elif submit_action == 'save_and_upload':
                return redirect(f"{reverse('catalog:product_detail', args=[product.id])}#gallery")
            return redirect('catalog:product_detail', product_id=product.id)
    else:
        form = ProductForm(instance=product)

    context = {
        'form': form,
        'product': product,
        'title': f"Chỉnh sửa sản phẩm: {product.name}",
        'active_nav': 'catalog_products',
    }
    return render(request, 'catalog/product_form.html', context)


def product_detail_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('images'),
        id=product_id
    )
    images = product.images.select_related('variant').all().order_by('sort_order', 'id')
    image_form = ProductImageForm(product=product)

    context = {
        'product': product,
        'images': images,
        'image_form': image_form,
        'has_soft_delete': True,
        'active_nav': 'catalog_products',
    }
    return render(request, 'catalog/product_detail.html', context)


def product_soft_delete_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_admin(request.user):
        return HttpResponseForbidden("Chỉ Quản trị viên (Admin) mới có quyền thực hiện xóa mềm sản phẩm.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if not product.is_deleted:
        product.is_deleted = True
        product.deleted_at = timezone.now()
        product.save(update_fields=['is_deleted', 'deleted_at'])
        messages.success(request, f"Đã xóa mềm sản phẩm '{product.name}' ({product.product_code}) thành công.")
    else:
        messages.info(request, f"Sản phẩm '{product.name}' ({product.product_code}) đã ở trạng thái xóa mềm trước đó.")

    return redirect('catalog:product_list')


def product_image_upload_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if product.is_deleted:
        messages.error(request, "Không thể tải ảnh cho sản phẩm đã bị xóa mềm.")
        return redirect('catalog:product_detail', product_id=product.id)

    form = ProductImageForm(request.POST, request.FILES, product=product)
    if form.is_valid():
        product_image = form.save(commit=False)
        product_image.product = product
        product_image.save()
        if product_image.variant:
            messages.success(request, f"Đã tải lên ảnh mới cho sản phẩm {product.product_code} gắn với biến thể {product_image.variant.sku} thành công.")
        else:
            messages.success(request, f"Đã tải lên ảnh chung mới cho sản phẩm {product.product_code} thành công.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{error}")

    return redirect('catalog:product_detail', product_id=product.id)


def product_image_delete_view(request, product_id, image_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if product.is_deleted:
        messages.error(request, "Không thể xóa hình ảnh của sản phẩm đã bị xóa mềm.")
        return redirect('catalog:product_detail', product_id=product.id)

    image_obj = get_object_or_404(ProductImage, id=image_id, product=product)

    image_obj.image.delete(save=False)
    image_obj.delete()
    messages.success(request, "Đã xóa ảnh sản phẩm thành công.")

    return redirect('catalog:product_detail', product_id=product.id)


def product_image_assign_variant_view(request, product_id, image_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if product.is_deleted:
        messages.error(request, "Không thể chỉnh sửa ảnh của sản phẩm đã bị xóa mềm.")
        return redirect('catalog:product_detail', product_id=product.id)

    image_obj = get_object_or_404(ProductImage, id=image_id, product=product)

    form = ProductImageVariantAssignForm(request.POST, instance=image_obj, product=product)
    if form.is_valid():
        form.save()
        if image_obj.variant:
            messages.success(request, f"Đã cập nhật ảnh #{image_obj.id} gắn với biến thể {image_obj.variant.sku} thành công.")
        else:
            messages.success(request, f"Đã chuyển ảnh #{image_obj.id} thành ảnh chung của sản phẩm thành công.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"Lỗi cập nhật ảnh #{image_obj.id}: {error}")

    return redirect('catalog:product_detail', product_id=product.id)


def product_images_reorder_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    with transaction.atomic():
        locked_product = Product.objects.select_for_update().get(id=product.id)
        if locked_product.is_deleted:
            messages.error(request, "Không thể thay đổi thứ tự ảnh của sản phẩm đã bị xóa mềm.")
            return redirect('catalog:product_detail', product_id=product.id)

        existing_images = {img.id: img for img in locked_product.images.select_for_update().all()}

        action_raw = request.POST.get('action')
        if action_raw is None or action_raw == '':
            action = 'save_order'
        else:
            action = action_raw.strip()
            if action not in ('save_order', 'set_primary'):
                messages.error(request, f"Hành động '{action}' không hợp lệ.")
                return redirect('catalog:product_detail', product_id=product.id)

        if action == 'set_primary':
            primary_id_list = request.POST.getlist('primary_image_id')
            if len(primary_id_list) != 1:
                messages.error(request, "Dữ liệu chỉ định ảnh đại diện không hợp lệ.")
                return redirect('catalog:product_detail', product_id=product.id)

            primary_id_raw = primary_id_list[0].strip()
            parsed_primary_id = parse_positive_id(primary_id_raw)
            if parsed_primary_id is None or parsed_primary_id not in existing_images:
                messages.error(request, "Ảnh đại diện được chỉ định không hợp lệ hoặc không thuộc sản phẩm này.")
                return redirect('catalog:product_detail', product_id=product.id)

            other_images = [img for img_id, img in existing_images.items() if img_id != parsed_primary_id]
            other_images.sort(key=lambda x: (x.sort_order, x.id))

            existing_images[parsed_primary_id].sort_order = 0
            existing_images[parsed_primary_id].save(update_fields=['sort_order'])

            for idx, img in enumerate(other_images, start=1):
                if img.sort_order != idx:
                    img.sort_order = idx
                    img.save(update_fields=['sort_order'])

            messages.success(request, f"Đã đặt ảnh #{parsed_primary_id} làm ảnh đại diện chính.")
            return redirect('catalog:product_detail', product_id=product.id)

        updates = {}
        seen_ids = set()
        found_any = False

        for key, values in request.POST.lists():
            if key.startswith('sort_order_'):
                found_any = True
                if len(values) > 1:
                    messages.error(request, f"Dữ liệu gửi lên chứa khóa thứ tự trùng lặp ({key}). Thao tác đã bị hủy.")
                    return redirect('catalog:product_detail', product_id=product.id)

                img_id_str = key[len('sort_order_'):]
                parsed_id = parse_positive_id(img_id_str)
                if parsed_id is None or parsed_id not in existing_images:
                    messages.error(request, f"Dữ liệu ảnh không hợp lệ hoặc chứa mã ảnh không thuộc sản phẩm ({img_id_str}).")
                    return redirect('catalog:product_detail', product_id=product.id)

                if parsed_id in seen_ids:
                    messages.error(request, f"Phát hiện mã ảnh trùng lặp trong dữ liệu gửi lên (#{parsed_id}). Thao tác đã bị hủy.")
                    return redirect('catalog:product_detail', product_id=product.id)
                seen_ids.add(parsed_id)

                val_str = str(values[0]).strip()
                if not (val_str.isascii() and val_str.isdigit()) or len(val_str) > 19:
                    messages.error(request, f"Thứ tự hiển thị '{val_str}' không hợp lệ. Vui lòng nhập số nguyên không âm.")
                    return redirect('catalog:product_detail', product_id=product.id)

                val_int = int(val_str)
                if val_int > 9223372036854775807:
                    messages.error(request, "Thứ tự hiển thị vượt quá giới hạn tối đa cho phép.")
                    return redirect('catalog:product_detail', product_id=product.id)

                updates[parsed_id] = val_int

        if not found_any or not updates:
            messages.warning(request, "Không có thay đổi thứ tự nào được ghi nhận.")
            return redirect('catalog:product_detail', product_id=product.id)

        for img_id, new_order in updates.items():
            img = existing_images[img_id]
            if img.sort_order != new_order:
                img.sort_order = new_order
                img.save(update_fields=['sort_order'])

        messages.success(request, "Đã lưu cập nhật thứ tự hiển thị ảnh thành công.")
        return redirect('catalog:product_detail', product_id=product.id)


from .utils import format_vnd


def homepage_view(request):
    visible_categories = Category.objects.filter(is_visible=True).order_by('name')

    products_qs = (
        Product.objects.filter(
            category__is_visible=True,
            is_active=True,
            is_deleted=False,
        )
        .select_related('category')
        .prefetch_related('images')
        .annotate(
            has_active_variants=Exists(
                ProductVariant.objects.filter(product=OuterRef('pk'), is_active=True)
            )
        )
        .order_by('category__name', 'name')
    )

    featured_products = list(products_qs[:12])

    context = {
        'categories': visible_categories,
        'products': featured_products,
        'total_featured_count': len(featured_products),
        'active_nav': 'home',
        'customer_notice': 'Thông tin phiên bản và giá bán đang được cập nhật',
    }
    return render(request, 'catalog/homepage.html', context)


def public_catalog_view(request, category_id=None):
    visible_categories = Category.objects.filter(is_visible=True).order_by('name')

    selected_category = None
    invalid_category = False

    if category_id:
        selected_category = get_object_or_404(Category, id=category_id, is_visible=True)
    else:
        query_cat = request.GET.get('category', '').strip()
        if query_cat:
            cat_id = parse_positive_id(query_cat)
            if cat_id is not None:
                selected_category = Category.objects.filter(id=cat_id, is_visible=True).first()
                if not selected_category:
                    invalid_category = True
            else:
                invalid_category = True

    products_qs = (
        Product.objects.filter(
            category__is_visible=True,
            is_active=True,
            is_deleted=False,
        )
        .select_related('category')
        .prefetch_related('images')
        .annotate(
            has_active_variants=Exists(
                ProductVariant.objects.filter(product=OuterRef('pk'), is_active=True)
            )
        )
    )

    if invalid_category:
        products_qs = products_qs.none()
    elif selected_category:
        products_qs = products_qs.filter(category=selected_category)

    q = request.GET.get('q', '').strip()
    if q:
        q_filter = Q(name__icontains=q) | Q(brand__icontains=q)
        clean_q = q.upper()
        if clean_q.startswith('SP'):
            parsed_id = parse_positive_id(clean_q[2:])
            if parsed_id is not None:
                q_filter |= Q(id=parsed_id)
        else:
            parsed_id = parse_positive_id(q)
            if parsed_id is not None:
                q_filter |= Q(id=parsed_id)
        products_qs = products_qs.filter(q_filter)

    products_qs = products_qs.order_by('category__name', 'name')

    paginator = Paginator(products_qs, 12)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    preserved_params = {}
    if q:
        preserved_params['q'] = q
    query_cat_param = request.GET.get('category', '').strip()
    if not category_id and query_cat_param and not invalid_category and selected_category:
        preserved_params['category'] = str(selected_category.id)
    preserved_querystring = urlencode(preserved_params, quote_via=quote)

    context = {
        'categories': visible_categories,
        'selected_category': selected_category,
        'products': page_obj,
        'page_obj': page_obj,
        'total_results_count': paginator.count,
        'search_query': q,
        'preserved_querystring': preserved_querystring,
        'active_nav': 'public_catalog',
        'customer_notice': 'Thông tin phiên bản và giá bán đang được cập nhật',
    }
    return render(request, 'catalog/public_catalog.html', context)


def public_product_detail_view(request, product_id):
    product = get_object_or_404(
        Product.objects.select_related('category'),
        id=product_id,
        category__is_visible=True,
        is_active=True,
        is_deleted=False,
    )

    all_images = list(product.images.all().order_by('sort_order', 'id'))
    visible_categories = Category.objects.filter(is_visible=True).order_by('name')

    active_variants = list(
        product.variants.filter(is_active=True)
        .select_related('inventory')
        .prefetch_related('attribute_values__attribute', 'attribute_values__attribute_value')
        .order_by('id')
    )

    has_variants = bool(active_variants)
    selected_variant = None
    selected_variant_data = None
    selected_gallery_images = []
    primary_img = None
    variants_json = {}

    if has_variants:
        for v in active_variants:
            links = list(v.attribute_values.all())
            if links:
                links.sort(key=lambda link: (link.attribute.name, link.id))
                v.display_label = ", ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
            else:
                v.display_label = "Phiên bản mặc định"

            try:
                inv = v.inventory
                has_inv = True
                avail = max(0, inv.available)
            except (AttributeError, ObjectDoesNotExist):
                has_inv = False
                avail = 0

            v.avail_count = avail
            v.has_inv = has_inv
            v.is_in_stock = (avail > 0)

            v_linked = [img for img in all_images if img.variant_id == v.id]
            v_general = [img for img in all_images if img.variant_id is None]
            v_imgs = v_linked + v_general

            has_disc = bool(v.original_price is not None and v.original_price > v.price)

            variants_json[str(v.id)] = {
                'id': v.id,
                'sku': v.sku,
                'label': v.display_label,
                'price': str(v.price),
                'price_formatted': format_vnd(v.price),
                'original_price': str(v.original_price) if v.original_price is not None else None,
                'original_price_formatted': format_vnd(v.original_price) if has_disc else "",
                'has_discount': has_disc,
                'available': avail,
                'has_inventory': has_inv,
                'is_in_stock': v.is_in_stock,
                'images': [
                    {
                        'id': img.id,
                        'url': img.image.url,
                        'alt': f"{product.name} - {v.display_label}",
                    }
                    for img in v_imgs
                ],
            }

        req_variant_id = request.GET.get('variant')
        requested_variant = None
        if req_variant_id:
            try:
                var_id_int = int(req_variant_id)
                requested_variant = next((v for v in active_variants if v.id == var_id_int), None)
            except (ValueError, TypeError):
                requested_variant = None

        if requested_variant:
            selected_variant = requested_variant
        else:
            in_stock_variants = [v for v in active_variants if v.avail_count > 0]
            if in_stock_variants:
                selected_variant = min(in_stock_variants, key=lambda v: v.id)
            else:
                selected_variant = min(active_variants, key=lambda v: v.id)

        selected_variant_data = variants_json[str(selected_variant.id)]
        selected_linked = [img for img in all_images if img.variant_id == selected_variant.id]
        selected_general = [img for img in all_images if img.variant_id is None]
        selected_gallery_images = selected_linked + selected_general
        if selected_gallery_images:
            primary_img = selected_gallery_images[0]
    else:
        selected_gallery_images = [img for img in all_images if img.variant_id is None]
        if selected_gallery_images:
            primary_img = selected_gallery_images[0]

    initial_quantity = '1'
    req_qty = request.GET.get('quantity')
    if req_qty:
        cleaned_qty = req_qty.strip()
        if cleaned_qty:
            initial_quantity = cleaned_qty

    context = {
        'product': product,
        'has_variants': has_variants,
        'active_variants': active_variants,
        'selected_variant': selected_variant,
        'selected_variant_data': selected_variant_data,
        'selected_gallery_images': selected_gallery_images,
        'primary_img': primary_img,
        'variants_json': variants_json,
        'initial_quantity': initial_quantity,
        'categories': visible_categories,
        'active_nav': 'public_catalog',
        'customer_notice': 'Thông tin phiên bản và giá bán đang được cập nhật',
    }
    return render(request, 'catalog/public_product_detail.html', context)


def attribute_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attributes = Attribute.objects.prefetch_related('values').all().order_by('name')

    context = {
        'attributes': attributes,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'catalog/attribute_list.html', context)


def attribute_create_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method == 'POST':
        form = AttributeForm(request.POST)
        if form.is_valid():
            attribute = form.save()
            messages.success(request, f"Đã thêm thuộc tính '{attribute.name}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeForm()

    context = {
        'form': form,
        'title': 'Thêm thuộc tính mới',
        'is_edit': False,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'catalog/attribute_form.html', context)


def attribute_edit_view(request, attribute_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)

    if request.method == 'POST':
        form = AttributeForm(request.POST, instance=attribute)
        if form.is_valid():
            attribute = form.save()
            messages.success(request, f"Đã cập nhật thuộc tính '{attribute.name}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeForm(instance=attribute)

    context = {
        'form': form,
        'attribute': attribute,
        'title': f"Chỉnh sửa thuộc tính: {attribute.name}",
        'is_edit': True,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'catalog/attribute_form.html', context)


def attribute_value_create_view(request, attribute_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)

    if request.method == 'POST':
        form = AttributeValueForm(request.POST, attribute=attribute)
        if form.is_valid():
            val = form.save()
            messages.success(request, f"Đã thêm giá trị '{val.value}' cho thuộc tính '{attribute.name}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeValueForm(attribute=attribute)

    context = {
        'form': form,
        'attribute': attribute,
        'title': f"Thêm giá trị cho '{attribute.name}'",
        'is_edit': False,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'catalog/attribute_value_form.html', context)


def attribute_value_edit_view(request, attribute_id, value_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)
    value_obj = get_object_or_404(AttributeValue, pk=value_id, attribute=attribute)

    if request.method == 'POST':
        form = AttributeValueForm(request.POST, instance=value_obj, attribute=attribute)
        if form.is_valid():
            val = form.save()
            messages.success(request, f"Đã cập nhật giá trị '{val.value}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeValueForm(instance=value_obj, attribute=attribute)

    context = {
        'form': form,
        'attribute': attribute,
        'value_obj': value_obj,
        'title': f"Chỉnh sửa giá trị: {value_obj.value}",
        'is_edit': True,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'catalog/attribute_value_form.html', context)


def product_variant_list_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), pk=product_id)
    variants = product.variants.all().prefetch_related(
        'attribute_values__attribute',
        'attribute_values__attribute_value'
    ).order_by('id')

    variant_data = []
    for v in variants:
        if v.combination_key == "":
            comb_text = "Mặc định (Không thuộc tính)"
            is_empty_comb = True
        else:
            links = list(v.attribute_values.all())
            if links:
                comb_text = " | ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
            else:
                comb_text = v.combination_key
            is_empty_comb = False

        variant_data.append({
            'obj': v,
            'combination_display': comb_text,
            'is_empty_combination': is_empty_comb,
        })

    context = {
        'product': product,
        'variant_data': variant_data,
        'variant_count': len(variant_data),
        'active_nav': 'catalog_products',
        'is_admin': is_admin(request.user),
    }
    return render(request, 'catalog/variant_list.html', context)


def product_variant_create_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), pk=product_id)

    if product.is_deleted:
        messages.error(request, 'Không thể tạo biến thể cho sản phẩm đã bị xóa mềm.')
        return redirect('catalog:product_variant_list', product_id=product.id)

    if request.method == 'POST':
        form = ProductVariantForm(product=product, data=request.POST)
        if form.is_valid():
            variant = form.save()
            messages.success(request, f"Đã thêm biến thể mới với mã SKU '{variant.sku}' thành công.")
            return redirect('catalog:product_variant_list', product_id=product.id)
    else:
        form = ProductVariantForm(product=product)

    context = {
        'product': product,
        'form': form,
        'title': f"Thêm biến thể mới — {product.name}",
        'is_edit': False,
        'active_nav': 'catalog_products',
    }
    return render(request, 'catalog/variant_form.html', context)


def product_variant_edit_view(request, product_id, variant_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), pk=product_id)
    variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)

    if product.is_deleted:
        messages.error(request, 'Không thể chỉnh sửa biến thể của sản phẩm đã bị xóa mềm.')
        return redirect('catalog:product_variant_list', product_id=product.id)

    if request.method == 'POST':
        form = ProductVariantForm(product=product, data=request.POST, instance=variant)
        if form.is_valid():
            v = form.save()
            messages.success(request, f"Đã cập nhật biến thể '{v.sku}' thành công.")
            return redirect('catalog:product_variant_list', product_id=product.id)
    else:
        form = ProductVariantForm(product=product, instance=variant)

    context = {
        'product': product,
        'variant': variant,
        'form': form,
        'title': f"Chỉnh sửa biến thể: {variant.sku}",
        'is_edit': True,
        'active_nav': 'catalog_products',
    }
    return render(request, 'catalog/variant_form.html', context)
