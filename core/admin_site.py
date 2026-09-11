from datetime import timedelta

from django.contrib.admin import AdminSite
from django.http import JsonResponse
from django.urls import path
import json
import logging
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Sum, Value, When
from django.utils import timezone


class BackofficeAdminSite(AdminSite):
    """Branded operations console backed by Django's permission system."""

    site_header = "靜院居家 營運管理"
    site_title = "靜院居家 營運管理"
    index_title = "營運儀表板"
    index_template = "admin/index.html"
    login_template = "admin/login.html"
    enable_nav_sidebar = True

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("image-uploads/presign/", self.admin_view(self.direct_image_presign), name="direct_image_presign"),
            path("image-uploads/complete/", self.admin_view(self.direct_image_complete), name="direct_image_complete"),
        ]
        return custom + urls

    @staticmethod
    def _image_json(request):
        from core.direct_image_uploads import DirectImageError
        if request.method != "POST":
            raise DirectImageError("必須使用 POST 請求。", "method_not_allowed", 405)
        if len(request.body) > 32 * 1024:
            raise DirectImageError("請求內容過大。", "request_too_large", 413)
        try:
            value = json.loads(request.body or "{}")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise DirectImageError("請求格式無效。", "invalid_json") from exc
        if not isinstance(value, dict):
            raise DirectImageError("請求格式無效。", "invalid_json")
        return value

    def _image_api(self, callback):
        from core.direct_image_uploads import DirectImageError
        try:
            response = callback()
        except DirectImageError as exc:
            response = JsonResponse({"error": exc.message, "code": exc.code}, status=exc.status)
        except Exception:
            logging.getLogger("restfull.image_uploads").exception("unexpected_image_api_error")
            response = JsonResponse({"error": "圖片處理失敗，請重試。", "code": "internal_error"}, status=500)
        response["Cache-Control"] = "no-store"
        return response

    def direct_image_presign(self, request):
        from core.direct_image_uploads import create_upload
        def action():
            data = self._image_json(request)
            upload, url = create_upload(user=request.user, category=data.get("category"), filename=data.get("filename"), content_type=data.get("content_type"), size=data.get("size"), width=data.get("width"), height=data.get("height"))
            return JsonResponse({"upload_url": url, "object_key": upload.object_key, "upload_id": upload.pk})
        return self._image_api(action)

    def direct_image_complete(self, request):
        from core.direct_image_uploads import complete_upload
        def action():
            data = self._image_json(request)
            upload, token = complete_upload(user=request.user, category=data.get("category"), upload_id=data.get("upload_id"), object_key=data.get("object_key"))
            return JsonResponse({"token": token, "width": upload.width, "height": upload.height, "bytes": upload.file_size})
        return self._image_api(action)

    def index(self, request, extra_context=None):
        from catalog.models import Product
        from inquiries.models import Inquiry
        from orders.models import NotificationOutbox, Order, Payment

        can_view_orders = request.user.has_perm("orders.view_order")
        can_view_inquiries = request.user.has_perm("inquiries.view_inquiry")
        can_view_products = request.user.has_perm("catalog.view_product")
        can_view_payments = request.user.has_perm("orders.view_payment")
        can_view_notifications = request.user.has_perm("orders.view_notificationoutbox")
        now = timezone.now()

        context = {
            "can_view_orders": can_view_orders,
            "can_view_inquiries": can_view_inquiries,
            "can_view_products": can_view_products,
            "can_view_payments": can_view_payments,
            "can_view_notifications": can_view_notifications,
            "orders_today": 0,
            "orders_need_action": 0,
            "customer_waiting": 0,
            "shipped_waiting": 0,
            "sales_30_days": 0,
            "new_inquiries": 0,
            "low_stock_count": 0,
            "failed_notifications": 0,
            "recent_orders": [],
            "recent_inquiries": [],
            "low_stock_products": [],
        }

        if can_view_orders:
            notification_error = NotificationOutbox.objects.filter(
                order_id=OuterRef("pk"),
                status__in=(NotificationOutbox.Status.RETRY, NotificationOutbox.Status.DEAD),
            )
            orders = Order.objects.select_related("line_customer").annotate(
                has_notification_error=Exists(notification_error),
                dashboard_priority=Case(
                    When(has_notification_error=True, then=Value(0)),
                    When(status__in=(Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.PAID, Order.Status.PREPARING, Order.Status.REFUND_PENDING), then=Value(1)),
                    When(status=Order.Status.SHIPPED, then=Value(2)),
                    When(status=Order.Status.AWAITING_PAYMENT, then=Value(3)),
                    default=Value(4), output_field=IntegerField(),
                ),
            )
            store_statuses = (Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.PAID, Order.Status.PREPARING, Order.Status.REFUND_PENDING)
            context.update(
                orders_today=orders.filter(created_at__date=timezone.localdate()).count(),
                orders_need_action=orders.filter(
                    Q(status__in=store_statuses) | Q(has_notification_error=True)
                ).distinct().count(),
                customer_waiting=orders.filter(status=Order.Status.AWAITING_PAYMENT).count(),
                shipped_waiting=orders.filter(status=Order.Status.SHIPPED).count(),
                recent_orders=orders.exclude(status__in=(Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.REFUNDED)).order_by("dashboard_priority", "created_at")[:8],
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
            low_stock = Product.objects.published().select_related("category").filter(
                is_preorder=False,
                stock__lte=2,
            ).order_by("stock", "sort_order", "name")
            context.update(low_stock_count=low_stock.count(), low_stock_products=low_stock[:5])

        if can_view_notifications:
            context["failed_notifications"] = NotificationOutbox.objects.filter(
                status__in=(NotificationOutbox.Status.RETRY, NotificationOutbox.Status.DEAD)
            ).count()

        if extra_context:
            context.update(extra_context)
        return super().index(request, context)


backoffice_site = BackofficeAdminSite(name="admin")
