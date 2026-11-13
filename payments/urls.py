from django.urls import path
from payments import views

app_name = 'payments'

urlpatterns = [
    path('', views.payment_verification_list_view, name='verification_list'),
    path('verification/', views.payment_verification_list_view, name='verification_list_alt'),
    path('<int:pk>/verify/', views.payment_verify_view, name='verify_payment'),
    path('reconciliations/', views.reconciliation_list_view, name='reconciliation_list'),
    path('reconciliations/<int:pk>/', views.reconciliation_detail_view, name='reconciliation_detail'),
    path('reconciliations/<int:pk>/resolve-expired/', views.resolve_expired_reconciliation_view, name='resolve_expired_reconciliation'),
    path('reconciliations/<int:pk>/resolve-valid-transfer/', views.resolve_valid_transfer_reconciliation_view, name='resolve_valid_transfer_reconciliation'),
    path('reconciliations/<int:pk>/resolve-terminal-transfer/', views.resolve_terminal_transfer_reconciliation_view, name='resolve_terminal_transfer_reconciliation'),
    path('reconciliations/<int:pk>/record-external-refund/', views.record_external_refund_view, name='record_external_refund'),
    path('<int:pk>/open-reconciliation/', views.open_reconciliation_view, name='open_reconciliation'),
]

