from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from university.events import account_status_changed, dispatch_event
from university.models import AuditLog
from university.webhook_models import WebhookDelivery, WebhookEndpoint
from university.webhook_services import WebhookURLError, validate_webhook_url


class WebhookURLSSRFValidationTests(TestCase):
    """
    Regression tests for the SSRF fix: an admin-configured webhook URL
    must never resolve to an internal/private/loopback/link-local address,
    since this server itself makes unattended, retried outbound POSTs to
    it on a schedule.
    """
    def test_rejects_loopback_ip(self):
        with self.assertRaises(WebhookURLError):
            validate_webhook_url("http://127.0.0.1/hook")

    def test_rejects_localhost_hostname(self):
        with self.assertRaises(WebhookURLError):
            validate_webhook_url("http://localhost/hook")

    def test_rejects_cloud_metadata_endpoint(self):
        with self.assertRaises(WebhookURLError):
            validate_webhook_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_private_rfc1918_address(self):
        with self.assertRaises(WebhookURLError):
            validate_webhook_url("http://10.0.0.5/hook")

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(WebhookURLError):
            validate_webhook_url("ftp://8.8.8.8/hook")

    def test_accepts_a_public_ip_literal(self):
        # Uses a numeric IP literal (Google public DNS) so the test has no
        # real DNS dependency -- getaddrinfo resolves IP literals locally.
        validate_webhook_url("https://8.8.8.8/hook")  # should not raise


class WebhookAdminViewSSRFTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="wh.admin", email="wh.admin@example.com", password="pass12345", role=Role.ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_create_rejects_a_loopback_url(self):
        response = self.client.post(reverse("university:webhook_create"), {
            "name": "Malicious Sink", "url": "http://127.0.0.1:8000/steal", "event_kinds": [],
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WebhookEndpoint.objects.filter(name="Malicious Sink").exists())

    def test_create_accepts_a_public_ip_literal(self):
        response = self.client.post(reverse("university:webhook_create"), {
            "name": "Public Sink", "url": "https://8.8.8.8/hook", "event_kinds": [],
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(WebhookEndpoint.objects.filter(name="Public Sink").exists())

    def test_edit_rejects_changing_url_to_a_metadata_endpoint(self):
        endpoint = WebhookEndpoint.objects.create(
            name="Edit Target", url="https://8.8.8.8/hook", event_kinds=[], is_active=True,
        )
        response = self.client.post(reverse("university:webhook_edit", args=[endpoint.pk]), {
            "name": "Edit Target", "url": "http://169.254.169.254/latest/meta-data/", "event_kinds": [],
        })
        self.assertEqual(response.status_code, 200)
        endpoint.refresh_from_db()
        self.assertEqual(endpoint.url, "https://8.8.8.8/hook")


class QueueWebhookDeliveriesTests(TestCase):
    def test_dispatching_an_event_queues_a_delivery_for_a_subscribed_endpoint(self):
        endpoint = WebhookEndpoint.objects.create(
            name="Test Sink", url="https://example.com/hook",
            event_kinds=["account_status_changed"], is_active=True)

        user = User.objects.create_user(username="wh.user", email="wh.user@example.com",
                                        password="pass12345", role=Role.STUDENT)
        dispatch_event(account_status_changed, user=user, old_status="ACTIVE", new_status="SUSPENDED")

        self.assertEqual(WebhookDelivery.objects.filter(endpoint=endpoint).count(), 1)
        delivery = WebhookDelivery.objects.get(endpoint=endpoint)
        self.assertEqual(delivery.status, WebhookDelivery.Status.PENDING)
        self.assertEqual(delivery.payload["data"]["new_status"], "SUSPENDED")

    def test_an_endpoint_not_subscribed_to_the_event_gets_no_delivery(self):
        WebhookEndpoint.objects.create(
            name="Unrelated Sink", url="https://example.com/other",
            event_kinds=["fee_payment_confirmed"], is_active=True)

        user = User.objects.create_user(username="wh.user2", email="wh.user2@example.com",
                                        password="pass12345", role=Role.STUDENT)
        dispatch_event(account_status_changed, user=user, old_status="ACTIVE", new_status="SUSPENDED")

        self.assertEqual(WebhookDelivery.objects.count(), 0)

    def test_an_inactive_endpoint_gets_no_delivery(self):
        WebhookEndpoint.objects.create(
            name="Paused Sink", url="https://example.com/paused",
            event_kinds=["account_status_changed"], is_active=False)

        user = User.objects.create_user(username="wh.user3", email="wh.user3@example.com",
                                        password="pass12345", role=Role.STUDENT)
        dispatch_event(account_status_changed, user=user, old_status="ACTIVE", new_status="SUSPENDED")

        self.assertEqual(WebhookDelivery.objects.count(), 0)


class WebhookEndpointModelTests(TestCase):
    def test_a_secret_is_auto_generated_on_creation(self):
        endpoint = WebhookEndpoint.objects.create(
            name="Auto Secret", url="https://example.com/hook", event_kinds=[])
        self.assertTrue(endpoint.secret)
        self.assertEqual(len(endpoint.secret), 64)

    def test_an_explicit_secret_is_not_overwritten(self):
        endpoint = WebhookEndpoint.objects.create(
            name="Explicit Secret", url="https://example.com/hook", event_kinds=[], secret="myfixedsecret")
        self.assertEqual(endpoint.secret, "myfixedsecret")


class DeliverPendingWebhooksTests(TestCase):
    def setUp(self):
        self.endpoint = WebhookEndpoint.objects.create(
            name="Delivery Target", url="https://example.com/hook",
            event_kinds=["account_status_changed"], is_active=True)
        self.delivery = WebhookDelivery.objects.create(
            endpoint=self.endpoint, event_kind="account_status_changed",
            payload={"event": "account_status_changed", "data": {}})

    def test_successful_delivery_marks_success_and_records_response(self):
        from university.webhook_services import deliver_pending_webhooks

        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False

        with patch("university.webhook_services._NO_REDIRECT_OPENER.open", return_value=mock_response):
            delivered = deliver_pending_webhooks()

        self.assertEqual(delivered, 1)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.SUCCESS)
        self.assertEqual(self.delivery.response_status, 200)
        self.assertEqual(self.delivery.attempt_count, 1)

    def test_failed_delivery_schedules_a_retry_with_backoff(self):
        from university.webhook_services import deliver_pending_webhooks

        with patch("university.webhook_services._NO_REDIRECT_OPENER.open", side_effect=OSError("connection refused")):
            deliver_pending_webhooks()

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.RETRYING)
        self.assertEqual(self.delivery.attempt_count, 1)
        self.assertGreater(self.delivery.next_attempt_at, timezone.now())

    def test_delivery_is_marked_failed_after_max_attempts_exhausted(self):
        from university.webhook_services import deliver_pending_webhooks

        self.delivery.attempt_count = self.delivery.max_attempts - 1
        self.delivery.save()

        with patch("university.webhook_services._NO_REDIRECT_OPENER.open", side_effect=OSError("connection refused")):
            deliver_pending_webhooks()

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.FAILED)

    def test_a_delivery_not_yet_due_is_skipped(self):
        from university.webhook_services import deliver_pending_webhooks

        self.delivery.next_attempt_at = timezone.now() + timezone.timedelta(hours=1)
        self.delivery.save()

        delivered = deliver_pending_webhooks()
        self.assertEqual(delivered, 0)


class WebhookAdminViewTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="wh.admin", email="wh.admin@example.com", password="pass12345",
            role=Role.ADMIN, is_staff=True, is_superuser=True)
        self.staff_only_user = User.objects.create_user(
            username="wh.staff", email="wh.staff@example.com", password="pass12345",
            role=Role.FACULTY, is_staff=True)

    def test_staff_without_admin_permission_cannot_manage_webhooks(self):
        client = Client()
        client.force_login(self.staff_only_user)
        res = client.get(reverse("university:webhook_list"))
        self.assertEqual(res.status_code, 403)

    def test_admin_can_create_and_list_a_webhook_endpoint(self):
        client = Client()
        client.force_login(self.admin_user)
        res = client.post(reverse("university:webhook_create"), {
            "name": "My Integration",
            "url": "https://example.com/webhooks/ums",
            "event_kinds": ["exam_marks_published"],
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(WebhookEndpoint.objects.filter(name="My Integration").exists())
        self.assertTrue(AuditLog.objects.filter(
            entity="WebhookEndpoint", action=AuditLog.Action.CREATE).exists())

        res_list = client.get(reverse("university:webhook_list"))
        self.assertContains(res_list, "My Integration")

    def test_admin_can_delete_a_webhook_endpoint(self):
        endpoint = WebhookEndpoint.objects.create(
            name="To Delete", url="https://example.com/hook", event_kinds=[])
        client = Client()
        client.force_login(self.admin_user)
        res = client.post(reverse("university:webhook_delete", args=[endpoint.pk]))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(WebhookEndpoint.objects.filter(pk=endpoint.pk).exists())

    def test_webhook_list_escapes_endpoint_name_for_the_inline_confirm_script(self):
        # Regression: the delete button's onsubmit="confirm('...name...')" used
        # to interpolate the raw (HTML-escaped only) name into a JS string.
        # Django's HTML escaping turns a quote into &#x27;, but the browser
        # HTML-attribute-decodes that back to a literal ' before the JS
        # engine parses onsubmit -- so a name containing a quote could break
        # out of the confirm() string and execute arbitrary JS in an admin's
        # browser. |escapejs must be applied so quotes become ' instead,
        # which survives HTML-attribute decoding unchanged.
        WebhookEndpoint.objects.create(
            name="Evil'); alert('xss", url="https://example.com/hook", event_kinds=[],
        )
        client = Client()
        client.force_login(self.admin_user)
        res = client.get(reverse("university:webhook_list"))
        html = res.content.decode()
        self.assertNotIn("confirm('Delete webhook endpoint \\'Evil');", html)
        self.assertIn("\\u0027", html)


class WebhookDeliveryRedirectSSRFTests(TestCase):
    """
    Regression tests: validate_webhook_url() only runs when an admin saves
    an endpoint's URL. urllib.request.urlopen() follows HTTP redirects by
    default, so a delivery request to an already-validated public URL could
    still be redirected by that endpoint (at delivery time, on the
    unattended background schedule) to an internal/private target,
    completely bypassing the SSRF check. Delivery must refuse to follow
    any redirect.
    """
    def setUp(self):
        self.endpoint = WebhookEndpoint.objects.create(
            name="Redirect Test", url="https://example.com/hook",
            event_kinds=["account_status_changed"], is_active=True)
        self.delivery = WebhookDelivery.objects.create(
            endpoint=self.endpoint, event_kind="account_status_changed",
            payload={"event": "account_status_changed", "data": {}})

    def test_delivery_does_not_follow_a_redirect_to_an_internal_target(self):
        from urllib.error import HTTPError
        from university.webhook_services import deliver_pending_webhooks

        def _raise_redirect(*args, **kwargs):
            raise HTTPError(
                "http://169.254.169.254/latest/meta-data/", 302,
                "Refusing to follow webhook redirect", {}, None,
            )

        with patch("university.webhook_services._NO_REDIRECT_OPENER.open", side_effect=_raise_redirect):
            deliver_pending_webhooks()

        self.delivery.refresh_from_db()
        # Treated as a failed delivery attempt, never as a followed request.
        self.assertIn(self.delivery.status, (WebhookDelivery.Status.RETRYING, WebhookDelivery.Status.FAILED))
        self.assertEqual(self.delivery.response_status, 302)
