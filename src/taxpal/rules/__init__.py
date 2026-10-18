from functools import cache
from pathlib import Path

import yaml

RULES_DIR = Path(__file__).parent


@cache
def tax_year() -> dict:
    return yaml.safe_load((RULES_DIR / "ty2026.yaml").read_text())


@cache
def taxonomy() -> dict[str, dict]:
    return yaml.safe_load((RULES_DIR / "taxonomy.yaml").read_text())["categories"]


@cache
def irs_documents() -> dict[str, dict]:
    """Plain-English summaries of the IRS documents TaxPal searches."""
    return yaml.safe_load((RULES_DIR / "irs_documents.yaml").read_text())["documents"]


def category(key: str) -> dict:
    try:
        return taxonomy()[key]
    except KeyError:
        raise ValueError(f"Unknown category: {key!r}") from None
