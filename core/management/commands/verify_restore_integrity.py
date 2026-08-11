from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from catalog.models import ProductImage
from content.models import InteriorProject, Publication
from orders.models import Order


class Command(BaseCommand):
    help = "復元後のDB制約・注文明細・画像参照整合性を読み取り専用で確認します。"

    def add_arguments(self, parser):
        parser.add_argument("--storage", action="store_true", help="全画像のstorage存在確認（外部アクセス）")

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
            if connection.vendor == "postgresql":
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        broken_orders = Order.objects.filter(items__isnull=True).count()
        missing = []
        if options["storage"]:
            fields = [*(image.image for image in ProductImage.objects.exclude(image="")), *(project.featured_image for project in InteriorProject.objects.exclude(featured_image="")), *(publication.cover_image for publication in Publication.objects.exclude(cover_image=""))]
            for field in fields:
                if field and not default_storage.exists(field.name): missing.append(field.name)
        self.stdout.write(f"orders_without_items={broken_orders} missing_media={len(missing)}")
        for name in missing[:50]: self.stdout.write(f"MISSING {name}")
        if broken_orders or missing: raise CommandError("restore integrity check failed")
        self.stdout.write(self.style.SUCCESS("restore integrity check passed"))
