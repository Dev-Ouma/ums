from .models import MenuItem, SiteSettings


def site(request):
    """Expose editable branding and navigation to every public template.

    Menus are returned as plain lists so a template can test them for
    emptiness and fall back to the hand-written navigation, which keeps the
    site working before an editor has configured anything.
    """
    settings_obj = SiteSettings.load()
    items = MenuItem.objects.filter(is_visible=True).select_related("page")
    menus = {}
    for item in items:
        menus.setdefault(item.location, []).append(item)
    return {
        "site": settings_obj,
        "site_menus": menus,
    }
