# Admin guide: rules and automation controls

The Admin sets the company up. An Admin cannot take part in purchases: no approving, entering invoices or paying.

## Where

**Admin** in the menu opens the admin area. Every change asks for a reason (the box at the top) and is recorded: **Audit**, or **Company setup** → **History**.

## Company setup

- **People**: **Add person**, **Give role**, **Set limit** (the most a person may approve per document type).
- **Roles**: tick what each role may do, then press the Save button under that role.
- **Approvers**: who stands behind each approval role (**Save role** adds one). **Set head** sets a department head.
- **Approval chains**: which approvers a request or order needs, by amount and category. A change makes a new version. Old decisions stay tied to the version they used. **Save new version**.
- **Policies**: budget behaviour, quotation days, reminders, payment window, tolerances for price and quantity by item category (**Save tolerance**), and the mailboxes. **Requests mailbox** and **Invoices mailbox** are the addresses the company reads mail from; leave one empty to switch it off. Also here: the wording of the quotation request email (fields such as {items} and {reference} are filled in for each supplier), how quotes are compared (price, or weighted by delivery and quality record) and how far below the others a price must be to need a question.

## Automation controls

**Automation** shows each decision the platform can make, and how much it may do alone: off, recommend (a person approves each), or act within a value cap and a confidence limit.

- Paying, activating a supplier and releasing a block show **person only**. They cannot be automatic.
- **Stop all automation** is the kill switch. It stops every automatic action at once. People can still work by hand. **Switch off** puts it back.
- Start at recommend. Raise a decision to automatic only after its record shows no wrong results.

## Look at what happened

**Audit** (who did what), **Agent runs**, **Rule decisions** and **Model calls** show what the programs did and which rule version they used.

## If you are stuck

A refused change says why. A change that needs a reason does nothing until the reason box has text.
