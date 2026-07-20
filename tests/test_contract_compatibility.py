from __future__ import annotations

import unittest

from finance_extension.contract_compatibility import (
    ContractCompatibilityError,
    classify_contract_change,
    compare_contract_catalogs,
    reject_undeclared_breaking_change,
)


def catalog(schema: dict[str, object]) -> dict[str, object]:
    return {"catalog_format": 1, "schemas": {"sample.schema.json": schema}}


class ContractCompatibilityTests(unittest.TestCase):
    def test_optional_field_is_minor_and_description_is_patch(self) -> None:
        old = catalog({"type": "object", "properties": {"id": {"type": "string"}}})
        documented = catalog(
            {
                "type": "object",
                "description": "documented",
                "properties": {"id": {"type": "string"}},
            }
        )
        extended = catalog(
            {
                "type": "object",
                "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
            }
        )
        self.assertEqual(classify_contract_change(old, documented), "PATCH")
        self.assertEqual(classify_contract_change(old, extended), "MINOR")

    def test_removed_or_newly_required_field_is_major(self) -> None:
        old = catalog(
            {
                "type": "object",
                "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
            }
        )
        removed = catalog({"type": "object", "properties": {"id": {"type": "string"}}})
        required = catalog(
            {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
            }
        )
        self.assertEqual(compare_contract_catalogs(old, removed)["classification"], "MAJOR")
        self.assertEqual(classify_contract_change(old, required), "MAJOR")

    def test_undeclared_breaking_change_fails_release(self) -> None:
        old = catalog({"type": "string", "enum": ["A", "B"]})
        new = catalog({"type": "string", "enum": ["A"]})
        with self.assertRaisesRegex(
            ContractCompatibilityError, "FINANCE_CONTRACT_BREAKING_CHANGE_UNDECLARED"
        ):
            reject_undeclared_breaking_change(
                old, new, previous_version="1.0.0", current_version="1.1.0"
            )


if __name__ == "__main__":
    unittest.main()
