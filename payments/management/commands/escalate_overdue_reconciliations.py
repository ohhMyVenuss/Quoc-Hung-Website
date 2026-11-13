from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from payments.services import escalate_overdue_reconciliations


class Command(BaseCommand):
    help = "Quét và chuyển cấp quản trị (ESCALATED) cho các hồ sơ đối soát QR quá hạn SLA 12 giờ làm việc (Task 09.6)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--as-of',
            type=str,
            default=None,
            help='Mốc thời gian đối chiếu (định dạng ISO 8601, ví dụ: 2026-11-10T17:00:00+07:00). Mặc định là thời gian hiện tại.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Chế độ mô phỏng kiểm tra, chỉ liệt kê các hồ sơ quá hạn mà không thực hiện cập nhật CSDL.',
        )

    def handle(self, *args, **options):
        as_of_str = options.get('as_of')
        dry_run = options.get('dry_run', False)

        as_of = None
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str.strip())
                if timezone.is_naive(as_of):
                    as_of = timezone.make_aware(as_of, timezone.get_current_timezone())
            except (ValueError, TypeError) as exc:
                raise CommandError(f"Định dạng thời gian --as-of không hợp lệ: '{as_of_str}'. Lỗi: {exc}")
        else:
            as_of = timezone.now()

        mode_str = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(self.style.NOTICE(
            f"{mode_str}Bắt đầu quét hồ sơ đối soát quá hạn SLA tại mốc: {as_of.isoformat()}..."
        ))

        try:
            result = escalate_overdue_reconciliations(as_of=as_of, dry_run=dry_run)

            if dry_run:
                self.stdout.write(self.style.SUCCESS(
                    f"[DRY-RUN] Tìm thấy {result.escalated_count} hồ sơ đối soát quá hạn SLA cần chuyển cấp: {result.escalated_ids} (Không có dữ liệu nào bị thay đổi)."
                ))
            else:
                if result.escalated_count > 0:
                    self.stdout.write(self.style.SUCCESS(
                        f"Đã chuyển cấp quản trị (ESCALATED) thành công cho {result.escalated_count} hồ sơ đối soát: {result.escalated_ids}."
                    ))
                else:
                    self.stdout.write(self.style.SUCCESS(
                        "Không có hồ sơ đối soát nào quá hạn SLA cần chuyển cấp."
                    ))

        except Exception as exc:
            raise CommandError(f"Lỗi khi thực hiện quét chuyển cấp hồ sơ đối soát: {exc}")

