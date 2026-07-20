"""Validate every contract schema and every checked-in example with jsonschema."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "extensions/finance"
INSTALLED_ROOT = Path(__file__).resolve().parents[1] / "extensions/finance"
ROOT = SOURCE_ROOT if SOURCE_ROOT.exists() else INSTALLED_ROOT


def main() -> int:
    schemas = list((ROOT / "schemas").glob("*.schema.json"))
    documents_by_name = {
        path.name: json.loads(path.read_text(encoding="utf-8")) for path in schemas
    }
    documents = list(documents_by_name.values())
    registry = Registry().with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents
    )
    for document in documents:
        Draft202012Validator.check_schema(document)
    for example_path in (ROOT / "examples").glob("*.json"):
        schema_name = (
            "commands.schema.json"
            if example_path.name.endswith(".command.json")
            else "events.schema.json"
        )
        schema = documents_by_name[schema_name]
        errors = list(
            Draft202012Validator(
                schema, registry=registry, format_checker=FormatChecker()
            ).iter_errors(json.loads(example_path.read_text(encoding="utf-8")))
        )
        if errors:
            raise SystemExit(f"Invalid example {example_path.name}: {errors[0].json_path}")
    print(f"OK: {len(schemas)} schemas and checked-in examples validate under Draft 2020-12.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
