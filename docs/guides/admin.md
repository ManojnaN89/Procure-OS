# Admin guide: rules and automation controls

The Admin sets the company up. An Admin cannot take part in purchases: no approving, entering invoices or paying.

## Where

**Admin** in the menu opens the admin area. Every change asks for a reason (the box at the top) and is recorded: **Audit**, or **Company setup** → **History**.

## Two administrators

A change to roles, limits, approvers, approval chains, the buying rules, policies, tolerances or automation, deactivating or reactivating a person, and changing a person's manager wait for a second administrator. It is listed under **Changes waiting for a second administrator** on Company setup and Automation: the value now, the new value and the reason. Another administrator presses **Approve**, or writes a reason and presses **Reject**. Whoever proposed a change cannot approve it, and nobody approves a change to their own access, roles, limits or manager. A change not approved within 7 days expires and nothing is applied; propose it again if it is still needed. While one change to a setting waits, a second change to the same setting is refused. The tolerances count as one setting: while a tolerance change waits, no other tolerance can be changed, and one change may carry several tolerance values that apply together. **History** shows who made each change and who approved it. With one administrator a change only waits. A new person is never made an Admin in one step: add them, then give the Admin role.

## Company setup

- **People**: **Add person**, **Give role**, **Set limit** (the most a person may approve per document type).
- **Roles**: tick what each role may do, then press the Save button under that role.
- **Approvers**: who stands behind each approval role (**Save role** adds one). **Set head** sets a department head.
- **Approval chains**: which approvers a request or order needs, by amount and category. A change makes a new version. Old decisions stay tied to the version they used. **Save new version**.
- **Buying route**, **Supplier selection** and **Touchless orders**: the rules for how a line is bought (how many quotes by value, when a safe reorder skips quotation), how quotes are compared (total cost or weighted score; the weights add up to 100); a buying route version can be for the whole company or for one category, which can only make it stricter and how big an order may go out with no buyer. Each tab shows the version in force in words, every earlier version with who made it and why, and a form for the next version from a date. An old version is never changed, so a decision keeps the version it used.
- **Single-source items**: items bought from one supplier only. **Mark single source** adds one; remove takes it off.
- **Policies**: budget behaviour, quotation days, reminders, payment window, tolerances for price and quantity by item category (**Save tolerance**), and the mailboxes. **Requests mailbox** and **Invoices mailbox** are the addresses the company reads mail from; leave one empty to switch it off. Also here: the wording of the quotation request email (fields such as {items} and {reference} are filled in for each supplier), the terms printed on the purchase order (fields {{ po_no }}, {{ payment_terms }}, {{ delivery_terms }}, {{ ack_days }}; an order already sent keeps its terms) and how far below the others a price must be to need a question.

## Loading master data again

The first import of a company's master files (no import before, no supplier or item yet) loads everything as given. Any later import matches each row by its key: new rows are added, changed rows are updated where that is allowed, and nothing is ever deleted (a row the files leave out stays). It never changes a supplier's status or bank accounts, policies, approval rules, tolerances, number series, roles, deputies, approvers, people's managers and limits, opening stock or a price or contract version: those change on the screens, with a second person. New suppliers arrive as drafts, their bank accounts and documents unverified, new contracts and contract prices as drafts. Each of these is a warning in the import's report; run it with `--dry-run` first to see them.

## Go-live

A company starts in **setup**: everything works, and what is done is test data (a banner says so). **Company setup** → **Go-live** shows the company's state with its history, and the readiness checks: master data, two administrators, approval bands with no gap, every approval role naming someone who may approve, a second person for every two-person control (separation), five different people for requester, approver, receiver, invoice approver and payer, no change waiting, a company bank account. A failed blocker must be cleared; a warning (a requester with nobody else to approve their request, bank accounts verified by a file) is shown and may be accepted. **Check again** reads them anew.

When setup is ready, the platform team moves the company to **cutover**: the setup database is kept as an archive and a clean one is made with the company's configuration (people, roles, rules, settings, master data, the loaded stock history); requests, orders, invoices and their numbers start again. In cutover only the administrators and Finance may sign in, and nothing runs by itself. On the Go-live tab Finance loads the documents still open in the old system (orders, receipts, service confirmations, unpaid invoices, advances, what was bought from each supplier this year, and the old system's control totals) from the templates: **Check** first, then untick **Dry run** and **Load**. Every row left out comes back with its file, line and reason; correct it and load the files again (what loaded before is not loaded twice).

When readiness has no blocker, write the reason at the top and press **Propose going live**; another administrator approves it under **Changes waiting for a second administrator**, and readiness is checked again then. A live company never goes back. After go-live, imports of master data never add or change opening stock or bank accounts (post a stock count, or a bank change a second person verifies), and the stock history is no longer imported.

## Tolerances and price thresholds

**Policies** → **Tolerances** shows the version in force and every earlier one. A change is a new version from a date (today or later): invoices already matched keep the version they were judged under, and an invoice is judged by the version in force on the day it was received. **Price thresholds** sets when a contract above the market opens a review (gap %, rupees per unit, saving, evidence age, sources, days before expiry). The values set at the start are a recommended default that needs validating with the company. Both need a second administrator.

## Automation controls

**Automation** shows each decision the platform can make, and how much it may do alone: off, recommend (a person approves each), or act within a value cap and a confidence limit.

- Paying, activating a supplier and releasing a block show **person only**. They cannot be automatic.
- **Stop all automation** is the kill switch. It stops every automatic action at once. People can still work by hand. **Switch off** puts it back once a second administrator approves.
- Start at recommend. Raise a decision to automatic only after its record shows no wrong results.

## What happened, and if you are stuck

**Audit** (who did what), **Agent runs**, **Rule decisions** and **Model calls** show what the programs did and which rule version they used. A refused change says why. A change that needs a reason does nothing until the reason box has text.
