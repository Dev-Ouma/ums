from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from university.integrations.base import BaseIntegrationAdapter


@dataclass
class PaymentResult:
    success: bool
    status: str
    transaction_reference: str
    provider_reference: str = ""
    checkout_url: str = ""
    instructions: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


class BasePaymentProviderAdapter(BaseIntegrationAdapter):
    """
    Base interface for all institutional payment provider adapters.
    Provides standardized methods for initiating, verifying, testing, and handling callbacks.

    Subclasses BaseIntegrationAdapter (university/integrations/base.py) to
    pick up call_with_audit -- purely additive, every existing method and
    subclass (mpesa.py/card.py/bank.py) is unchanged.
    """
    integration_name = "Payments"

    def __init__(self, fee_account):
        self.fee_account = fee_account

    def initiate_payment(self, payment, request, extra_data: Optional[Dict[str, Any]] = None) -> PaymentResult:
        raise NotImplementedError("Subclasses must implement initiate_payment")

    def verify_payment(self, payment, provider_payload: Optional[Dict[str, Any]] = None) -> PaymentResult:
        raise NotImplementedError("Subclasses must implement verify_payment")

    def process_callback(self, request) -> PaymentResult:
        raise NotImplementedError("Subclasses must implement process_callback")

    def test_connection(self) -> Tuple[bool, str, Dict[str, Any]]:
        raise NotImplementedError("Subclasses must implement test_connection")
