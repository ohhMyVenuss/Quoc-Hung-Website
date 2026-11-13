from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from payments.models import ReconciliationReason


class PaymentVerificationForm(forms.Form):
    observed_recipient_account = forms.CharField(
        label="Tài khoản nhận tiền thực tế",
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập đầy đủ số tài khoản nhận tiền thực tế...',
            'id': 'id_observed_recipient_account',
            'autocomplete': 'off',
        }),
        help_text="Số tài khoản ngân hàng thụ hưởng ghi nhận tiền vào (yêu cầu đầy đủ số tài khoản).",
    )

    observed_amount = forms.DecimalField(
        label="Số tiền thực nhận (VNĐ)",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.00'),
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập số tiền thực tế ghi có...',
            'id': 'id_observed_amount',
            'step': '1000',
        }),
        help_text="Số tiền ghi có chính xác trên sao kê (không tự ý làm tròn).",
    )

    observed_transfer_reference = forms.CharField(
        label="Nội dung chuyển khoản thực tế",
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập diễn giải / nội dung chuyển khoản trên sao kê...',
            'id': 'id_observed_transfer_reference',
            'autocomplete': 'off',
        }),
        help_text="Nội dung chuyển tiền do người gửi nhập trên ứng dụng ngân hàng.",
    )

    bank_transaction_ref = forms.CharField(
        label="Mã giao dịch ngân hàng",
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Ví dụ: FT261108987654, MB123456789...',
            'id': 'id_bank_transaction_ref',
            'autocomplete': 'off',
        }),
        help_text="Mã định danh giao dịch duy nhất từ ngân hàng (chống xác nhận lặp).",
    )

    received_at = forms.DateTimeField(
        label="Thời điểm nhận tiền thực tế",
        required=True,
        widget=forms.DateTimeInput(attrs={
            'class': 'qhm-form-input',
            'type': 'datetime-local',
            'id': 'id_received_at',
        }),
        help_text="Thời điểm ngân hàng ghi có giao dịch (bắt buộc, không được ở tương lai).",
    )

    def clean_observed_recipient_account(self):
        val = self.cleaned_data.get('observed_recipient_account', '')
        val_str = str(val).strip()
        if not val_str:
            raise ValidationError("Tài khoản nhận tiền thực tế không được để trống.", code='recipient_account_required')
        return val_str

    def clean_bank_transaction_ref(self):
        val = self.cleaned_data.get('bank_transaction_ref', '')
        val_str = str(val).strip()
        if not val_str:
            raise ValidationError("Mã giao dịch ngân hàng không được để trống.", code='bank_transaction_ref_required')
        return val_str.upper()

    def clean_observed_transfer_reference(self):
        val = self.cleaned_data.get('observed_transfer_reference', '')
        val_str = str(val).strip()
        if not val_str:
            raise ValidationError("Nội dung chuyển khoản thực tế không được để trống.", code='transfer_reference_required')
        return val_str

    def clean_received_at(self):
        val = self.cleaned_data.get('received_at')
        if not val:
            raise ValidationError("Thời điểm nhận tiền thực tế từ ngân hàng không được để trống.", code='received_at_required')
        now = timezone.now()
        if val > now:
            raise ValidationError("Thời điểm nhận tiền thực tế không thể ở tương lai.", code='future_received_at')
        return val


class PaymentReconciliationForm(forms.Form):
    reason_code = forms.ChoiceField(
        label="Lý do đối soát",
        choices=ReconciliationReason.choices,
        required=True,
        widget=forms.Select(attrs={
            'class': 'qhm-form-input qhm-form-select',
            'id': 'id_reason_code',
        }),
        help_text="Chọn nguyên nhân phát sinh ngoại lệ chuyển khoản QR cần đối soát.",
    )
    notes = forms.CharField(
        label="Ghi chú đối soát",
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập thông tin chi tiết về sai lệch, thông tin giao dịch ghi nhận được hoặc các lưu ý đối soát...',
            'id': 'id_notes',
            'rows': 4,
        }),
        help_text="Ghi chú chi tiết hỗ trợ đối soát nội bộ và liên hệ khách hàng (tùy chọn).",
    )

    def clean_reason_code(self):
        code = self.cleaned_data.get('reason_code', '').strip()
        if not code or code not in ReconciliationReason.values:
            raise ValidationError("Lý do đối soát không hợp lệ hoặc để trống.", code='invalid_reason_code')
        return code

    def clean_notes(self):
        notes = self.cleaned_data.get('notes', '')
        return str(notes).strip() if notes else ''


