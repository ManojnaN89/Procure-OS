# Finance guide

You plan the payment runs, keep the tax at source and record what the bank did. The tax owner confirms TDS here too.

## What you see when you log in

**Dashboard** → **My work**: **Invoices due: plan a payment run**, **Runs waiting for the owner's approval**, **Approved runs: make the bank file**, **My work**, **TDS to confirm**.

## Plan a run

1. Open **Payments** and click **See what is due**. It lists approved invoices whose pay-by day is in the window, small suppliers first, one payment per supplier, with TDS, notes, advances and any discount worked out. **Held back** says why an invoice is not there: TDS to confirm, bank account not verified, a bank change waiting, a block, an unsettled advance, or the run that already holds it.
2. Check it, then click **Send the run for approval**.

## The approver the rule names

The run goes to the person `PAY-01` names (the director at Apex, the CFO at Brightwave), never to you if you proposed it. They open the run, press **Email me a code**, and approve with the code. The run is refused for anyone else, even another approver. Nothing leaves the bank before that.

## Pay

1. Open the approved run and click **Make the bank file**. **Show the bank file** shows it again. The run must be filed on its run date; an unfiled run is cancelled that evening.
2. For each payment press **Record this payment** with the bank reference (letters and digits, 6 to 30), the value date and a proof (PDF or photo). A reference is never made up. With a UTR the supplier's advice goes now.
3. **Import the bank statement**: a CSV with a date, the bank reference, the narration and the debit. Each debit line settles its own payment; a line without a UTR leaves the UTR empty. A credit line that names one of our payments is listed as "Maybe a return" — a person then presses **Bank returned it**, never the import. A run is paid when none of its payments waits and at least one is settled.

## What a payment does

On settlement the invoices become **paid**, notes grow what they have used, the budget takes the actual (stock was already spent at receipt), a finished order closes, an advance is taken off the order's invoices, and the supplier gets an advice with the real reference. A returned payment or a reversal reopens the invoices and the order lines.

## Tax at source

**TDS to confirm** lists the rows the platform worked out: 194Q on the part of a supplier's year above 50 lakh, 194C and 194J on the whole base once their threshold is crossed, advance TDS when it is paid. Confirm one or several, or change the section with a reason; the amount is always worked out again, never typed. A zero row under the item category's own section is already confirmed. An invoice with a row waiting is not paid.

## Advances

A buyer asks for one on the order. The purchase manager approves it. It is paid first in the next run (with its TDS shown), and the order's invoice then takes it off. Cancelling an order with a paid advance is refused until a debit note recovers what was not used: **Advances** → **Recover**, then another finance person approves the note.

## Notes

A credit or debit note enters **received**; another person with `invoice.approve` approves it. Approved notes are netted in the supplier's next payment, linked notes first then oldest, never below zero; the rest waits.

## What you cannot do

You cannot approve a run you proposed, mark a run paid by hand, or invent a reference. You cannot settle a payment without the bank's line. The person who approved a run cannot record or confirm its payments. **Exceptions** lists everything stuck, oldest first, with who must act and the next step.

## Watch the budgets

**Control tower** shows each cost centre's budget, what requests and orders hold, what was spent and what is left. Lock a month once it has ended; from 1 April close the year; **Adjust a budget** needs another finance person. A small supplier's legal deadline is the MSMED one: at most 45 days from acceptance when agreed in writing, 15 days without.
