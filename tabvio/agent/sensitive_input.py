from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True)
class PendingSensitiveInput:
    id: UUID
    element_index: int
    prompt: str
    kind: str = "mfa_code"


class SensitiveInputChannel:
    """One in-memory sensitive-input request bound to one browser run."""

    def __init__(self):
        self._pending: PendingSensitiveInput | None = None

    @property
    def pending(self) -> PendingSensitiveInput | None:
        return self._pending

    def begin(self, element_index: int, prompt: str) -> PendingSensitiveInput:
        if self._pending is not None:
            if (
                self._pending.element_index == element_index
                and self._pending.prompt == prompt
            ):
                return self._pending
            raise RuntimeError("Another sensitive input request is already pending")
        self._pending = PendingSensitiveInput(
            id=uuid4(), element_index=element_index, prompt=prompt
        )
        return self._pending

    def require(self, request_id: UUID) -> PendingSensitiveInput:
        if self._pending is None or self._pending.id != request_id:
            raise ValueError("The sensitive input request is no longer active")
        return self._pending

    def clear(self, request_id: UUID) -> None:
        self.require(request_id)
        self._pending = None
