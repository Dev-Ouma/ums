"""Site manager UI plus the public page renderer.

Every manager view is gated on the ADMIN role (superusers included) via
`role_required`, which is the same guard the rest of the dashboard uses.
"""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from university.decorators import role_required

from . import services
from .forms import (BlockForm, MediaAssetForm, MenuItemForm, PageForm,
                    SiteSettingsForm)
from .models import (MENU_LOCATIONS, Block, MediaAsset, MenuItem, Page,
                     SiteSettings)

manager_required = role_required(Role.ADMIN)


# ==========================================================================
# PUBLIC
# ==========================================================================
def page(request, slug):
    """Render a CMS page. Drafts are visible to managers only."""
    obj = services.get_page(slug, request.user)
    if obj is None:
        raise Http404("No such page")
    return services.render_page(request, obj)


# ==========================================================================
# MANAGER — overview & settings
# ==========================================================================
@manager_required
def dashboard(request):
    pages = Page.objects.all()
    return render(request, "cms/dashboard.html", {
        "pages": pages[:6],
        "page_count": pages.count(),
        "published_count": pages.published().count(),
        "block_count": Block.objects.count(),
        "menu_count": MenuItem.objects.count(),
        "media_count": MediaAsset.objects.count(),
        "settings_obj": SiteSettings.load(),
    })


@manager_required
def site_settings(request):
    instance = SiteSettings.load()
    if request.method == "POST":
        form = SiteSettingsForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Site settings updated.")
            return redirect("cms:settings")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = SiteSettingsForm(instance=instance)
    return render(request, "cms/settings.html", {"form": form})


# ==========================================================================
# MANAGER — pages
# ==========================================================================
@manager_required
def page_list(request):
    return render(request, "cms/page_list.html", {
        "pages": Page.objects.all().prefetch_related("blocks"),
    })


@manager_required
def page_create(request):
    if request.method == "POST":
        form = PageForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.updated_by = request.user
            obj.save()
            messages.success(request, f"“{obj.title}” created — now add some sections.")
            return redirect("cms:page_edit", pk=obj.pk)
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = PageForm()
    return render(request, "cms/page_form.html", {"form": form, "is_create": True})


@manager_required
def page_edit(request, pk):
    obj = get_object_or_404(Page, pk=pk)
    if request.method == "POST":
        form = PageForm(request.POST, instance=obj)
        if form.is_valid():
            page_obj = form.save(commit=False)
            page_obj.updated_by = request.user
            page_obj.save()
            messages.success(request, "Page updated.")
            return redirect("cms:page_edit", pk=obj.pk)
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = PageForm(instance=obj)
    return render(request, "cms/page_form.html", {
        "form": form, "page_obj": obj, "blocks": obj.blocks.all(),
    })


@manager_required
@require_POST
def page_delete(request, pk):
    obj = get_object_or_404(Page, pk=pk)
    title = obj.title
    obj.delete()
    messages.info(request, f"“{title}” and its sections were deleted.")
    return redirect("cms:page_list")


@manager_required
@require_POST
def page_toggle_status(request, pk):
    obj = get_object_or_404(Page, pk=pk)
    obj.status = Page.DRAFT if obj.is_published else Page.PUBLISHED
    obj.updated_by = request.user
    obj.save(update_fields=["status", "updated_by", "updated_at"])
    messages.success(request, f"“{obj.title}” is now {obj.get_status_display().lower()}.")
    return redirect(request.POST.get("next") or "cms:page_list")


