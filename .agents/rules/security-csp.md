---
trigger: always_on
---

# Security & CSP Rules — UMS Project

## CSP Rules

1. **Always update CSP before adding external resources** — If you embed a new
   map, CDN, font, or iframe, you MUST update `_CSP_DIRECTIVES` in
   `config/settings.py` BEFORE the template change.

2. **Minimum required domain only** — Add only the specific domain, not a
   wildcard. `https://unpkg.com` not `*`.

3. **Match the directive to the resource type:**
   - JS files → `script-src`
   - CSS → `style-src`
   - Fonts → `font-src`
   - Iframes/maps → `frame-src`
   - XHR/fetch → `connect-src`
   - Images → `img-src`

4. **Never use `unsafe-eval`** — If a library requires `unsafe-eval`, find an
   alternative or vendor the library.

5. **`X-Frame-Options: DENY`** — The app must not be embeddable in third-party
   iframes. Do not weaken this setting.

## Production Security Rules

6. **`DEBUG = False` always in production** — Never deploy with `DEBUG = True`.

7. **Secrets from environment only** — `SECRET_KEY`, database passwords, and
   API keys must come from environment variables, never hardcoded.

8. **Run `manage.py check --deploy` before every production release.**

9. **Audit dependencies monthly** — Run `.venv/bin/pip-audit` before releases.

10. **HTTPS-only in production** — `SECURE_SSL_REDIRECT = True`,
    `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`.
