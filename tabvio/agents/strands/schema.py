import copy
from typing import Any


def inline_references(schema: dict[str, Any]) -> dict[str, Any]:
    # Pydantic hoists each step type into $defs and points at it with $ref.
    # Not every model provider resolves those, so hand them a flat schema.
    # Safe only because no browser step refers back to itself.
    definitions = schema.get("$defs", {})
    flattened = _replace(schema, definitions)
    flattened.pop("$defs", None)
    return flattened


def _replace(node: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str):
            name = reference.rsplit("/", 1)[-1]
            return _replace(copy.deepcopy(definitions[name]), definitions)

        replaced = {}
        for key, value in node.items():
            # Its mapping points back into $defs, which is about to be dropped.
            # The inlined oneOf already carries the action each branch matches.
            if key == "discriminator":
                continue
            replaced[key] = _replace(value, definitions)
        return replaced

    if isinstance(node, list):
        replaced = []
        for item in node:
            replaced.append(_replace(item, definitions))
        return replaced

    return node
