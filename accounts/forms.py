import re
from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import User, Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q

from accounts.models import CustomerProfile
from accounts.permissions import (
    ROLE_CUSTOMER,
    ROLE_SALES_STAFF,
    ROLE_INVENTORY_STAFF,
)


def normalize_phone_number(raw_phone):
    if not raw_phone:
        return ''
    cleaned = re.sub(r'[\s.\-]', '', str(raw_phone).strip())
    if cleaned.startswith('+84'):
        cleaned = '0' + cleaned[3:]
    return cleaned


def validate_vietnamese_mobile(phone_raw):

    phone_cleaned = normalize_phone_number(phone_raw)
    if not re.match(r'^0[35789]\d{8}$', phone_cleaned):
        raise ValidationError('Số điện thoại không hợp lệ. Vui lòng nhập số điện thoại Việt Nam gồm 10 chữ số (ví dụ: 0982805704).')
    return phone_cleaned


def check_username_collides_with_email(username, exclude_user_id=None):
    qs = User.objects.filter(email__iexact=username)
    if exclude_user_id:
        qs = qs.exclude(id=exclude_user_id)
    return qs.exists()


def check_username_collides_with_phone(username, exclude_user_id=None):
    norm_phone = normalize_phone_number(username)
    if re.match(r'^0[0-9]{9}$', norm_phone):
        qs = CustomerProfile.objects.filter(phone=norm_phone)
        if exclude_user_id:
            qs = qs.exclude(user_id=exclude_user_id)
        return qs.exists()
    return False


def check_phone_collides_with_username(phone_cleaned, exclude_user_id=None):
    if not phone_cleaned:
        return False

    qs = User.objects.all()
    if exclude_user_id:
        qs = qs.exclude(id=exclude_user_id)

    if qs.filter(username__iexact=phone_cleaned).exists():
        return True
    for uname in qs.values_list('username', flat=True):
        if uname and normalize_phone_number(uname) == phone_cleaned:
            return True

    return False


def check_email_collides_with_username(email, exclude_user_id=None):
    qs = User.objects.filter(username__iexact=email)
    if exclude_user_id:
        qs = qs.exclude(id=exclude_user_id)
    return qs.exists()


