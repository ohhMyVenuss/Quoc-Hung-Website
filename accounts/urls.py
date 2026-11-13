from django.urls import path
from accounts.views import (
    register_view,
    login_view,
    logout_view,
    landing_view,
    profile_view,
    profile_edit_view,
    staff_list_view,
    staff_create_view,
    staff_edit_view,
    staff_change_status_view,
)

app_name = 'accounts'

urlpatterns = [
    path('register/', register_view, name='register'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('landing/', landing_view, name='landing'),
    path('profile/', profile_view, name='profile'),
    path('profile/edit/', profile_edit_view, name='profile_edit'),
    path('staff/', staff_list_view, name='staff_list'),
    path('staff/new/', staff_create_view, name='staff_create'),
    path('staff/<int:user_id>/edit/', staff_edit_view, name='staff_edit'),
    path('staff/<int:user_id>/status/', staff_change_status_view, name='staff_change_status'),
]


