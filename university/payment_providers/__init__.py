"""
Payment provider abstraction layer for University Management System.
Supports Safaricom M-Pesa (Paybill & Till), Card Gateways, Bank Transfers, and extensible payment adapters.
"""
from .base import PaymentResult, BasePaymentProviderAdapter
from .registry import get_payment_adapter

__all__ = ["PaymentResult", "BasePaymentProviderAdapter", "get_payment_adapter"]
