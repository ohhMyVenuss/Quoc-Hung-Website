from django.db import models, router, transaction
from django.db.models import Q, F
from django.core.exceptions import ValidationError


def normalize_category_name(raw_name):

    if not raw_name:
        return ""
    return " ".join(raw_name.strip().split())


class Category(models.Model):

    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Tên danh mục",
        help_text="Tối đa 100 ký tự. Phải là duy nhất sau khi chuẩn hóa khoảng trắng và chữ hoa/thường."
    )
    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Mô tả danh mục"
    )
    is_visible = models.BooleanField(
        default=True,
        verbose_name="Hiển thị danh mục",
        help_text="Mặc định True (1). Nếu False (0), danh mục bị ẩn khỏi khách hàng nhưng vẫn hiển thị với nhân viên quản lý."
    )

    class Meta:
        db_table = 'catalog_category'
        verbose_name = 'Danh mục sản phẩm'
        verbose_name_plural = 'Danh mục sản phẩm'
        ordering = ['id']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        normalized = normalize_category_name(self.name)
        if not normalized:
            raise ValidationError({'name': 'Tên danh mục không được để trống hoặc chỉ chứa khoảng trắng.'})

        norm_key = normalized.casefold()
        qs = Category.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        for other in qs:
            if normalize_category_name(other.name).casefold() == norm_key:
                raise ValidationError({
                    'name': f"Tên danh mục '{normalized}' đã tồn tại trong hệ thống (trùng lặp sau khi chuẩn hóa)."
                })

    def save(self, *args, **kwargs):
        if self.name:
            self.name = normalize_category_name(self.name)
        self.full_clean()
        super().save(*args, **kwargs)


class Product(models.Model):

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products',
        verbose_name="Danh mục sản phẩm"
    )
    name = models.CharField(
        max_length=150,
        verbose_name="Tên sản phẩm",
        help_text="Tối đa 150 ký tự. Bắt buộc nhập."
    )
    brand = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Thương hiệu",
        help_text="Thương hiệu sản phẩm (ví dụ: Apple, Samsung, Xiaomi...). Tùy chọn."
    )
    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Mô tả sản phẩm",
        help_text="Mô tả chi tiết đặc điểm và thông số cơ bản của sản phẩm. Tùy chọn."
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Kích hoạt kinh doanh",
        help_text="Mặc định True (1). Nếu False (0), sản phẩm tạm ngưng bán và bị ẩn khỏi giao diện khách hàng."
    )
    is_deleted = models.BooleanField(
        default=False,
        verbose_name="Đã xóa mềm",
        help_text="Mặc định False (0). Đánh dấu sản phẩm đã bị xóa mềm bởi Quản trị viên."
    )
    deleted_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Thời điểm xóa mềm",
        help_text="Thời điểm Quản trị viên thực hiện xóa mềm sản phẩm."
    )

    class Meta:
        db_table = 'catalog_product'
        verbose_name = 'Sản phẩm'
        verbose_name_plural = 'Sản phẩm'
        ordering = ['-id']

    def __str__(self):
        return f"{self.product_code} - {self.name}"

    @property
    def product_code(self):
        if self.id:
            return f"SP{self.id:04d}"
        return "SP----"

    def clean(self):
        super().clean()
        if self.name:
            self.name = " ".join(self.name.strip().split())
            if not self.name:
                raise ValidationError({'name': 'Tên sản phẩm không được để trống hoặc chỉ chứa khoảng trắng.'})
        else:
            raise ValidationError({'name': 'Tên sản phẩm không được để trống.'})

        if self.brand:
            self.brand = " ".join(self.brand.strip().split())
            if not self.brand:
                self.brand = None

    def save(self, *args, **kwargs):
        if self.name:
            self.name = " ".join(self.name.strip().split())
        if self.brand:
            self.brand = " ".join(self.brand.strip().split())
            if not self.brand:
                self.brand = None
        self.full_clean()
        super().save(*args, **kwargs)


