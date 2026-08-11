from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from catalog.models import Product
from content.models import InteriorProject, Publication


class Command(BaseCommand):
    help = "TEST商品・作品・出版物・仮画像を一覧化し、安全に非公開化または未使用商品のみ削除します。"

    def add_arguments(self, parser):
        parser.add_argument("--action", choices=("list", "unpublish", "delete-safe"), default="list")
        parser.add_argument("--execute", action="store_true", help="変更を実行（省略時はdry-run）")

    def handle(self, *args, **options):
        products = Product.objects.filter(Q(name__icontains="TEST") | Q(sku__istartswith="TEST") | Q(slug__istartswith="test" )).distinct()
        projects = InteriorProject.objects.filter(Q(title__icontains="TEST") | Q(slug__istartswith="test"))
        publications = Publication.objects.filter(Q(title__icontains="TEST") | Q(slug__istartswith="test"))
        placeholders = Product.objects.filter(Q(images__image="") | Q(images__isnull=True)).distinct()
        self.stdout.write(f"TEST products={products.count()} projects={projects.count()} publications={publications.count()} placeholder_products={placeholders.count()}")
        for product in products:
            self.stdout.write(f"PRODUCT {product.pk} {product.sku} {product.name} published={product.is_published} orders={product.order_items.count()}")
        for project in projects: self.stdout.write(f"PROJECT {project.pk} {project.title} published={project.published}")
        for publication in publications: self.stdout.write(f"PUBLICATION {publication.pk} {publication.title} published={publication.published}")
        if options["action"] == "list" or not options["execute"]:
            if options["action"] != "list": self.stdout.write(self.style.WARNING("dry-run: --execute未指定のため変更なし"))
            return
        with transaction.atomic():
            if options["action"] == "unpublish":
                count = products.update(is_published=False) + projects.update(published=False) + publications.update(published=False)
                self.stdout.write(self.style.SUCCESS(f"unpublished={count}"))
            elif options["action"] == "delete-safe":
                protected = products.filter(order_items__isnull=False).distinct()
                safe = products.exclude(pk__in=protected.values("pk"))
                count = safe.count()
                safe.delete()
                projects.update(published=False)
                publications.update(published=False)
                self.stdout.write(self.style.SUCCESS(f"deleted_unused_products={count}; accounting-linked products preserved={protected.count()}"))
