from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True)
class PendingSensitiveInput:
    id: UUID
    element_index: int
    prompt: str
    kind: str = "mfa_code"
    submit_element_index: int | None = None


class SensitiveInputChannel:
    """One in-memory sensitive-input request bound to one browser run."""

    def __init__(self):
        self._pending: PendingSensitiveInput | None = None
        self._withdrawn_reason: str | None = None

    @property
    def pending(self) -> PendingSensitiveInput | None:
        return self._pending

    @property
    def is_withdrawn(self) -> bool:
        """The code box is gone, but the agent step is still parked on it."""
        return self._withdrawn_reason is not None

    def begin(
        self,
        element_index: int,
        prompt: str,
        submit_element_index: int | None = None,
    ) -> PendingSensitiveInput:
        if self._pending is not None:
            if (
                self._pending.element_index == element_index
                and self._pending.prompt == prompt
            ):
                return self._pending
            raise RuntimeError("Another sensitive input request is already pending")
        self._pending = PendingSensitiveInput(
            id=uuid4(),
            element_index=element_index,
            prompt=prompt,
            submit_element_index=submit_element_index,
        )
        self._withdrawn_reason = None
        return self._pending

    def require(self, request_id: UUID) -> PendingSensitiveInput:
        if self._pending is None or self._pending.id != request_id:
            raise ValueError("The sensitive input request is no longer active")
        return self._pending

    def clear(self, request_id: UUID) -> None:
        """Finish a request, whether or not it was withdrawn while it waited."""
        if self._pending is None:
            self._withdrawn_reason = None
            return
        self.require(request_id)
        self._pending = None
        self._withdrawn_reason = None

    def withdraw(self, reason: str) -> PendingSensitiveInput | None:
        """Take the code box off the screen, remembering what to tell the agent."""
        withdrawn = self._pending
        if withdrawn is None:
            return None
        self._pending = None
        self._withdrawn_reason = reason
        return withdrawn

    def take_withdrawn_reason(self) -> str | None:
        """Consume the reason so the agent hears about it exactly once."""
        reason = self._withdrawn_reason
        self._withdrawn_reason = None
        return reason
