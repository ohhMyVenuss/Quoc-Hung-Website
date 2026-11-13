from django.urls import path
from inventory.views import (
    inventory_list_view,
    inventory_detail_view,
    inventory_update_threshold_view,
    inventory_receive_view,
    inventory_adjust_view,
    inventory_transaction_list_view,
)

app_name = 'inventory'

urlpatterns = [
    path('', inventory_list_view, name='inventory_list'),
    path('history/', inventory_transaction_list_view, name='inventory_history'),
    path('<int:inventory_id>/', inventory_detail_view, name='inventory_detail'),
    path('<int:inventory_id>/receive/', inventory_receive_view, name='inventory_receive'),
    path('<int:inventory_id>/adjust/', inventory_adjust_view, name='inventory_adjust'),
    path('<int:inventory_id>/threshold/', inventory_update_threshold_view, name='inventory_update_threshold'),
]
