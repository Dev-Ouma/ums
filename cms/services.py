"""Rendering helpers shared by the public views."""
from datetime import date

from django.shortcuts import render

from .models import Page


def _dynamic_context(block):
    """Live data for blocks that pull from the rest of the system.

    Imported lazily: `university.models` imports `accounts`, and pulling that
    chain in at module import time would make `cms` depend on app-loading
    order.
    """
    from university.models import Course, Department, Event

    limit = block.item_limit or 6
    if block.block_type == "departments":
        return {"items": Department.objects.all()[:limit]}
    if block.block_type == "courses":
        return {"items": Course.objects.select_related("department")[:limit]}
    if block.block_type == "events":
        return {"items": Event.objects.filter(date__gte=date.today())[:limit]}
    return {}


def block_context(block):
    ctx = {"block": block}
    ctx.update(_dynamic_context(block))
    return ctx


def get_page(slug, user=None):
    """The page for `slug`, or None. Drafts resolve only for site managers."""
    return (Page.objects.visible_to(user)
            .prefetch_related("blocks")
            .filter(slug=slug)
            .first())


def render_page(request, page, extra=None):
    ctx = {
        "page": page,
        "blocks": [block_context(b) for b in page.visible_blocks()],
        "is_preview": not page.is_published,
    }
    ctx.update(extra or {})
    return render(request, page.layout_template, ctx)