class ProductImage(models.Model):

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='images',
        verbose_name="Sản phẩm"
    )
    variant = models.ForeignKey(
        'ProductVariant',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='images',
        verbose_name="Biến thể liên kết",
        help_text="Biến thể cụ thể gắn với ảnh này (để trống nếu là ảnh chung của sản phẩm)."
    )
    image = models.ImageField(
        upload_to='products/%Y/%m/',
        verbose_name="Tập tin hình ảnh",
        help_text="Tập tin ảnh hợp lệ (.jpg, .jpeg, .png, .webp). Tối đa 5MB."
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        verbose_name="Thứ tự hiển thị",
        help_text="Thứ tự sắp xếp (số nhỏ hơn hiển thị trước, 0 là ảnh đại diện chính)."
    )

    class Meta:
        db_table = 'catalog_productimage'
        verbose_name = 'Ảnh sản phẩm'
        verbose_name_plural = 'Ảnh sản phẩm'
        ordering = ['sort_order', 'id']

    def __str__(self):
        var_str = f" ({self.variant.sku})" if self.variant_id and self.variant else " (Ảnh chung)"
        return f"Ảnh #{self.id} của {self.product.product_code}{var_str} (thứ tự: {self.sort_order})"

    def clean(self):
        super().clean()
        if hasattr(self, 'product') and self.product_id:
            try:
                if self.product.is_deleted:
                    raise ValidationError('Không thể tạo mới hoặc chỉnh sửa ảnh cho sản phẩm đã bị xóa mềm.')
            except Product.DoesNotExist:
                pass

        if self.variant_id:
            var_obj = self.variant if (hasattr(self, 'variant') and self.variant) else None
            if var_obj and hasattr(self, 'product') and self.product_id:
                if var_obj.product_id != self.product_id:
                    raise ValidationError({'variant': 'Biến thể được chọn không thuộc sản phẩm này.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


def normalize_attribute_text(raw_text):

    if not raw_text:
        return ""
    return " ".join(raw_text.strip().split())


class Attribute(models.Model):

    name = models.CharField(
        max_length=80,
        unique=True,
        verbose_name="Tên thuộc tính",
        help_text="Tối đa 80 ký tự. Tên thuộc tính duy nhất trong hệ thống."
    )

    class Meta:
        db_table = 'catalog_attribute'
        verbose_name = 'Thuộc tính sản phẩm'
        verbose_name_plural = 'Thuộc tính sản phẩm'
        ordering = ['name']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        normalized = normalize_attribute_text(self.name)
        if not normalized:
            raise ValidationError({'name': 'Tên thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.'})
        if len(normalized) > 80:
            raise ValidationError({'name': 'Tên thuộc tính không được vượt quá 80 ký tự.'})

        norm_key = normalized.casefold()
        qs = Attribute.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        for other in qs:
            if normalize_attribute_text(other.name).casefold() == norm_key:
                raise ValidationError({
                    'name': f"Tên thuộc tính '{normalized}' đã tồn tại trong hệ thống (trùng lặp sau khi chuẩn hóa)."
                })

    def save(self, *args, **kwargs):
        if self.name:
            self.name = normalize_attribute_text(self.name)
        self.full_clean()
        super().save(*args, **kwargs)


class AttributeValue(models.Model):

    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.PROTECT,
        related_name='values',
        verbose_name="Thuộc tính cha",
        help_text="Thuộc tính cha định nghĩa giá trị này."
    )
    value = models.CharField(
        max_length=80,
        verbose_name="Giá trị thuộc tính",
        help_text="Tối đa 80 ký tự. Duy nhất trong phạm vi thuộc tính cha."
    )

    class Meta:
        db_table = 'catalog_attributevalue'
        verbose_name = 'Giá trị thuộc tính'
        verbose_name_plural = 'Giá trị thuộc tính'
        ordering = ['attribute', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['attribute', 'value'],
                name='unique_attribute_value_per_attribute'
            )
        ]

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"

    def clean(self):
        super().clean()
        normalized = normalize_attribute_text(self.value)
        if not normalized:
            raise ValidationError({'value': 'Giá trị thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.'})
        if len(normalized) > 80:
            raise ValidationError({'value': 'Giá trị thuộc tính không được vượt quá 80 ký tự.'})

        if hasattr(self, 'attribute') and self.attribute_id:
            norm_key = normalized.casefold()
            qs = AttributeValue.objects.filter(attribute=self.attribute)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            for other in qs:
                if normalize_attribute_text(other.value).casefold() == norm_key:
                    raise ValidationError({
                        'value': f"Giá trị '{normalized}' đã tồn tại trong thuộc tính '{self.attribute.name}'."
                    })

    def save(self, *args, **kwargs):
        if self.value:
            self.value = normalize_attribute_text(self.value)
        self.full_clean()
        super().save(*args, **kwargs)


