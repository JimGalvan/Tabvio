import copy
from typing import Any


def inline_references(schema: dict[str, Any]) -> dict[str, Any]:
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
