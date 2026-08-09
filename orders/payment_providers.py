from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    """Contract for a future hosted online payment provider.

    A provider return/success page is never authoritative. Implementations must
    confirm payments only from a verified, idempotently processed webhook.
    """

    @abstractmethod
    def create_payment(self, *, payment):
        raise NotImplementedError

    @abstractmethod
    def get_payment_url(self, *, payment):
        raise NotImplementedError

    @abstractmethod
    def verify_webhook(self, *, raw_body, headers):
        raise NotImplementedError

    @abstractmethod
    def handle_webhook(self, *, raw_body, headers):
        """Validate signature, payment/order/amount/currency and event id."""
        raise NotImplementedError


PROVIDERS = {}


def register_provider(name, provider):
    if not isinstance(provider, PaymentProvider):
        raise TypeError("provider must implement PaymentProvider")
    PROVIDERS[name] = provider


def get_provider(name):
    return PROVIDERS.get(name)
