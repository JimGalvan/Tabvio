from urllib.parse import urlsplit

from tabvio.browser.constants import PAYMENT_HANDOFF_SIGNAL_KINDS
from tabvio.browser.models import PaymentSignal


class PaymentDetectionResult:
    def __init__(self, url: str = "", signals: tuple[PaymentSignal, ...] = ()) -> None:
        self._url = url
        self._signals = signals

    @property
    def url(self) -> str:
        return self._url

    @property
    def signals(self) -> tuple[PaymentSignal, ...]:
        return self._signals

    @property
    def get_signals(self) -> list[str]:
        payment_signals = []
        for signal in self._signals:
            if signal.type in PAYMENT_HANDOFF_SIGNAL_KINDS:
                payment_signals.append(f"{signal.type}:{signal.value}")
        return sorted(payment_signals)

    @property
    def fingerprint(self) -> str | None:
        signals = self.get_signals
        if not signals:
            return None
        url = urlsplit(self._url)
        return "|".join([f"{url.scheme}://{url.netloc}{url.path}", *signals])

    def needs_handoff(self, acknowledged: str | None) -> bool:
        fingerprint = self.fingerprint
        return fingerprint is not None and fingerprint != acknowledged

    def describe(self) -> str:
        signals = self.get_signals
        if not signals:
            return ""

        return (
            "<payment-surface>This page can take a payment "
            f"({', '.join(signals)}). Acting on it is blocked; the "
            "user enters payment details themselves."
            "</payment-surface>"
        )

    def __repr__(self) -> str:
        return (
            f"PaymentDetectionResult(url={self._url!r}, "
            f"signals={self.get_signals})"
        )
