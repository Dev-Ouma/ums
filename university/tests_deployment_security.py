from django.test import TestCase, override_settings

from university.security_compliance_services import build_security_compliance_summary


class DeploymentSecurityTests(TestCase):
    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": "redis://cache/1"}},
    )
    def test_production_shared_cache_is_reported_as_ready(self):
        summary = build_security_compliance_summary()
        check = next(item for item in summary["runtime_checks"] if item["label"] == "Shared security cache")
        self.assertEqual(check["status"], "PASS")

    @override_settings(DEBUG=True)
    def test_development_local_cache_is_explicitly_non_production(self):
        summary = build_security_compliance_summary()
        check = next(item for item in summary["runtime_checks"] if item["label"] == "Shared security cache")
        self.assertEqual(check["status"], "WARN")
