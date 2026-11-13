from django.urls import path
from cart import views

app_name = 'cart'

urlpatterns = [
    path('', views.cart_detail_view, name='cart_detail'),
    path('add/', views.cart_add_view, name='cart_add'),
    path('item/<int:item_id>/update/', views.cart_update_quantity_view, name='cart_update_quantity'),
    path('item/<int:item_id>/switch/', views.cart_switch_variant_view, name='cart_switch_variant'),
    path('item/<int:item_id>/remove/', views.cart_remove_item_view, name='cart_remove_item'),
    path('sync/', views.cart_sync_guest_cart_view, name='sync_guest_cart'),
    path('coupon/preview/', views.cart_coupon_preview_view, name='cart_coupon_preview'),
]
