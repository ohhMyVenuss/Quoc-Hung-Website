from django.contrib import admin
from django.urls import path, include
from config.views import test_page
from catalog.views import homepage_view

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', homepage_view, name='home'),
    path('admin/', admin.site.urls),
    path('test/', test_page, name='bootstrap_test'),
    path('accounts/', include('accounts.urls')),
    path('catalog/', include('catalog.urls')),
    path('inventory/', include('inventory.urls')),
    path('cart/', include('cart.urls', namespace='cart')),
    path('orders/', include('orders.urls', namespace='orders')),
    path('payments/', include('payments.urls', namespace='payments')),
    path('management/payments/', include('payments.urls', namespace='payments_mgmt')),
    path('management/coupons/', include('coupons.urls')),
    path('chat/', include('chat.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