class CustomerRegistrationForm(forms.Form):
    username = forms.CharField(
        label='Tên đăng nhập',
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'id': 'id_username',
            'placeholder': 'nguyenvana_qh',
            'class': 'form-input',
            'autocomplete': 'username',
        }),
        error_messages={
            'required': 'Vui lòng nhập tên đăng nhập.',
            'max_length': 'Tên đăng nhập không được vượt quá 150 ký tự.',
        }
    )

    email = forms.CharField(
        label='Email',
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={
            'id': 'id_email',
            'placeholder': 'Ví dụ: example@gmail.com',
            'class': 'form-input',
            'autocomplete': 'email',
        }),
    )

    phone = forms.CharField(
        label='Số điện thoại',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_phone',
            'placeholder': 'Ví dụ: 0982805704',
            'class': 'form-input',
            'autocomplete': 'tel',
        }),
    )

    password = forms.CharField(
        label='Mật khẩu',
        required=True,
        widget=forms.PasswordInput(attrs={
            'id': 'id_password',
            'placeholder': '••••••••••••',
            'class': 'form-input',
            'autocomplete': 'new-password',
        }),
        error_messages={
            'required': 'Vui lòng nhập mật khẩu.',
        }
    )

    password_confirm = forms.CharField(
        label='Nhập lại mật khẩu',
        required=True,
        widget=forms.PasswordInput(attrs={
            'id': 'id_password_confirm',
            'placeholder': '••••••••••••',
            'class': 'form-input',
            'autocomplete': 'new-password',
        }),
        error_messages={
            'required': 'Vui lòng nhập lại mật khẩu.',
        }
    )

    terms_agreed = forms.BooleanField(
        label='Tôi đồng ý với Điều khoản dịch vụ và Chính sách bảo mật',
        required=True,
        widget=forms.CheckboxInput(attrs={
            'id': 'id_terms_agreed',
            'class': 'form-checkbox',
        }),
        error_messages={
            'required': 'Bạn phải đồng ý với Điều khoản dịch vụ và Chính sách bảo mật để tiếp tục.',
        }
    )

    def __init__(self, *args, **kwargs):
        if args and isinstance(args[0], dict) and 'contact' in args[0] and 'email' not in args[0] and 'phone' not in args[0]:
            data = args[0].copy()
            contact_val = data.get('contact', '').strip()
            if '@' in contact_val:
                data['email'] = contact_val
            else:
                data['phone'] = contact_val
            args = (data,) + args[1:]
        elif 'data' in kwargs and isinstance(kwargs['data'], dict) and 'contact' in kwargs['data'] and 'email' not in kwargs['data'] and 'phone' not in kwargs['data']:
            data = kwargs['data'].copy()
            contact_val = data.get('contact', '').strip()
            if '@' in contact_val:
                data['email'] = contact_val
            else:
                data['phone'] = contact_val
            kwargs['data'] = data
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if not username:
            raise ValidationError('Tên đăng nhập không được để trống.')
        if len(username) < 3:
            raise ValidationError('Tên đăng nhập phải có ít nhất 3 ký tự.')
        if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
            raise ValidationError('Tên đăng nhập chỉ được chứa chữ cái, số, dấu gạch dưới, gạch ngang hoặc dấu chấm.')
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('Tên đăng nhập này đã được sử dụng. Vui lòng chọn tên khác.')
        if check_username_collides_with_email(username):
            raise ValidationError('Tên đăng nhập này trùng với email của một tài khoản khác.')
        if check_username_collides_with_phone(username):
            raise ValidationError('Tên đăng nhập này trùng với số điện thoại của một tài khoản khác.')
        return username

    def clean_email(self):
        email_raw = self.cleaned_data.get('email', '').strip()
        if not email_raw:
            return ''

        email = email_raw.lower()
        try:
            validate_email(email)
        except ValidationError:
            raise ValidationError('Địa chỉ email không hợp lệ.')

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Địa chỉ email này đã được đăng ký tài khoản.')

        if check_email_collides_with_username(email):
            raise ValidationError('Địa chỉ email này trùng với tên đăng nhập của một tài khoản khác.')

        return email

    def clean_phone(self):
        phone_raw = self.cleaned_data.get('phone', '').strip()
        if not phone_raw:
            return ''
        phone_cleaned = validate_vietnamese_mobile(phone_raw)

        if CustomerProfile.objects.filter(phone=phone_cleaned).exists():
            raise ValidationError('Số điện thoại này đã được đăng ký tài khoản.')

        if check_phone_collides_with_username(phone_cleaned):
            raise ValidationError('Số điện thoại này trùng với tên đăng nhập của một tài khoản khác.')
        if User.objects.filter(email__iexact=phone_cleaned).exists():
            raise ValidationError('Số điện thoại này trùng với email của một tài khoản khác.')

        return phone_cleaned

    def clean_password(self):
        password = self.cleaned_data.get('password', '')
        if len(password) < 8:
            raise ValidationError('Mật khẩu phải có độ dài tối thiểu 8 ký tự.')
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')

        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'Mật khẩu nhập lại không khớp.')

        has_email = bool(self.data.get('email', '').strip())
        has_phone = bool(self.data.get('phone', '').strip())
        if not has_email and not has_phone:
            msg = 'Vui lòng cung cấp ít nhất một phương thức liên hệ (email hoặc số điện thoại).'
            self.add_error('email', msg)
            self.add_error('phone', msg)

        return cleaned_data

    def save(self):

        username = self.cleaned_data['username']
        email = self.cleaned_data.get('email', '')
        phone = self.cleaned_data.get('phone', '') or None
        password = self.cleaned_data['password']

        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=False,
                is_superuser=False,
                is_active=True,
            )

            customer_group, _ = Group.objects.get_or_create(name=ROLE_CUSTOMER)
            user.groups.add(customer_group)

            CustomerProfile.objects.create(
                user=user,
                phone=phone,
                default_address=None,
            )

        return user