class ResolveExpiredReconciliationForm(forms.Form):
    confirm_no_transfer = forms.BooleanField(
        label="Xác nhận đối soát ngân hàng",
        required=True,
        widget=forms.CheckboxInput(attrs={
            'id': 'id_confirm_no_transfer',
            'class': 'qhm-form-checkbox',
        }),
        help_text="Bắt buộc tích chọn xác nhận đã kiểm tra đối soát sao kê/sổ phụ ngân hàng và chắc chắn không có khoản tiền hợp lệ nào được chuyển vào cho đơn này.",
    )
    audit_note = forms.CharField(
        label="Ghi chú kiểm toán rà soát sao kê",
        required=True,
        min_length=5,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập chi tiết kết quả rà soát sổ phụ/sao kê ngân hàng (ví dụ: Đã kiểm tra sao kê Vietcombank từ 09:00 đến 17:00, không có giao dịch khớp mã tham chiếu hoặc số tiền tương ứng)...',
            'id': 'id_audit_note',
            'rows': 3,
        }),
        help_text="Ghi chú căn cứ kiểm toán đối soát bắt buộc giải trình cơ sở đưa ra kết luận (tối thiểu 5 ký tự).",
    )

    def clean_confirm_no_transfer(self):
        confirmed = self.cleaned_data.get('confirm_no_transfer')
        if not confirmed:
            raise ValidationError(
                "Bắt buộc phải tích xác nhận đã rà soát sao kê ngân hàng và xác nhận không có chuyển khoản hợp lệ.",
                code='confirmation_required'
            )
        return confirmed

    def clean_audit_note(self):
        note = self.cleaned_data.get('audit_note', '').strip()
        if not note or len(note) < 5:
            raise ValidationError(
                "Ghi chú kiểm toán rà soát sao kê ngân hàng bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        return note


class ResolveValidTransferReconciliationForm(forms.Form):
    observed_recipient_account = forms.CharField(
        label="Tài khoản nhận tiền thực tế",
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập đầy đủ số tài khoản nhận tiền thực tế...',
            'id': 'id_valid_observed_recipient_account',
            'autocomplete': 'off',
        }),
        help_text="Số tài khoản ngân hàng thụ hưởng thực tế ghi nhận tiền vào.",
    )

    observed_amount = forms.DecimalField(
        label="Số tiền thực nhận (VNĐ)",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.00'),
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập số tiền thực tế ghi có...',
            'id': 'id_valid_observed_amount',
            'step': '1000',
        }),
        help_text="Số tiền ghi có chính xác trên sao kê (không tự ý làm tròn).",
    )

    observed_transfer_reference = forms.CharField(
        label="Nội dung chuyển khoản thực tế",
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập diễn giải / nội dung chuyển khoản trên sao kê...',
            'id': 'id_valid_observed_transfer_reference',
            'autocomplete': 'off',
        }),
        help_text="Nội dung chuyển tiền do người gửi nhập trên ứng dụng ngân hàng.",
    )

    bank_transaction_ref = forms.CharField(
        label="Mã giao dịch ngân hàng",
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Ví dụ: FT261108987654, MB123456789...',
            'id': 'id_valid_bank_transaction_ref',
            'autocomplete': 'off',
        }),
        help_text="Mã định danh giao dịch duy nhất từ ngân hàng (chống trùng lặp).",
    )

    received_at = forms.DateTimeField(
        label="Thời điểm nhận tiền thực tế",
        required=True,
        widget=forms.DateTimeInput(attrs={
            'class': 'qhm-form-input',
            'type': 'datetime-local',
            'id': 'id_valid_received_at',
        }),
        help_text="Thời điểm ngân hàng ghi có (bắt buộc phải nằm trong thời hạn thanh toán đơn hàng).",
    )

    audit_note = forms.CharField(
        label="Ghi chú kiểm toán / Căn cứ giải quyết",
        required=True,
        min_length=5,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Ghi rõ căn cứ đối chiếu sao kê và lý do xác nhận chuyển khoản hợp lệ...',
            'id': 'id_valid_audit_note',
            'rows': 3,
        }),
        help_text="Ghi chú kiểm toán bắt buộc giải trình căn cứ xác nhận (tối thiểu 5 ký tự).",
    )

    def clean_observed_recipient_account(self):
        val = self.cleaned_data.get('observed_recipient_account', '')
        val_str = str(val).strip()
        if not val_str:
            raise ValidationError("Tài khoản nhận tiền thực tế không được để trống.", code='recipient_account_required')
        return val_str

    def clean_bank_transaction_ref(self):
        val = self.cleaned_data.get('bank_transaction_ref', '')
        val_str = str(val).strip().upper()
        if not val_str:
            raise ValidationError("Mã giao dịch ngân hàng không được để trống.", code='bank_transaction_ref_required')
        return val_str

    def clean_observed_transfer_reference(self):
        val = self.cleaned_data.get('observed_transfer_reference', '')
        val_str = str(val).strip()
        if not val_str:
            raise ValidationError("Nội dung chuyển khoản thực tế không được để trống.", code='transfer_content_required')
        return val_str

    def clean_received_at(self):
        val = self.cleaned_data.get('received_at')
        if not val:
            raise ValidationError("Thời điểm nhận tiền thực tế không được để trống.", code='received_at_required')
        if timezone.is_naive(val):
            val = timezone.make_aware(val, timezone.get_current_timezone())
        now = timezone.now()
        if val > now:
            raise ValidationError(
                f"Thời điểm nhận tiền thực tế ({val.strftime('%H:%M:%S %d/%m/%Y')}) không được ở tương lai so với hiện tại ({now.strftime('%H:%M:%S %d/%m/%Y')}).",
                code='future_received_at'
            )
        return val

    def clean_audit_note(self):
        note = self.cleaned_data.get('audit_note', '').strip()
        if not note or len(note) < 5:
            raise ValidationError(
                "Ghi chú kiểm toán xác nhận chuyển khoản hợp lệ bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        return note


class ResolveTerminalTransferReconciliationForm(forms.Form):
    observed_recipient_account = forms.CharField(
        label="Tài khoản nhận tiền thực tế",
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập đầy đủ số tài khoản nhận tiền thực tế...',
            'id': 'id_terminal_observed_recipient_account',
            'autocomplete': 'off',
        }),
        help_text="Số tài khoản ngân hàng thụ hưởng thực tế ghi nhận tiền vào.",
    )

    observed_amount = forms.DecimalField(
        label="Số tiền thực nhận (VNĐ)",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.00'),
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập số tiền thực tế ghi có...',
            'id': 'id_terminal_observed_amount',
            'step': '1000',
        }),
        help_text="Số tiền ghi có chính xác trên sao kê (không tự ý làm tròn).",
    )

    observed_transfer_reference = forms.CharField(
        label="Nội dung chuyển khoản thực tế",
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập diễn giải / nội dung chuyển khoản trên sao kê...',
            'id': 'id_terminal_observed_transfer_reference',
            'autocomplete': 'off',
        }),
        help_text="Nội dung chuyển tiền do người gửi nhập trên ứng dụng ngân hàng.",
    )

    bank_transaction_ref = forms.CharField(
        label="Mã giao dịch ngân hàng",
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Ví dụ: FT261108987654, MB123456789...',
            'id': 'id_terminal_bank_transaction_ref',
            'autocomplete': 'off',
        }),
        help_text="Mã định danh giao dịch duy nhất từ ngân hàng (chống trùng lặp).",
    )

    received_at = forms.DateTimeField(
        label="Thời điểm nhận tiền thực tế",
        required=True,
        widget=forms.DateTimeInput(attrs={
            'class': 'qhm-form-input',
            'type': 'datetime-local',
            'id': 'id_terminal_received_at',
        }),
        help_text="Thời điểm ngân hàng ghi có thực tế (không ở tương lai).",
    )

    audit_note = forms.CharField(
        label="Ghi chú kiểm toán / Căn cứ giải quyết",
        required=True,
        min_length=5,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Ghi rõ căn cứ đối chiếu sao kê và lý do xác nhận tiền muộn/sau hủy chờ hoàn tiền...',
            'id': 'id_terminal_audit_note',
            'rows': 3,
        }),
        help_text="Ghi chú kiểm toán bắt buộc giải trình căn cứ xác nhận (tối thiểu 5 ký tự).",
    )

    def clean_observed_recipient_account(self):
        val = self.cleaned_data.get('observed_recipient_account', '')
        val_str = str(val).strip()
        if not val_str:
            raise ValidationError("Tài khoản nhận tiền thực tế không được để trống.", code='recipient_account_required')
        return val_str

    def clean_bank_transaction_ref(self):
        val = self.cleaned_data.get('bank_transaction_ref', '')
        val_str = str(val).strip().upper()
        if not val_str:
            raise ValidationError("Mã giao dịch ngân hàng không được để trống.", code='bank_transaction_ref_required')
        return val_str

    def clean_observed_transfer_reference(self):
        val = self.cleaned_data.get('observed_transfer_reference', '')
        val_str = str(val).strip()
        if not val_str:
            raise ValidationError("Nội dung chuyển khoản thực tế không được để trống.", code='transfer_content_required')
        return val_str

    def clean_received_at(self):
        val = self.cleaned_data.get('received_at')
        if not val:
            raise ValidationError("Thời điểm nhận tiền thực tế không được để trống.", code='received_at_required')
        if timezone.is_naive(val):
            val = timezone.make_aware(val, timezone.get_current_timezone())
        now = timezone.now()
        if val > now:
            raise ValidationError(
                f"Thời điểm nhận tiền thực tế ({val.strftime('%H:%M:%S %d/%m/%Y')}) không được ở tương lai so với hiện tại ({now.strftime('%H:%M:%S %d/%m/%Y')}).",
                code='future_received_at'
            )
        return val

    def clean_audit_note(self):
        note = self.cleaned_data.get('audit_note', '').strip()
        if not note or len(note) < 5:
            raise ValidationError(
                "Ghi chú kiểm toán xác nhận chuyển khoản cho đơn kết thúc bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        return note


class RecordExternalRefundForm(forms.Form):
    bank_reference = forms.CharField(
        label="Mã giao dịch ngân hàng chuyển hoàn",
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Ví dụ: FT261109999001, REF_MB_123456...',
            'id': 'id_refund_bank_reference',
            'autocomplete': 'off',
        }),
        help_text="Mã định danh giao dịch chuyển khoản hoàn tiền từ sao kê ngân hàng (bắt buộc, chống trùng lặp).",
    )

    refund_amount = forms.DecimalField(
        label="Số tiền hoàn thực tế (VNĐ)",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập đúng 100% số tiền hoàn...',
            'id': 'id_refund_amount',
            'step': '0.01',
        }),
        help_text="Số tiền hoàn thực tế. Nghiệp vụ yêu cầu hoàn đủ 100% số tiền thực nhận, từ chối hoàn một phần hoặc vượt mức.",
    )

    completed_at = forms.DateTimeField(
        label="Thời điểm ngân hàng hoàn tất",
        required=True,
        widget=forms.DateTimeInput(attrs={
            'class': 'qhm-form-input',
            'type': 'datetime-local',
            'id': 'id_refund_completed_at',
        }),
        help_text="Thời điểm giao dịch chuyển tiền hoàn thành công theo xác nhận ngân hàng (không ở tương lai).",
    )

    audit_note = forms.CharField(
        label="Ghi chú kiểm toán / Lý do hoàn tiền",
        required=True,
        min_length=5,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-input',
            'placeholder': 'Nhập ghi chú kiểm toán giải trình lý do xác nhận hoàn tiền...',
            'id': 'id_refund_audit_note',
            'rows': 3,
        }),
        help_text="Ghi chú kiểm toán giải trình việc hoàn tiền (tối thiểu 5 ký tự).",
    )

    confirm_outbound_success = forms.BooleanField(
        label="Xác nhận giao dịch chuyển tiền hoàn đã thành công ngoài thực tế",
        required=True,
        widget=forms.CheckboxInput(attrs={
            'id': 'id_refund_confirm_outbound_success',
        }),
        help_text="Tôi cam đoan giao dịch chuyển tiền hoàn đã hoàn tất thành công ngoài thực tế trên tài khoản ngân hàng (hệ thống không tự chuyển tiền).",
    )

    def clean_bank_reference(self):
        val = self.cleaned_data.get('bank_reference', '')
        val_str = str(val).strip().upper()
        if not val_str:
            raise ValidationError(
                "Mã giao dịch ngân hàng chuyển hoàn không được để trống.",
                code='bank_reference_required'
            )
        return val_str

    def clean_completed_at(self):
        val = self.cleaned_data.get('completed_at')
        if not val:
            raise ValidationError(
                "Thời điểm ngân hàng hoàn tất không được để trống.",
                code='completed_at_required'
            )
        if timezone.is_naive(val):
            val = timezone.make_aware(val, timezone.get_current_timezone())
        now = timezone.now()
        if val > now:
            raise ValidationError(
                f"Thời điểm ngân hàng hoàn tất ({val.strftime('%H:%M:%S %d/%m/%Y')}) không được ở tương lai so với hiện tại ({now.strftime('%H:%M:%S %d/%m/%Y')}).",
                code='future_completed_at'
            )
        return val

    def clean_audit_note(self):
        note = self.cleaned_data.get('audit_note', '').strip()
        if not note or len(note) < 5:
            raise ValidationError(
                "Ghi chú kiểm toán xác nhận hoàn tiền bắt buộc phải có ít nhất 5 ký tự.",
                code='audit_note_required'
            )
        return note

    def clean_confirm_outbound_success(self):
        val = self.cleaned_data.get('confirm_outbound_success')
        if not val:
            raise ValidationError(
                "Bắt buộc xác nhận giao dịch chuyển tiền hoàn đã thành công ngoài thực tế trước khi ghi nhận.",
                code='confirm_outbound_required'
            )
        return val


