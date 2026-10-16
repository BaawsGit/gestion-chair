from decimal import Decimal
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _format_with_spaces(val, decimals=None):
    if val is None or val == "":
        return "0"
    try:
        # Convert to Decimal for precision
        d = Decimal(str(val))
        if decimals is not None:
            formatted = f"{d:.{decimals}f}"
        else:
            # If it's an exact integer, don't show trailing .00
            if d == d.to_integral():
                formatted = f"{int(d)}"
            else:
                formatted = f"{d:.2f}"

        if "." in formatted:
            int_part, dec_part = formatted.split(".")
            int_grouped = f"{int(int_part):,}".replace(",", " ")
            return f"{int_grouped},{dec_part}"
        else:
            return f"{int(formatted):,}".replace(",", " ")
    except Exception:
        return str(val)


@register.filter(name="cfa")
def cfa(value, decimals=0):
    """Format an amount with spaces every 3 digits followed by FCFA. Ex: 10000 -> 10 000 FCFA"""
    formatted = _format_with_spaces(value, decimals=decimals)
    return f"{formatted} FCFA"


@register.filter(name="cfa_kpi")
def cfa_kpi(value, decimals=0):
    """Format for KPI cards with small FCFA unit tag."""
    formatted = _format_with_spaces(value, decimals=decimals)
    return mark_safe(f'{formatted} <small class="fs-6 text-muted fw-normal">FCFA</small>')


@register.filter(name="group_num")
def group_num(value, decimals=None):
    """Format any number with space every 3 digits. Ex: 10000 -> 10 000"""
    return _format_with_spaces(value, decimals=decimals)


@register.filter(name="kg")
def kg(value):
    """Format quantity with kg unit and spaces."""
    return f"{_format_with_spaces(value)} kg"
