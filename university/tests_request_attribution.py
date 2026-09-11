from django.test import RequestFactory, TestCase

from university.audit_services import get_client_ip


class RequestAttributionSecurityTests(TestCase):
    def test_audit_ip_ignores_spoofed_forwarded_header(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="10.20.30.40",
            HTTP_X_FORWARDED_FOR="198.51.100.77, 10.20.30.40",
        )

        self.assertEqual(get_client_ip(request), "10.20.30.40")

