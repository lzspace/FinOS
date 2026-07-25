"""Stable category codes for Finance Extension 0.3.0."""

from __future__ import annotations

import re

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

CUSTOM_CATEGORY_PATTERN = re.compile(r"^CUSTOM_[A-Z0-9_]{1,48}$")


def require_category(code: str) -> str:
    if code not in CATEGORY_CODES and not CUSTOM_CATEGORY_PATTERN.fullmatch(code):
        raise ValueError("FINANCE_CATEGORY_UNKNOWN")
    return code
