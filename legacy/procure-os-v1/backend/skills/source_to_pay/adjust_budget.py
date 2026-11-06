SKILL_NAME = "adjust_budget"
SKILL_DESCRIPTION = (
    "Adjust a category's budgeted amount for a given month. Use when the user wants to "
    "raise or lower a budget, e.g. after a category has gone over budget and the user "
    "decides the original number was wrong or needs updating. Once the new amount "
    "covers actual spend, the budget-overrun notification for it resolves automatically "
    "on the next check, the same way it would if the overrun had never happened; no "
    "separate dismiss needed. This only changes the budget number itself — it does not "
    "move or reallocate actual spend between categories, since spend isn't tracked at a "
    "granularity that would make that meaningful."
)
SKILL_PARAMETERS = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "description": "The category name, e.g. Electronics"},
        "month": {"type": "string", "description": "Month, format YYYY-MM"},
        "new_budgeted_amount": {"type": "number"},
    },
    "required": ["category", "month", "new_budgeted_amount"],
}


def run(db, **kwargs):
    from models import Budget

    b = db.query(Budget).filter(
        Budget.category == kwargs["category"], Budget.month == kwargs["month"]
    ).first()
    if not b:
        return {"error": f"No budget found for {kwargs['category']} in {kwargs['month']}"}

    b.budgeted_amount = kwargs["new_budgeted_amount"]
    db.commit()
    return {"category": b.category, "month": b.month, "new_budgeted_amount": b.budgeted_amount}
