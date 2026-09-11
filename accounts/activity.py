"""Login-activity tracking: first access, last access and last IP address.

Django already maintains ``User.last_login``. What it does not track is when a
user was last *active* (as opposed to last authenticated), which is what the
account menu shows. ``LastSeenMiddleware`` fills that gap with a throttled
write so an active session costs at most one extra UPDATE per minute.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

# Only touch the DB once per this many seconds of continuous browsing.
LAST_SEEN_THROTTLE_SECONDS = 60
SESSION_KEY = "_last_seen_written"


def client_ip(request):
    """Return the observed peer address, not an untrusted proxy header."""
    return request.META.get("REMOTE_ADDR") or None


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    now = timezone.now()
    fields = ["last_seen_at"]
    user.last_seen_at = now
    # Some authentication paths (notably Django's test client `login()` helper
    # and programmatic `login()` calls) hand over a bare request with no
    # REMOTE_ADDR. Keep the last address we genuinely observed rather than
    # blanking a known IP.
    ip = client_ip(request) if request is not None else None
    if ip:
        user.last_login_ip = ip
        fields.append("last_login_ip")
    if user.first_seen_at is None:
        # Backfill from date_joined so pre-existing accounts show a sensible
        # "first access" rather than jumping to today.
        user.first_seen_at = user.date_joined or now
        fields.append("first_seen_at")
    user.save(update_fields=fields)
    if request is not None:
        request.session[SESSION_KEY] = now.timestamp()


class LastSeenMiddleware:
    """Keep ``last_seen_at`` fresh without writing on every single request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or getattr(request, "control_read_only", None):
            return response

        now = timezone.now()
        written = request.session.get(SESSION_KEY)
        if written and (now.timestamp() - written) < LAST_SEEN_THROTTLE_SECONDS:
            return response

        # queryset.update() avoids a full model save (and any save signals) on
        # what is a hot path for every authenticated page view.
        updates = {"last_seen_at": now}
        ip = client_ip(request)
        if ip:
            updates["last_login_ip"] = ip
        get_user_model().objects.filter(pk=user.pk).update(**updates)
        request.session[SESSION_KEY] = now.timestamp()
        return response
