"""
Single discoverable lookup of every external integration point in UMS.

Not wired into any admin UI yet -- it exists so a future integration (LMS,
Zoom, HR/SMHR -- see university/integration_views.py for the standing
wishlist) has an obvious place to register itself, and so "what does this
system integrate with" has one place to answer from instead of grepping.
"""


def _payments_factory(fee_account):
    from university.payment_providers.registry import get_payment_adapter
    return get_payment_adapter(fee_account)


def _sms_factory(**kwargs):
    from university.integrations.sms import SmsProviderAdapter
    return SmsProviderAdapter(**kwargs)


def _email_factory(**kwargs):
    from university.email_services import get_email_config
    return get_email_config()


INTEGRATION_KINDS = {
    "payments": _payments_factory,
    "sms": _sms_factory,
    "email": _email_factory,
}


def get_integration(kind, *args, **kwargs):
    """Look up an integration by kind ("payments", "sms", "email", ...)."""
    factory = INTEGRATION_KINDS.get(kind)
    if not factory:
        raise KeyError(f"No integration registered for kind '{kind}'. "
                       f"Known kinds: {sorted(INTEGRATION_KINDS)}")
    return factory(*args, **kwargs)
