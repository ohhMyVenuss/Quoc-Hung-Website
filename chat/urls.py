from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('faq/<int:faq_id>/', views.chat_ask_faq_view, name='ask_faq'),
    path('send/', views.chat_send_view, name='send'),
    path('close/', views.chat_close_view, name='close'),
    path('staff/', views.staff_inbox_view, name='staff_inbox'),
    path('staff/<int:conversation_id>/', views.staff_conversation_view, name='staff_conversation'),
    path('staff/<int:conversation_id>/close/', views.staff_close_view, name='staff_close'),
]
