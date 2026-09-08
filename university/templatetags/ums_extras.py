from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Look up a value in a dict by key inside templates."""
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return None


@register.filter
def pct_bar(value):
    try:
        return min(100, max(0, float(value)))
    except (TypeError, ValueError):
        return 0


@register.filter
def has_perm(user, permission_code):
    """Check whether a user has a specific granular permission."""
    from university.permissions_services import has_user_permission
    return has_user_permission(user, permission_code)


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """
    Safely update or append query parameters in a URL without duplicating them.
    Usage: href="?{% url_replace page=page_obj.next_page_number %}"
    """
    query = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v is None or v == "":
            query.pop(k, None)
        else:
            query[k] = str(v)
    return query.urlencode()

