from django.urls import path
from coupons import views

app_name = 'coupons'

urlpatterns = [
    path('', views.coupon_list_view, name='coupon_list'),
    path('create/', views.coupon_create_view, name='coupon_create'),
    path('<int:pk>/edit/', views.coupon_edit_view, name='coupon_edit'),
]

