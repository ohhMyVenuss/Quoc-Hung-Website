from django.core.management.base import BaseCommand

from chat.models import ChatFAQ

SAMPLE_FAQS = [
    {
        'question': 'Cửa hàng làm việc giờ nào và liên hệ thế nào?',
        'answer': (
            'Quốc Hùng Mobile hoạt động từ 08:00 đến 21:30 hằng ngày (kể cả chủ nhật và ngày lễ). '
            'Hotline hỗ trợ miễn phí: 1800 6868. '
            'Trụ sở chính: 123 Đường Công Nghệ, Quận 1, TP. Hồ Chí Minh. '
            'Email: contact@quochungmobile.vn. '
            'Bạn cũng có thể nhắn câu hỏi trực tiếp tại đây để nhân viên hỗ trợ.'
        ),
        'keywords': 'giờ làm việc, thời gian làm việc, mở cửa, hotline, địa chỉ cửa hàng, trụ sở cửa hàng, tổng đài, email liên hệ, liên hệ cửa hàng',
    },
    {
        'question': 'Có những hình thức thanh toán nào?',
        'answer': (
            'Cửa hàng hỗ trợ 2 hình thức thanh toán: thanh toán khi nhận hàng (COD) '
            'và chuyển khoản ngân hàng qua mã VietQR.'
        ),
        'keywords': 'hình thức thanh toán, phương thức thanh toán, cách thức thanh toán, thanh toán như thế nào, thanh toán cod, thanh toán chuyển khoản',
    },
    {
        'question': 'Chuyển khoản VietQR được xác nhận như thế nào?',
        'answer': (
            'Sau khi hoàn tất quét mã và chuyển khoản đúng số tiền, bạn bấm nút "Tôi đã chuyển khoản" '
            'trên trang chi tiết đơn hàng. Nhân viên bán hàng sẽ kiểm tra đối soát sao kê và xác nhận đơn '
            'trong giờ làm việc. Lưu ý: việc bấm nút chưa có nghĩa là cửa hàng đã nhận được tiền.'
        ),
        'keywords': 'chuyển khoản vietqr, xác nhận vietqr, xác nhận qr, đối soát qr, đối soát sao kê, xác nhận chuyển khoản, xác minh chuyển khoản',
    },
    {
        'question': 'Tôi có thể hủy đơn hàng không?',
        'answer': (
            'Bạn có thể tự hủy đơn hàng trực tiếp trên hệ thống nếu bạn là người đặt đơn, đơn chọn thanh toán COD, '
            'chưa thanh toán và đang ở trạng thái Chờ xác nhận hoặc Đã xác nhận (chưa giao cho đơn vị vận chuyển). '
            'Nếu đơn hàng không thuộc các điều kiện trên hoặc bạn cần trợ giúp, vui lòng nhắn tin tại đây để nhân viên hỗ trợ xử lý.'
        ),
        'keywords': 'hủy đơn, hủy hàng, huỷ đơn, huỷ hàng, hủy đơn hàng, huỷ đơn hàng',
    },
    {
        'question': 'Chính sách đổi trả và bảo hành được hỗ trợ thế nào?',
        'answer': (
            'Cửa hàng hỗ trợ tiếp nhận và xử lý yêu cầu bảo hành hoặc đổi trả theo từng trường hợp cụ thể '
            'đối với sản phẩm lỗi kỹ thuật hoặc sự cố giao hàng. Vui lòng nhắn tin kèm mã đơn hàng và mô tả tình trạng sản phẩm '
            'tại đây để nhân viên bán hàng kiểm tra và hướng dẫn chi tiết.'
        ),
        'keywords': 'đổi trả, đổi hàng, trả hàng, hoàn tiền, bảo hành, khiếu nại',
    },
    {
        'question': 'Làm thế nào để tìm kiếm và đặt mua sản phẩm?',
        'answer': (
            'Bạn có thể tìm kiếm sản phẩm theo tên hoặc danh mục tại thanh tìm kiếm trên trang chủ, chọn cấu hình phù hợp '
            'và bấm "Thêm vào giỏ hàng" để tiến hành đặt hàng trực tuyến. Nếu cần tư vấn thêm về sản phẩm, '
            'bạn có thể nhắn tin trực tiếp tại đây.'
        ),
        'keywords': 'tìm kiếm sản phẩm, đặt mua sản phẩm, đặt hàng, mua hàng, tư vấn sản phẩm',
    },
]


class Command(BaseCommand):
    help = 'Tạo các câu hỏi thường gặp mẫu cho chatbox (không ghi đè câu đã có).'

    def handle(self, *args, **options):
        created_count = 0
        for item in SAMPLE_FAQS:
            _faq, created = ChatFAQ.objects.get_or_create(
                question=item['question'],
                defaults={'answer': item['answer'], 'keywords': item['keywords'], 'is_active': True},
            )
            created_count += int(created)
        self.stdout.write(self.style.SUCCESS(
            f'Đã tạo {created_count} câu hỏi mới, bỏ qua {len(SAMPLE_FAQS) - created_count} câu đã có.'
        ))
