# AP clerk guide

You enter supplier invoices and work the ones that do not match.

## What you see when you log in

**Dashboard** → **My work**: **Goods received, waiting for an invoice** and **Blocked invoices to work**.

## Enter an invoice

1. Open **Invoices**. Click **Enter an invoice**, or **Upload an invoice** for a PDF or a photograph. Invoices mailed to the company's invoice address appear as drafts: click **Check and enter**, or **Dismiss**.
2. Fill in or check the supplier's invoice number, date, the order and the lines.
3. Click **Enter and match**. The system checks duplicates and matches the invoice to the order and the receipt. A good one is approved for payment.

## When it is blocked

The **Blocked** list shows each blocked invoice with the reason and a line starting "Next:" that says what to do about it. Open the invoice. It says why: price over the order (within neither 2% nor the rupee limit), more quantity than received, GST that differs from the order by rate or by more than a rupee a head, a service the requester has not confirmed yet, tax or IRN problem, or a duplicate. Every reason on a line is shown, and the tax cell gives the order's rate, the invoice's rate, and the expected and charged CGST, SGST and IGST.

1. Click **Explain why and what to do** for a plain-words explanation and a draft query. Edit it and click **Send the query to the supplier**: it goes from the platform to the supplier's order contact, with replies to the invoice mailbox.
2. If goods or a service are being corrected, wait: a receipt, an inspection or a service confirmation matches the invoice again by itself. **Match again** is for a changed order.
3. A price variance is accepted only by the person the price rule names, under **My work** → **Price variances to accept**: click **Accept the price and pay**, write the reason and **Send**. Finance cannot accept a price; a tax or arithmetic block still goes to an approver as **Ask to pay it anyway**.
4. A quantity or service hold is never released: it waits for the goods, the confirmation or a corrected invoice.
5. If the invoice is wrong, click **Cancel**.

## Reconcile with a supplier and with the store

1. Open **Reconciliation**. **Received not invoiced** lists, per order line, goods accepted and services confirmed that have no invoice yet (and invoices for more than was received), with their age and totals by supplier. Pick a date and click **Show**; **Export** gives the list as a file.
2. Under **Supplier statements**, choose the supplier, the statement file (CSV or Excel in the template) and click **Import and match**. Each line comes back **Matched**, **Amount differs**, **Only on theirs** or **Only on ours**, with the reason. The same file twice is imported once.
3. **Order trace** shows the whole chain of any document: request, quotation, order, receipts, invoices and payment, with dates, amounts and who acted.

## What you cannot do

You cannot release a block on an invoice you entered, and you cannot approve payments.

## If you are stuck

If the goods were never received, the Store has to post the receipt first. Use the filters **Blocked**, **Approved for payment** and **Paid** to find an invoice.

## Exceptions

**Exceptions** lists everything stuck that needs a person, oldest first, with the reason, who must act, the next step and the age in working days; tick **Only mine** for what waits on you. Blocked invoices show there with what to do next. Each row is a case with an assignee, a due date and a level; **Check again** asks the engine whether it is still stuck (it closes only when it is gone), and **Reassign** gives it to someone who may act.
