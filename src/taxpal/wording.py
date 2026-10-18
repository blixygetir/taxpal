"""Small helpers for messages people read."""


def plural(n: int, one: str, many: str | None = None) -> str:
    """plural(1, "transaction") -> "1 transaction", plural(3, "category", "categories") -> "3 categories"."""
    return f"{n} {one if n == 1 else many or one + 's'}"
