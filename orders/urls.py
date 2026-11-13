from django.urls import path
from orders import views

app_name = 'orders'

urlpatterns = [
    path('checkout/', views.checkout_view, name='checkout'),
    path('<int:pk>/', views.order_detail_view, name='order_detail'),
    path('<int:pk>/cancel/', views.customer_order_cancel_view, name='cancel_order'),
    path('<int:pk>/notify-transfer/', views.order_notify_transfer_view, name='notify_transfer'),

    path('management/pending/', views.pending_orders_list_view, name='pending_orders_list'),
    path('management/<int:pk>/', views.order_management_detail_view, name='management_detail'),
    path('management/<int:pk>/confirm/', views.confirm_order_action_view, name='confirm_order'),
    path('management/<int:pk>/ship/', views.ship_order_action_view, name='ship_order'),
    path('management/<int:pk>/deliver/', views.deliver_order_action_view, name='deliver_order'),
]

