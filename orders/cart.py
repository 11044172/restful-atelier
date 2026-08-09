from decimal import Decimal

from catalog.models import Product


class Cart:
    SESSION_KEY = "cart"

    def __init__(self, request):
        self.session = request.session
        self.data = self.session.get(self.SESSION_KEY, {})

    def add(self, product, quantity=1, replace=False):
        key = str(product.pk)
        quantity = max(1, int(quantity))
        current = int(self.data.get(key, 0))
        self.data[key] = quantity if replace else current + quantity
        self.save()

    def remove(self, product_id):
        self.data.pop(str(product_id), None)
        self.save()

    def clear(self):
        self.session.pop(self.SESSION_KEY, None)
        if hasattr(self.session, "modified"):
            self.session.modified = True
        self.data = {}

    def save(self):
        self.session[self.SESSION_KEY] = self.data
        if hasattr(self.session, "modified"):
            self.session.modified = True

    def __len__(self):
        return sum(int(value) for value in self.data.values())

    def items(self):
        ids = [int(key) for key in self.data if key.isdigit()]
        products = Product.objects.filter(pk__in=ids, is_published=True).select_related("category").prefetch_related("images")
        by_id = {product.pk: product for product in products}
        result = []
        changed = False
        for key, raw_quantity in list(self.data.items()):
            product = by_id.get(int(key)) if key.isdigit() else None
            if not product:
                self.data.pop(key, None)
                changed = True
                continue
            quantity = max(1, int(raw_quantity))
            result.append({"product": product, "quantity": quantity, "line_total": product.price * Decimal(quantity)})
        if changed:
            self.save()
        return result

    @property
    def subtotal(self):
        return sum((item["line_total"] for item in self.items()), Decimal("0"))
