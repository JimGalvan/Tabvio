from typing import Generic, TypeVar

Target = TypeVar("Target")


class TargetRegistry(Generic[Target]):
    """Gives live tabs or frames stable ids like "tab:0" across observations."""

    def __init__(self, prefix: str):
        self._prefix = prefix
        self._by_id: dict[str, Target] = {}
        self._next_id = 0

    def sync(self, live_items: list[Target]) -> dict[str, Target]:
        """Drop ids for items that are gone, keep the rest, and number the new ones."""
        live_by_id = {}
        for item_id, item in self._by_id.items():
            if item in live_items:
                live_by_id[item_id] = item
        self._by_id = live_by_id
        registered = list(self._by_id.values())
        for item in live_items:
            if item not in registered:
                self._by_id[f"{self._prefix}:{self._next_id}"] = item
                self._next_id += 1
                registered.append(item)
        return self._by_id

    def get(self, item_id: str) -> Target | None:
        return self._by_id.get(item_id)

    def reset(self) -> None:
        self._by_id = {}
        self._next_id = 0
