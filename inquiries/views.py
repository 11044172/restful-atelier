from django.conf import settings
from django.db import transaction
from django.shortcuts import redirect, render

from .antispam import rate_limit_exceeded, verify_turnstile
from .forms import InquiryForm
from .services import send_inquiry_emails


def contact(request):
    submitted = False
    if request.method == "POST":
        form = InquiryForm(request.POST)
        if rate_limit_exceeded(request, "contact", settings.CONTACT_RATE_LIMIT):
            return render(request, "inquiries/contact.html", {"form": form, "rate_limited": True}, status=429)
        if form.is_valid():
            if not verify_turnstile(request):
                form.add_error(None, "安全驗證失敗，請再試一次。")
            else:
                inquiry = form.save()
                transaction.on_commit(lambda: send_inquiry_emails(inquiry))
                request.session["inquiry_submitted"] = True
                return redirect("inquiries:contact_success")
    else:
        form = InquiryForm()
        topic = request.GET.get("topic")
        if topic:
            category = form.fields["category"].queryset.filter(slug=topic).first()
            if category:
                form.initial["category"] = category
    return render(request, "inquiries/contact.html", {"form": form, "submitted": submitted, "turnstile_site_key": settings.TURNSTILE_SITE_KEY})


def contact_success(request):
    if not request.session.pop("inquiry_submitted", False):
        return redirect("inquiries:contact")
    return render(request, "inquiries/contact_success.html", {"noindex": True})
