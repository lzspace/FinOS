"""Stable category codes for Finance Extension 0.3.0."""

from __future__ import annotations

CATEGORY_CODES = (
    "INCOME_SALARY",
    "INCOME_OTHER",
    "HOUSING_RENT",
    "HOUSING_UTILITIES",
    "FOOD_GROCERIES",
    "FOOD_RESTAURANTS",
    "MOBILITY_PUBLIC_TRANSPORT",
    "MOBILITY_FUEL",
    "HEALTH",
    "INSURANCE",
    "LEISURE",
    "SUBSCRIPTIONS",
    "EDUCATION",
    "FEES",
    "TAXES",
    "OTHER_EXPENSE",
    "UNCLASSIFIED",
)


def require_category(code: str) -> str:
    if code not in CATEGORY_CODES:
        raise ValueError("FINANCE_CATEGORY_UNKNOWN")
    return code
