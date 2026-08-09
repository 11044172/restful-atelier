from django import template

register = template.Library()


@register.filter
def currency(value):
    if value is None:
        return "未確定"
    try:
        return f"NT$ {int(value):,}"
    except (TypeError, ValueError):
        return value