def derive_combination_key(attribute_value_ids):

    if not attribute_value_ids:
        return ""
    clean_ids = sorted(set(int(vid) for vid in attribute_value_ids if vid is not None and str(vid).strip() != ""))
    if not clean_ids:
        return ""
    key = ",".join(str(vid) for vid in clean_ids)
    if len(key) > 255:
        raise ValidationError(f"Khóa tổ hợp thuộc tính vượt quá giới hạn 255 ký tự ({len(key)} ký tự).")
    return key


class ProductVariant(models.Model):

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants',
        verbose_name="Sản phẩm cơ sở",
        help_text="Sản phẩm cơ sở định nghĩa biến thể này."
    )
    sku = models.CharField(
        max_length=60,
        unique=True,
        verbose_name="Mã SKU",
        help_text="Mã SKU duy nhất toàn hệ thống (tối đa 60 ký tự)."
    )
    price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="Giá bán",
        help_text="Giá bán hiện tại của biến thể (không âm)."
    )
    original_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Giá gốc",
        help_text="Giá gốc/niêm yết ban đầu (tùy chọn, nếu có phải >= giá bán)."
    )
    combination_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        editable=False,
        verbose_name="Khóa tổ hợp thuộc tính",
        help_text="Khóa tổ hợp sắp xếp theo ID giá trị thuộc tính (tự động tạo)."
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Kích hoạt kinh doanh",
        help_text="Mặc định True (1). Nếu False (0), biến thể tạm ngưng kinh doanh."
    )

    class Meta:
        db_table = 'catalog_productvariant'
        verbose_name = 'Biến thể sản phẩm'
        verbose_name_plural = 'Biến thể sản phẩm'
        ordering = ['product', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'combination_key'],
                name='unique_combination_per_product'
            ),
            models.CheckConstraint(
                condition=Q(price__gte=0),
                name='check_variant_price_non_negative'
            ),
            models.CheckConstraint(
                condition=Q(original_price__isnull=True) | Q(original_price__gte=F('price')),
                name='check_variant_original_price_gte_price'
            ),
        ]

    @property
    def attribute_summary(self):

        if not self.pk:
            return "Mặc định"
        links = self.attribute_values.select_related('attribute_value').order_by('attribute_id', 'id')
        vals = [link.attribute_value.value for link in links]
        return ", ".join(vals) if vals else "Mặc định"

    def __str__(self):
        summary = self.attribute_summary
        if summary and summary != "Mặc định":
            return f"{self.product.name} ({self.sku} - {summary})"
        return f"{self.product.name} ({self.sku})"

    def clean(self):
        super().clean()

        if self.sku is not None:
            self.sku = self.sku.strip().upper()
        if not self.sku:
            raise ValidationError({'sku': 'Mã SKU không được để trống hoặc chỉ chứa khoảng trắng.'})
        if len(self.sku) > 60:
            raise ValidationError({'sku': 'Mã SKU không được vượt quá 60 ký tự.'})

        sku_qs = ProductVariant.objects.all()
        if self.pk:
            sku_qs = sku_qs.exclude(pk=self.pk)
        for other in sku_qs:
            if (other.sku or '').strip().upper() == self.sku:
                raise ValidationError({'sku': f"Mã SKU '{self.sku}' đã tồn tại trong hệ thống."})

        if self.price is None:
            raise ValidationError({'price': 'Giá bán không được để trống.'})
        if self.price < 0:
            raise ValidationError({'price': 'Giá bán không được âm.'})
        if self.original_price is not None and self.original_price < self.price:
            raise ValidationError({'original_price': 'Giá gốc phải lớn hơn hoặc bằng giá bán.'})

        if hasattr(self, 'product') and self.product:
            if self.product.is_deleted:
                raise ValidationError({'product': 'Không thể tạo hoặc chỉnh sửa biến thể của sản phẩm đã bị xóa mềm.'})
            if self.pk:
                orig_prod_id = ProductVariant.objects.filter(pk=self.pk).values_list('product_id', flat=True).first()
                if orig_prod_id and orig_prod_id != self.product_id:
                    raise ValidationError({'product': 'Không được phép thay đổi sản phẩm cha của biến thể.'})

        if hasattr(self, 'product') and self.product_id:
            siblings = ProductVariant.objects.filter(product_id=self.product_id)
            if self.pk:
                siblings = siblings.exclude(pk=self.pk)

            if self.combination_key == "":
                if siblings.filter(combination_key="").exists():
                    raise ValidationError({'combination_key': 'Sản phẩm đã có biến thể mặc định không thuộc tính.'})
                if siblings.exclude(combination_key="").exists():
                    raise ValidationError({
                        'combination_key': (
                            'Không thể tạo biến thể không có thuộc tính vì sản phẩm đã có các biến thể theo thuộc tính. '
                            'Không được pha trộn giữa biến thể có thuộc tính và không có thuộc tính trong cùng một sản phẩm.'
                        )
                    })
            else:
                if siblings.filter(combination_key="").exists():
                    raise ValidationError({
                        'combination_key': (
                            'Không thể tạo biến thể có thuộc tính vì sản phẩm đang sử dụng biến thể mặc định không thuộc tính. '
                            'Vui lòng chuyển đổi hoặc xử lý biến thể hiện có trước khi thêm biến thể theo thuộc tính.'
                        )
                    })
                if siblings.filter(combination_key=self.combination_key).exists():
                    raise ValidationError({
                        'combination_key': f"Tổ hợp thuộc tính này đã tồn tại cho sản phẩm '{self.product.name}'."
                    })

    def save(self, *args, **kwargs):
        if self.sku:
            self.sku = self.sku.strip().upper()
        self.full_clean()
        using = kwargs.get('using') or router.db_for_write(self.__class__, instance=self)
        with transaction.atomic(using=using):
            super().save(*args, **kwargs)