class LoginForm(forms.Form):

    identifier = forms.CharField(
        label='Tên đăng nhập / Email / SĐT',
        required=True,
        widget=forms.TextInput(attrs={
            'id': 'id_identifier',
            'placeholder': 'Tên đăng nhập, email hoặc số điện thoại',
            'class': 'form-input',
            'autocomplete': 'username',
        }),
        error_messages={
            'required': 'Vui lòng nhập tên đăng nhập, email hoặc số điện thoại.',
        }
    )

    password = forms.CharField(
        label='Mật khẩu',
        required=True,
        widget=forms.PasswordInput(attrs={
            'id': 'id_password',
            'placeholder': '••••••••••••',
            'class': 'form-input',
            'autocomplete': 'current-password',
        }),
        error_messages={
            'required': 'Vui lòng nhập mật khẩu.',
        }
    )

    remember_me = forms.BooleanField(
        label='Ghi nhớ đăng nhập trên thiết bị này',
        required=False,
        widget=forms.CheckboxInput(attrs={
            'id': 'id_remember_me',
            'class': 'form-checkbox',
        }),
    )

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        raw_identifier = cleaned_data.get('identifier', '')
        password = cleaned_data.get('password', '')

        identifier = raw_identifier.strip()
        if not identifier or not password:
            return cleaned_data

        users_by_username = list(User.objects.filter(username__iexact=identifier))

        users_by_email = list(User.objects.filter(email__iexact=identifier))

        phone_cleaned = normalize_phone_number(identifier)

        if re.match(r'^0[0-9]{9}$', phone_cleaned):
            users_by_phone = list(User.objects.filter(customer_profile__phone=phone_cleaned))
        else:
            users_by_phone = []

        matched_users_dict = {}
        for u in users_by_username + users_by_email + users_by_phone:
            matched_users_dict[u.id] = u

        if len(matched_users_dict) != 1:
            raise ValidationError('Thông tin đăng nhập hoặc mật khẩu không chính xác.')

        target_user = list(matched_users_dict.values())[0]

        authenticated_user = authenticate(
            self.request,
            username=target_user.username,
            password=password
        )

        if authenticated_user is None:
            raise ValidationError('Thông tin đăng nhập hoặc mật khẩu không chính xác.')

        self.user_cache = authenticated_user
        return cleaned_data

    def get_user(self):
        return self.user_cache


