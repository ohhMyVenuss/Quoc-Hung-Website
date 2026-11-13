from django.urls import path
from catalog.views import (
    category_list_view,
    category_create_view,
    category_edit_view,
    category_toggle_visibility_view,
    product_list_view,
    product_create_view,
    product_edit_view,
    product_detail_view,
    product_soft_delete_view,
    product_image_upload_view,
    product_image_delete_view,
    product_images_reorder_view,
    product_image_assign_variant_view,
    public_catalog_view,
    public_product_detail_view,
    attribute_list_view,
    attribute_create_view,
    attribute_edit_view,
    attribute_value_create_view,
    attribute_value_edit_view,
    product_variant_list_view,
    product_variant_create_view,
    product_variant_edit_view,
)

app_name = 'catalog'

urlpatterns = [
    path('', public_catalog_view, name='public_catalog'),
    path('category/<int:category_id>/', public_catalog_view, name='public_category'),
    path('product/<int:product_id>/', public_product_detail_view, name='public_product_detail'),

    path('categories/', category_list_view, name='category_list'),
    path('categories/new/', category_create_view, name='category_create'),
    path('categories/<int:category_id>/edit/', category_edit_view, name='category_edit'),
    path('categories/<int:category_id>/visibility/', category_toggle_visibility_view, name='category_toggle_visibility'),

    path('products/', product_list_view, name='product_list'),
    path('products/new/', product_create_view, name='product_create'),
    path('products/<int:product_id>/', product_detail_view, name='product_detail'),
    path('products/<int:product_id>/edit/', product_edit_view, name='product_edit'),

    path('products/<int:product_id>/delete/', product_soft_delete_view, name='product_soft_delete'),

    path('products/<int:product_id>/images/upload/', product_image_upload_view, name='product_image_upload'),
    path('products/<int:product_id>/images/<int:image_id>/delete/', product_image_delete_view, name='product_image_delete'),
    path('products/<int:product_id>/images/reorder/', product_images_reorder_view, name='product_images_reorder'),
    path('products/<int:product_id>/images/<int:image_id>/variant/', product_image_assign_variant_view, name='product_image_assign_variant'),

    path('attributes/', attribute_list_view, name='attribute_list'),
    path('attributes/new/', attribute_create_view, name='attribute_create'),
    path('attributes/<int:attribute_id>/edit/', attribute_edit_view, name='attribute_edit'),
    path('attributes/<int:attribute_id>/values/new/', attribute_value_create_view, name='attribute_value_create'),
    path('attributes/<int:attribute_id>/values/<int:value_id>/edit/', attribute_value_edit_view, name='attribute_value_edit'),

    path('products/<int:product_id>/variants/', product_variant_list_view, name='product_variant_list'),
    path('products/<int:product_id>/variants/new/', product_variant_create_view, name='product_variant_create'),
    path('products/<int:product_id>/variants/<int:variant_id>/edit/', product_variant_edit_view, name='product_variant_edit'),
]
