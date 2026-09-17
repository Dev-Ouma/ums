from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from university.events import account_status_changed, dispatch_event
from university.models import AuditLog
from university.webhook_models import WebhookDelivery, WebhookEndpoint


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

        with patch("urllib.request.urlopen", return_value=mock_response):
            delivered = deliver_pending_webhooks()

        self.assertEqual(delivered, 1)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.SUCCESS)
        self.assertEqual(self.delivery.response_status, 200)
        self.assertEqual(self.delivery.attempt_count, 1)

    def test_failed_delivery_schedules_a_retry_with_backoff(self):
        from university.webhook_services import deliver_pending_webhooks

        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            deliver_pending_webhooks()

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, WebhookDelivery.Status.RETRYING)
        self.assertEqual(self.delivery.attempt_count, 1)
        self.assertGreater(self.delivery.next_attempt_at, timezone.now())

    def test_delivery_is_marked_failed_after_max_attempts_exhausted(self):
        from university.webhook_services import deliver_pending_webhooks

        self.delivery.attempt_count = self.delivery.max_attempts - 1
        self.delivery.save()

        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
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
