"""The wording of the quotation request email: a template the company can edit, with a few fields filled in.

A field is a name in curly braces, like {items}. Only the fields listed here exist; anything else in braces is refused
when the template is saved, so a typo cannot reach a supplier. Filling the fields is plain text replacement, nothing is
evaluated.
"""

import re
from collections.abc import Mapping
from datetime import date

FIELDS = {
    "contact": "the supplier contact's name",
    "company": "your company",
    "reference": "the RFQ number",
    "due_date": "the date to reply by",
    "deliver_to": "where the goods go",
    "items": "the list of items and quantities",
}
REQUIRED = ("items", "reference")  # without them the supplier would not know what to quote or which RFQ it is
MAX_LENGTH = 4000

DEFAULT_RFQ_TEMPLATE = (
    "Dear {contact},\n\n"
    "{company} invites you to quote for the items below.\n\n"
    "Reference: {reference}\n"
    "Please reply by: {due_date}\n"
    "Deliver to: {deliver_to}\n\n"
    "{items}\n\n"
    "Please reply to this email, keeping the subject line, with for each item: the unit price, the GST rate, "
    "the delivery time in days, how long your price is valid, and your payment terms. "
    "An Excel form is attached: fill it in and send it back, or attach your own quotation.\n\n"
    "Regards,\nProcurement, {company}\n"
)

# What a buyer is offered as a start when a supplier asks a question: the question, a promise of an answer, and the
# facts of the request that most questions are about. It is a draft; nobody sends it but the buyer.
QUESTION_REPLY = (
    "Dear {contact},\n\n"
    "Thank you for your question on {reference}:\n\n"
    "> {question}\n\n"
    "Our buyer will answer it shortly. Meanwhile, the request stands as follows.\n"
    "Please reply by: {due_date}\n"
    "Deliver to: {deliver_to}\n\n"
    "{items}\n\n"
    "Regards,\nProcurement, {company}\n"
)

_FIELD = re.compile(r"\{(\w*)\}")


def problems(template: str) -> list[str]:
    """What is wrong with a template, in words for the person editing it (empty when it is fine)."""
    found: list[str] = []
    if len(template) > MAX_LENGTH:
        found.append(f"the text is longer than {MAX_LENGTH} characters")
    names = _FIELD.findall(template)
    for name in sorted(set(names) - set(FIELDS)):
        found.append(f"{{{name}}} is not a field; the fields are " + ", ".join(f"{{{k}}}" for k in FIELDS))
    for name in REQUIRED:
        if name not in names:
            found.append(f"the text must contain {{{name}}} ({FIELDS[name]})")
    if "{" in _FIELD.sub("", template) or "}" in _FIELD.sub("", template):
        found.append("a curly brace is left over: write fields as {items}, with both braces")
    return found


def render(template: str, values: Mapping[str, str]) -> str:
    """The template with each field replaced by its value (a field with no value is left empty)."""
    return _FIELD.sub(lambda m: values.get(m.group(1), ""), template)


# ---- strategic sourcing: fixed words (D20, D45); nothing here is written by a model -------------------------------


def period_sentence(start: date, end: date) -> str:
    """Opens the item block of a period RFQ: prices are for orders in the period, the quantities only an estimate."""
    return (
        f"Prices are asked for the period {start:%d %b %Y} to {end:%d %b %Y}, for orders placed during it. "
        "The quantities are our estimate for the period, not a commitment to buy."
    )


AWARD_LETTER = (
    "Dear {contact},\n\n"
    "{company} has selected {supplier} for {reference}:\n"
    "{lines}\n"
    "for the period {period_from} to {period_to}, delivered to {location}.\n\n"
    "This is a notice of selection, not an order and not a commitment to buy. "
    "The rate contract {contract} is attached for your review: accept or decline it here: "
)
AWARD_LETTER_END = (
    "\n\nOrders will follow during the period as we need the goods.\n\nRegards,\nProcurement, {company}\n"
)

# a regret note never says a price, a quantity, a value or who was selected (D45)
REGRET_NOTE = (
    "Dear {contact},\n\n"
    "thank you for your quotation {quote} for {reference}. After comparing the offers we have not selected yours this "
    "time. We value your interest and hope to invite you again.\n\n"
    "Regards,\nProcurement, {company}\n"
)