class CustomerProfileForm(forms.Form):

    full_name = forms.CharField(
        label='Họ và tên',
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_full_name',
            'placeholder': 'Ví dụ: Nguyễn Văn A',
            'class': 'form-input',
            'autocomplete': 'name',
        }),
        error_messages={
            'max_length': 'Họ và tên không được vượt quá 150 ký tự.',
        }
    )

    email = forms.CharField(
        label='Email',
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={
            'id': 'id_email',
            'placeholder': 'nguyenvana@gmail.com',
            'class': 'form-input',
            'autocomplete': 'email',
        }),
        error_messages={
            'max_length': 'Địa chỉ email không được vượt quá 254 ký tự.',
        }
    )

    phone = forms.CharField(
        label='Số điện thoại',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_phone',
            'placeholder': '0982 805 704',
            'class': 'form-input',
            'autocomplete': 'tel',
        }),
        error_messages={
            'max_length': 'Số điện thoại không được vượt quá 20 ký tự.',
        }
    )

    default_address = forms.CharField(
        label='Địa chỉ mặc định',
        required=False,
        widget=forms.Textarea(attrs={
            'id': 'id_default_address',
            'placeholder': 'Số nhà, tên đường, phường/xã, quận/huyện, tỉnh/thành phố...',
            'class': 'form-input form-textarea',
            'rows': 3,
        }),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if self.user:
            profile = getattr(self.user, 'customer_profile', None)
            if profile is None:
                profile = CustomerProfile.objects.filter(user=self.user).first()

            if 'email' not in self.initial and self.user.email:
                self.initial['email'] = self.user.email
            if 'full_name' not in self.initial:
                name_parts = [self.user.last_name, self.user.first_name]
                full_name_val = " ".join([p for p in name_parts if p]).strip()
                if full_name_val:
                    self.initial['full_name'] = full_name_val
            if profile:
                if 'phone' not in self.initial and profile.phone:
                    self.initial['phone'] = profile.phone
                if 'default_address' not in self.initial and profile.default_address:
                    self.initial['default_address'] = profile.default_address

    def clean_full_name(self):
        full_name = self.cleaned_data.get('full_name', '').strip()
        return full_name

    def clean_email(self):
        email_raw = self.cleaned_data.get('email', '').strip()
        if not email_raw:
            return ''

        email = email_raw.lower()
        try:
            validate_email(email)
        except ValidationError:
            raise ValidationError('Địa chỉ email không hợp lệ.')

        query = User.objects.filter(email__iexact=email)
        if self.user:
            query = query.exclude(id=self.user.id)
        if query.exists():
            raise ValidationError('Địa chỉ email này đã được sử dụng bởi một tài khoản khác.')

        username_query = User.objects.filter(username__iexact=email)
        if self.user:
            username_query = username_query.exclude(id=self.user.id)
        if username_query.exists():
            raise ValidationError('Địa chỉ email này trùng với tên đăng nhập của một tài khoản khác.')

        return email

    def clean_phone(self):
        phone_raw = self.cleaned_data.get('phone', '').strip()
        if not phone_raw:
            return ''

        phone_cleaned = validate_vietnamese_mobile(phone_raw)

        profile_query = CustomerProfile.objects.filter(phone=phone_cleaned)
        if self.user:
            profile_query = profile_query.exclude(user_id=self.user.id)
        if profile_query.exists():
            raise ValidationError('Số điện thoại này đã được đăng ký cho một tài khoản khác.')

        user_id = self.user.id if self.user else None
        if check_phone_collides_with_username(phone_cleaned, exclude_user_id=user_id):
            raise ValidationError('Số điện thoại này trùng với tên đăng nhập của một tài khoản khác.')

        email_query = User.objects.filter(email__iexact=phone_cleaned)
        if self.user:
            email_query = email_query.exclude(id=self.user.id)
        if email_query.exists():
            raise ValidationError('Số điện thoại này trùng với email của một tài khoản khác.')

        return phone_cleaned

    def clean_default_address(self):
        address = self.cleaned_data.get('default_address', '').strip()
        return address

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email', '')
        phone = cleaned_data.get('phone', '')

        if 'first_name' in self.data:
            cleaned_data['first_name'] = self.data.get('first_name', '').strip()
        if 'last_name' in self.data:
            cleaned_data['last_name'] = self.data.get('last_name', '').strip()

        if not email and not phone:
            raise ValidationError('Vui lòng cung cấp ít nhất một phương thức liên hệ (email hoặc số điện thoại).')

        return cleaned_data

    def save(self):

        if not self.user:
            raise ValueError('Yêu cầu truyền đối tượng user hợp lệ để lưu hồ sơ.')

        email = self.cleaned_data.get('email', '')
        phone = self.cleaned_data.get('phone', '') or None
        default_address = self.cleaned_data.get('default_address', '') or None
        full_name = self.cleaned_data.get('full_name', '')

        with transaction.atomic():
            user = self.user
            user.email = email

            if 'first_name' in self.cleaned_data or 'last_name' in self.cleaned_data:
                user.first_name = self.cleaned_data.get('first_name', user.first_name)
                user.last_name = self.cleaned_data.get('last_name', user.last_name)
            elif full_name:
                parts = full_name.rsplit(' ', 1)
                if len(parts) == 2:
                    user.last_name = parts[0]
                    user.first_name = parts[1]
                else:
                    user.last_name = ''
                    user.first_name = parts[0]
            else:
                user.first_name = ''
                user.last_name = ''

            user.save(update_fields=['email', 'first_name', 'last_name'])

            profile = getattr(user, 'customer_profile', None)
            if profile is None:
                profile = CustomerProfile.objects.filter(user=user).first()
            if profile is None:
                profile = CustomerProfile(user=user)

            profile.phone = phone
            profile.default_address = default_address
            profile.save()

        return user, profile


class StaffCreationForm(forms.Form):
    username = forms.CharField(
        label='Tên đăng nhập',
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'id': 'id_username',
            'placeholder': 'nv_banhang01',
            'class': 'form-input',
            'autocomplete': 'username',
        }),
        error_messages={
            'required': 'Vui lòng nhập tên đăng nhập.',
            'max_length': 'Tên đăng nhập không được vượt quá 150 ký tự.',
        }
    )

    first_name = forms.CharField(
        label='Tên',
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_first_name',
            'placeholder': 'Hùng',
            'class': 'form-input',
            'autocomplete': 'given-name',
        }),
    )

    last_name = forms.CharField(
        label='Họ và tên đệm',
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_last_name',
            'placeholder': 'Nguyễn Quốc',
            'class': 'form-input',
            'autocomplete': 'family-name',
        }),
    )

    email = forms.CharField(
        label='Địa chỉ email',
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={
            'id': 'id_email',
            'placeholder': 'nhanvien@quochungmobile.vn',
            'class': 'form-input',
            'autocomplete': 'email',
        }),
    )

    password = forms.CharField(
        label='Mật khẩu khởi tạo',
        required=True,
        widget=forms.PasswordInput(attrs={
            'id': 'id_password',
            'placeholder': '••••••••••••',
            'class': 'form-input',
            'autocomplete': 'new-password',
        }),
        error_messages={
            'required': 'Vui lòng nhập mật khẩu khởi tạo.',
        }
    )

    password_confirm = forms.CharField(
        label='Xác nhận mật khẩu',
        required=True,
        widget=forms.PasswordInput(attrs={
            'id': 'id_password_confirm',
            'placeholder': '••••••••••••',
            'class': 'form-input',
            'autocomplete': 'new-password',
        }),
        error_messages={
            'required': 'Vui lòng xác nhận lại mật khẩu.',
        }
    )

    roles = forms.MultipleChoiceField(
        label='Phân quyền vai trò nhân viên',
        choices=[
            (ROLE_SALES_STAFF, 'Nhân viên bán hàng (Sales Staff)'),
            (ROLE_INVENTORY_STAFF, 'Nhân viên kho (Inventory Staff)'),
        ],
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'form-checkbox-role',
        }),
        required=True,
        error_messages={
            'required': 'Vui lòng chọn ít nhất một vai trò nhân viên hợp lệ.',
        }
    )

    is_active = forms.BooleanField(
        label='Kích hoạt tài khoản ngay khi tạo',
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'id': 'id_is_active',
            'class': 'form-checkbox',
        }),
    )

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if not username:
            raise ValidationError('Tên đăng nhập không được để trống.')
        if len(username) < 3:
            raise ValidationError('Tên đăng nhập phải có ít nhất 3 ký tự.')
        if check_username_collides_with_email(username):
            raise ValidationError('Tên đăng nhập này trùng với email của một tài khoản khác.')
        if check_username_collides_with_phone(username):
            raise ValidationError('Tên đăng nhập này trùng với số điện thoại của một tài khoản khác.')
        if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
            raise ValidationError('Tên đăng nhập chỉ được chứa chữ cái, số, dấu gạch dưới, gạch ngang hoặc dấu chấm.')
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('Tên đăng nhập này đã được sử dụng. Vui lòng chọn tên khác.')
        return username

    def clean_email(self):
        email_raw = self.cleaned_data.get('email', '').strip()
        if not email_raw:
            return ''
        email = email_raw.lower()
        try:
            validate_email(email)
        except ValidationError:
            raise ValidationError('Địa chỉ email không hợp lệ.')
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Địa chỉ email này đã được sử dụng bởi một tài khoản khác.')
        if User.objects.filter(username__iexact=email).exists():
            raise ValidationError('Địa chỉ email này trùng với tên đăng nhập của một tài khoản khác.')
        return email

    def clean_password(self):
        password = self.cleaned_data.get('password', '')
        if len(password) < 8:
            raise ValidationError('Mật khẩu phải có độ dài tối thiểu 8 ký tự.')
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'Mật khẩu và xác nhận mật khẩu không trùng khớp.')

        if password:
            username = cleaned_data.get('username', '').strip()
            email = cleaned_data.get('email', '').strip()
            first_name = cleaned_data.get('first_name', '').strip()
            last_name = cleaned_data.get('last_name', '').strip()
            temp_user = User(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
            try:
                validate_password(password, user=temp_user)
            except ValidationError as error:
                vi_messages = []
                for msg in error.messages:
                    if 'too short' in msg.lower():
                        vi_messages.append('Mật khẩu quá ngắn. Phải chứa ít nhất 8 ký tự.')
                    else:
                        vi_messages.append(msg)
                self.add_error('password', ValidationError(vi_messages))

        roles = cleaned_data.get('roles')
        if not roles:
            self.add_error('roles', 'Vui lòng chọn ít nhất một vai trò nhân viên.')
        return cleaned_data

    def save(self):
        with transaction.atomic():
            username = self.cleaned_data['username']
            email = self.cleaned_data.get('email', '')
            password = self.cleaned_data['password']
            first_name = self.cleaned_data.get('first_name', '').strip()
            last_name = self.cleaned_data.get('last_name', '').strip()
            is_active = self.cleaned_data.get('is_active', True)
            roles = self.cleaned_data.get('roles', [])

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            user.is_active = is_active
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=['is_active', 'is_staff', 'is_superuser'])

            for role_name in roles:
                group, _ = Group.objects.get_or_create(name=role_name)
                user.groups.add(group)

            return user


