from .base import BasePaymentProviderAdapter
from .mpesa import MpesaProviderAdapter
from .card import CardGatewayAdapter
from .bank import BankTransferAdapter


def get_payment_adapter(fee_account) -> BasePaymentProviderAdapter:
    """
    Factory function returning the provider adapter instance for a given FeeAccount.
    """
    acc_type = getattr(fee_account, "account_type", "")
    if acc_type in ["MPESA_PAYBILL", "MPESA_TILL"]:
        return MpesaProviderAdapter(fee_account)
    elif acc_type == "CARD_GATEWAY":
        return CardGatewayAdapter(fee_account)
    elif acc_type == "BANK_ACCOUNT":
        return BankTransferAdapter(fee_account)
    else:
        # Default fallback to bank / manual reference instructions
        return BankTransferAdapter(fee_account)