# ==========================================================================
# MANAGER — blocks
# ==========================================================================
@manager_required
def block_create(request, page_pk):
    parent = get_object_or_404(Page, pk=page_pk)
    if request.method == "POST":
        form = BlockForm(request.POST, request.FILES)
        if form.is_valid():
            block = form.save(commit=False)
            block.page = parent
            last = parent.blocks.order_by("-order").first()
            block.order = (last.order + 1) if last else 0
            block.save()
            messages.success(request, "Section added.")
            return redirect("cms:page_edit", pk=parent.pk)
        messages.error(request, "Please correct the highlighted fields.")
    else:
        # Pre-seed the type so the variant choices match from the first render.
        initial = {}
        requested = request.GET.get("type")
        if requested in dict(Block._meta.get_field("block_type").choices):
            initial["block_type"] = requested
        form = BlockForm(initial=initial)
    return render(request, "cms/block_form.html", {
        "form": form, "page_obj": parent, "is_create": True,
    })


@manager_required
def block_edit(request, pk):
    block = get_object_or_404(Block.objects.select_related("page"), pk=pk)
    if request.method == "POST":
        form = BlockForm(request.POST, request.FILES, instance=block)
        if form.is_valid():
            form.save()
            messages.success(request, "Section updated.")
            return redirect("cms:page_edit", pk=block.page.pk)
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = BlockForm(instance=block)
    return render(request, "cms/block_form.html", {
        "form": form, "block": block, "page_obj": block.page,
    })


@manager_required
@require_POST
def block_delete(request, pk):
    block = get_object_or_404(Block.objects.select_related("page"), pk=pk)
    page_pk = block.page.pk
    block.delete()
    messages.info(request, "Section removed.")
    return redirect("cms:page_edit", pk=page_pk)


@manager_required
@require_POST
def block_move(request, pk, direction):
    """Swap a block with its neighbour.

    Swapping the two `order` values (rather than renumbering the whole page)
    keeps the operation to two writes and leaves every other block untouched.
    """
    block = get_object_or_404(Block.objects.select_related("page"), pk=pk)
    siblings = list(block.page.blocks.all())
    index = siblings.index(block)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(siblings):
        other = siblings[target]
        block.order, other.order = other.order, block.order
        # save() re-sanitises the body, which is harmless but wasteful here.
        Block.objects.filter(pk=block.pk).update(order=block.order)
        Block.objects.filter(pk=other.pk).update(order=other.order)
    return redirect("cms:page_edit", pk=block.page.pk)


# ==========================================================================
# MANAGER — menus
# ==========================================================================
@manager_required
def menu_list(request):
    grouped = []
    for value, label in MENU_LOCATIONS:
        grouped.append({
            "value": value, "label": label,
            "items": MenuItem.objects.filter(location=value).select_related("page"),
        })
    return render(request, "cms/menu_list.html", {"groups": grouped})


@manager_required
def menu_create(request):
    if request.method == "POST":
        form = MenuItemForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Menu item added.")
            return redirect("cms:menu_list")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = MenuItemForm(initial={"location": request.GET.get("location", "header")})
    return render(request, "cms/menu_form.html", {"form": form, "is_create": True})


@manager_required
def menu_edit(request, pk):
    item = get_object_or_404(MenuItem, pk=pk)
    if request.method == "POST":
        form = MenuItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Menu item updated.")
            return redirect("cms:menu_list")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = MenuItemForm(instance=item)
    return render(request, "cms/menu_form.html", {"form": form, "item": item})


@manager_required
@require_POST
def menu_delete(request, pk):
    get_object_or_404(MenuItem, pk=pk).delete()
    messages.info(request, "Menu item removed.")
    return redirect("cms:menu_list")


# ==========================================================================
# MANAGER — media
# ==========================================================================
@manager_required
def media_library(request):
    if request.method == "POST":
        form = MediaAssetForm(request.POST, request.FILES)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.uploaded_by = request.user
            asset.save()
            messages.success(request, "Image uploaded.")
            return redirect("cms:media")
        messages.error(request, "That image could not be saved — see the message below.")
    else:
        form = MediaAssetForm()
    return render(request, "cms/media.html", {
        "form": form, "assets": MediaAsset.objects.all(),
    })


@manager_required
@require_POST
def media_delete(request, pk):
    get_object_or_404(MediaAsset, pk=pk).delete()
    messages.info(request, "Image deleted.")
    return redirect("cms:media")