class StaffEditForm(forms.Form):

    username = forms.CharField(
        label='Tên đăng nhập',
        required=False,
        disabled=True,
        widget=forms.TextInput(attrs={
            'id': 'id_username',
            'class': 'form-input form-input-readonly',
            'readonly': 'readonly',
        }),
    )

    first_name = forms.CharField(
        label='Tên',
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_first_name',
            'placeholder': 'Hùng',
            'class': 'form-input',
            'autocomplete': 'given-name',
        }),
    )

    last_name = forms.CharField(
        label='Họ và tên đệm',
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_last_name',
            'placeholder': 'Nguyễn Quốc',
            'class': 'form-input',
            'autocomplete': 'family-name',
        }),
    )

    email = forms.CharField(
        label='Địa chỉ email',
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={
            'id': 'id_email',
            'placeholder': 'nhanvien@quochungmobile.vn',
            'class': 'form-input',
            'autocomplete': 'email',
        }),
    )

    roles = forms.MultipleChoiceField(
        label='Phân quyền vai trò nhân viên',
        choices=[
            (ROLE_SALES_STAFF, 'Nhân viên bán hàng (Sales Staff)'),
            (ROLE_INVENTORY_STAFF, 'Nhân viên kho (Inventory Staff)'),
        ],
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'form-checkbox-role',
        }),
        required=True,
        error_messages={
            'required': 'Vui lòng chọn ít nhất một vai trò nhân viên hợp lệ.',
        }
    )

    def __init__(self, *args, target_user=None, **kwargs):
        self.target_user = target_user
        super().__init__(*args, **kwargs)
        if self.target_user:
            self.initial['username'] = self.target_user.username
            self.initial['first_name'] = self.target_user.first_name
            self.initial['last_name'] = self.target_user.last_name
            self.initial['email'] = self.target_user.email
            current_roles = list(
                self.target_user.groups.filter(
                    name__in=[ROLE_SALES_STAFF, ROLE_INVENTORY_STAFF]
                ).values_list('name', flat=True)
            )
            self.initial['roles'] = current_roles

    def clean_email(self):
        email_raw = self.cleaned_data.get('email', '').strip()
        if not email_raw:
            return ''
        email = email_raw.lower()
        try:
            validate_email(email)
        except ValidationError:
            raise ValidationError('Địa chỉ email không hợp lệ.')

        query = User.objects.filter(email__iexact=email)
        if self.target_user:
            query = query.exclude(id=self.target_user.id)
        if query.exists():
            raise ValidationError('Địa chỉ email này đã được sử dụng bởi một tài khoản khác.')

        user_query = User.objects.filter(username__iexact=email)
        if self.target_user:
            user_query = user_query.exclude(id=self.target_user.id)
        if user_query.exists():
            raise ValidationError('Địa chỉ email này trùng với tên đăng nhập của một tài khoản khác.')

        return email

    def clean(self):
        cleaned_data = super().clean()
        roles = cleaned_data.get('roles')
        if not roles:
            self.add_error('roles', 'Vui lòng chọn ít nhất một vai trò nhân viên.')
        return cleaned_data

    def save(self):
        with transaction.atomic():
            self.target_user.first_name = self.cleaned_data.get('first_name', '').strip()
            self.target_user.last_name = self.cleaned_data.get('last_name', '').strip()
            self.target_user.email = self.cleaned_data.get('email', '').strip()
            self.target_user.save(update_fields=['first_name', 'last_name', 'email'])

            selected_roles = set(self.cleaned_data.get('roles', []))

            sales_group, _ = Group.objects.get_or_create(name=ROLE_SALES_STAFF)
            inventory_group, _ = Group.objects.get_or_create(name=ROLE_INVENTORY_STAFF)

            if ROLE_SALES_STAFF in selected_roles:
                self.target_user.groups.add(sales_group)
            else:
                self.target_user.groups.remove(sales_group)

            if ROLE_INVENTORY_STAFF in selected_roles:
                self.target_user.groups.add(inventory_group)
            else:
                self.target_user.groups.remove(inventory_group)

            return self.target_user


