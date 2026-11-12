"""Small helpers for the words people read: a count with its noun in the right number."""


def count(n: int, noun: str, plural: str | None = None) -> str:
    """'1 line', '3 lines'; give the plural when adding an s is wrong ('1 person', '2 people')."""
    return f"{n} {noun if n == 1 else plural or noun + 's'}"
