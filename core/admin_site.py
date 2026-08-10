from datetime import timedelta

from django.contrib.admin import AdminSite
from django.db.models import Sum
from django.utils import timezone


class BackofficeAdminSite(AdminSite):
    """Branded operations console backed by Django's permission system."""

    site_header = "靜院居家 營運管理"
    site_title = "靜院居家 營運管理"
    index_title = "營運儀表板"
    index_template = "admin/index.html"
    login_template = "admin/login.html"
    enable_nav_sidebar = True

    def index(self, request, extra_context=None):
        from catalog.models import Product
        from inquiries.models import Inquiry
        from orders.models import LineNotification, Order, Payment

        can_view_orders = request.user.has_perm("orders.view_order")
        can_view_inquiries = request.user.has_perm("inquiries.view_inquiry")
        can_view_products = request.user.has_perm("catalog.view_product")
        can_view_payments = request.user.has_perm("orders.view_payment")
        can_view_notifications = request.user.has_perm("orders.view_linenotification")
        now = timezone.now()

        context = {
            "can_view_orders": can_view_orders,
            "can_view_inquiries": can_view_inquiries,
            "can_view_products": can_view_products,
            "can_view_payments": can_view_payments,
            "can_view_notifications": can_view_notifications,
            "orders_today": 0,
            "orders_need_action": 0,
            "sales_30_days": 0,
            "new_inquiries": 0,
            "low_stock_count": 0,
            "failed_notifications": 0,
            "recent_orders": [],
            "recent_inquiries": [],
            "low_stock_products": [],
        }

        if can_view_orders:
            orders = Order.objects.select_related("line_customer")
            context.update(
                orders_today=orders.filter(created_at__date=timezone.localdate()).count(),
                orders_need_action=orders.filter(
                    status__in=(
                        Order.Status.RECEIVED,
                        Order.Status.SHIPPING_REVIEW,
                        Order.Status.AWAITING_PAYMENT,
                        Order.Status.PAID,
                        Order.Status.PREPARING,
                    )
                ).count(),
                recent_orders=orders.exclude(status__in=(Order.Status.COMPLETED, Order.Status.CANCELLED))[:6],
            )

        if can_view_payments:
            context["sales_30_days"] = Payment.objects.filter(
                status=Payment.Status.CONFIRMED,
                paid_at__gte=now - timedelta(days=30),
            ).aggregate(total=Sum("amount"))["total"] or 0

        if can_view_inquiries:
            inquiries = Inquiry.objects.select_related("category")
            context.update(
                new_inquiries=inquiries.filter(status=Inquiry.Status.NEW).count(),
                recent_inquiries=inquiries.exclude(status__in=(Inquiry.Status.COMPLETED, Inquiry.Status.SPAM))[:5],
            )

        if can_view_products:
            low_stock = Product.objects.select_related("category").filter(
                is_published=True,
                is_preorder=False,
                stock__lte=2,
            ).order_by("stock", "sort_order", "name")
            context.update(low_stock_count=low_stock.count(), low_stock_products=low_stock[:5])

        if can_view_notifications:
            context["failed_notifications"] = LineNotification.objects.filter(
                status=LineNotification.Status.FAILED
            ).count()

        if extra_context:
            context.update(extra_context)
        return super().index(request, context)


backoffice_site = BackofficeAdminSite(name="admin")
