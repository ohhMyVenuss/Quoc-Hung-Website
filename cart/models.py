from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum


class Cart(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='carts',
        verbose_name='Khách hàng sở hữu',
        help_text='Tài khoản Khách hàng (Customer) sở hữu giỏ hàng này.',
    )
    session_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        verbose_name='Khóa phiên',
        help_text='Khóa phiên Django session dành cho khách vãng lai.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời điểm tạo',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Cập nhật gần nhất',
    )

    class Meta:
        verbose_name = 'Giỏ hàng'
        verbose_name_plural = 'Danh sách giỏ hàng'
        ordering = ['-updated_at', '-created_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False) | Q(session_key__isnull=False),
                name='cart_user_or_session_required',
            ),
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(user__isnull=False),
                name='unique_active_user_cart',
            ),
            models.UniqueConstraint(
                fields=['session_key'],
                condition=Q(user__isnull=True, session_key__isnull=False),
                name='unique_active_session_cart',
            ),
        ]

    def __str__(self):
        if self.user:
            return f"Giỏ hàng #{self.id} (User: {self.user.username})"
        return f"Giỏ hàng #{self.id} (Session: {self.session_key[:8] if self.session_key else 'N/A'}...)"

    @property
    def total_quantity(self):
        res = self.items.aggregate(total=Sum('quantity'))['total']
        return res if res is not None else 0

    @property
    def line_count(self):
        return self.items.count()


class CartItem(models.Model):

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Giỏ hàng',
    )
    variant = models.ForeignKey(
        'catalog.ProductVariant',
        on_delete=models.PROTECT,
        related_name='cart_items',
        verbose_name='Biến thể sản phẩm',
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Số lượng',
        help_text='Số lượng sản phẩm muốn mua (nguyên dương > 0).',
    )
    price_at_addition = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Đơn giá lúc thêm vào giỏ',
        help_text='Đơn giá tham chiếu lúc đưa vào giỏ để nhận biết thay đổi giá theo BR-CA04-CA06.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời điểm tạo',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Cập nhật lúc',
    )

    class Meta:
        verbose_name = 'Dòng giỏ hàng'
        verbose_name_plural = 'Các dòng giỏ hàng'
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name='cartitem_quantity_gt_0',
            ),
            models.UniqueConstraint(
                fields=['cart', 'variant'],
                name='unique_cart_variant',
            ),
        ]

    def __str__(self):
        return f"Dòng #{self.id} — Giỏ #{self.cart_id} — {self.variant.sku} x {self.quantity}"

    @property
    def current_unit_price(self):
        return self.variant.price

    @property
    def line_total(self):
        if self.variant and self.variant.price is not None:
            return self.variant.price * Decimal(self.quantity)
        return Decimal('0')

    @property
    def has_price_changed(self):
        if self.price_at_addition is None or self.variant is None or self.variant.price is None:
            return False
        return self.variant.price != self.price_at_addition
