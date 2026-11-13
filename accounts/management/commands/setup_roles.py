from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from accounts.permissions import ALL_ROLES


class Command(BaseCommand):
    help = (
        "Khoi tao 4 nhom vai tro nghiep vu chinh thuc theo SRS v2.3 Muc II: "
        "Customer, Sales Staff, Inventory Staff, Admin."
    )

    def handle(self, *args, **options):
        self.stdout.write("Bat dau cau hinh 4 nhom vai tro nghiep vu chuan Django...")
        created_count = 0
        existing_count = 0

        for role_name in ALL_ROLES:
            group, created = Group.objects.get_or_create(name=role_name)
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f" [MOI] Da tao thanh cong nhom: '{role_name}' (ID: {group.id})")
                )
            else:
                existing_count += 1
                perm_count = group.permissions.count()
                self.stdout.write(
                    f" [HIEN HUU] Nhom '{role_name}' da ton tai (ID: {group.id}, quyen gan truc tiep: {perm_count}). Giu nguyen."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Hoan thanh thiet lap vai tro! (Tao moi: {created_count}, Da co: {existing_count}, Tong: {len(ALL_ROLES)})"
            )
        )
