"""A small allowlist HTML sanitiser.

Block bodies are authored by site managers, who are trusted — but "trusted"
is not the same as "the markup they paste is safe". Editors routinely copy
formatted text out of other websites, which drags along scripts, event
handlers and tracking iframes. Sanitising on save keeps that out of every
visitor's browser, and means a compromised manager account cannot turn the
public site into a payload delivery system.

Deliberately dependency-free: the project ships with Django and Pillow only,
and a sanitiser is not worth a third dependency when the allowlist is this
small.
"""
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "blockquote",
    "ul", "ol", "li", "h2", "h3", "h4", "h5", "h6",
    "a", "span", "div", "small", "hr", "code", "pre",
}

# Tags whose *content* must go too — dropping only the tag would leave the
# script body as visible text, or worse, as markup once re-parsed.
DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "template"}

VOID_TAGS = {"br", "hr"}

ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "span": {"class"},
    "div": {"class"},
    "p": {"class"},
    "code": {"class"},
}

# Anything not in here is refused as a link scheme, which is what stops
# javascript:, data: and vbscript: URLs.
ALLOWED_SCHEMES = {"http", "https", "mailto", "tel"}


def _safe_href(value):
    value = (value or "").strip()
    if not value:
        return None
    # Relative and anchor links carry no scheme and are always fine.
    if value.startswith(("/", "#", "?")):
        return value
    scheme, sep, _ = value.partition(":")
    if not sep:
        return value  # bare relative path such as "catalog/"
    return value if scheme.lower() in ALLOWED_SCHEMES else None


class _Sanitiser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open_tags = []
        self.suppress_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT_TAGS:
            self.suppress_depth += 1
            return
        if self.suppress_depth or tag not in ALLOWED_TAGS:
            return

        allowed = ALLOWED_ATTRS.get(tag, set())
        rendered = []
        for name, value in attrs:
            name = name.lower()
            if name not in allowed:
                continue
            if name == "href":
                value = _safe_href(value)
                if value is None:
                    continue
            rendered.append(f' {name}="{escape(value or "", quote=True)}"')

        # Links that open a new tab must not hand the opener to the target.
        if tag == "a" and any(n == "target" for n, _ in attrs):
            rendered.append(' rel="noopener noreferrer"')

        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{''.join(rendered)}>")
        else:
            self.out.append(f"<{tag}{''.join(rendered)}>")
            self.open_tags.append(tag)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT_TAGS:
            self.suppress_depth = max(0, self.suppress_depth - 1)
            return
        if self.suppress_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self.open_tags:
            # Close anything left dangling inside, so malformed input cannot
            # leak an unbalanced tag into the surrounding page.
            while self.open_tags:
                open_tag = self.open_tags.pop()
                self.out.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data):
        if not self.suppress_depth:
            self.out.append(escape(data, quote=False))

    def close(self):
        super().close()
        while self.open_tags:
            self.out.append(f"</{self.open_tags.pop()}>")

    @property
    def value(self):
        return "".join(self.out)


def clean_html(value):
    """Return `value` with everything outside the allowlist removed."""
    if not value:
        return ""
    parser = _Sanitiser()
    parser.feed(value)
    parser.close()
    return parser.value
