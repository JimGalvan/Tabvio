from typing import Any


class SchemaUtils:
    @staticmethod
    def inline_references(schema: dict[str, Any]) -> dict[str, Any]:
        return SchemaUtils.replace(schema, schema.get("$defs", {}))

    @staticmethod
    def replace(node: Any, definitions: dict[str, Any]) -> Any:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if reference:
                name = reference.rsplit("/", 1)[-1]
                return SchemaUtils.replace(definitions[name], definitions)
            replaced = {}
            for key, value in node.items():
                if key in {"$defs", "discriminator", "default"}:
                    continue
                if key == "oneOf":
                    key = "anyOf"
                replaced[key] = SchemaUtils.replace(value, definitions)
            if replaced.get("type") == "object":
                replaced["additionalProperties"] = False
                replaced["required"] = list(replaced["properties"])
            return replaced
        if isinstance(node, list):
            replaced = []
            for item in node:
                replaced.append(SchemaUtils.replace(item, definitions))
            return replaced
        return node
