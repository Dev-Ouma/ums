"""Template helpers for CMS blocks."""
import html as html_module

from django import template

register = template.Library()


@register.filter
def rows(value):
    """Split a block body into rows of pipe-separated cells.

    Repeating blocks (stats, feature grids, cards) need a list of items, but
    a full repeater UI is a lot of machinery for what editors can express in
    one textarea:

        46 | Students | fa-user-graduate
        10 | Faculty  | fa-chalkboard-user

    Entities are unescaped because the body was HTML-escaped on save; these
    cells are re-escaped by the template on output.
    """
    out = []
    for line in (value or "").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append([html_module.unescape(cell.strip()) for cell in line.split("|")])
    return out


@register.filter
def cell(row, index):
    """Return row[index], or an empty string when the editor left it out."""
    try:
        return row[int(index)]
    except (IndexError, ValueError, TypeError):
        return ""