class VariantAttributeValue(models.Model):

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name='attribute_values',
        verbose_name="Biến thể",
        help_text="Biến thể sở hữu giá trị thuộc tính này."
    )
    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.PROTECT,
        related_name='variant_links',
        verbose_name="Thuộc tính",
        help_text="Thuộc tính định nghĩa (Màu sắc, Dung lượng...)."
    )
    attribute_value = models.ForeignKey(
        AttributeValue,
        on_delete=models.PROTECT,
        related_name='variant_value_links',
        verbose_name="Giá trị thuộc tính",
        help_text="Giá trị thuộc tính cụ thể gắn với biến thể."
    )

    class Meta:
        db_table = 'catalog_variantattributevalue'
        verbose_name = 'Giá trị thuộc tính của biến thể'
        verbose_name_plural = 'Giá trị thuộc tính của biến thể'
        ordering = ['variant', 'attribute']
        constraints = [
            models.UniqueConstraint(
                fields=['variant', 'attribute'],
                name='unique_attribute_per_variant'
            ),
        ]

    def __str__(self):
        return f"{self.variant.sku} - {self.attribute.name}: {self.attribute_value.value}"

    def clean(self):
        super().clean()
        if self.attribute_id and self.attribute_value_id:
            if self.attribute_value.attribute_id != self.attribute_id:
                raise ValidationError({
                    'attribute_value': (
                        f"Giá trị '{self.attribute_value.value}' không thuộc về thuộc tính '{self.attribute.name}'."
                    )
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


