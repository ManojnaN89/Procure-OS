# AP clerk guide

You enter supplier invoices and work the ones that do not match.

## What you see when you log in

**Dashboard** → **My work**: **Goods received, waiting for an invoice** and **Blocked invoices to work**.

## Enter an invoice

1. Open **Invoices**. Click **Enter an invoice**, or **Upload an invoice** for a PDF or a photograph. Invoices mailed to the company's invoice address appear as drafts: click **Check and enter**, or **Dismiss**.
2. Fill in or check the supplier's invoice number, date, the order and the lines.
3. Click **Enter and match**. The system checks duplicates and matches the invoice to the order and the receipt. A good one is approved for payment.

## When it is blocked

The **Blocked** list shows each blocked invoice with the reason and a line starting "Next:" that says what to do about it. Open the invoice. It says why: price over the order, more quantity than received, a service the requester has not confirmed yet (it is matched again by itself once they do), tax or IRN problem, or a duplicate.

1. Click **Explain why and what to do** for a plain-words explanation.
2. If goods or a price are being corrected, wait, then click **Match again**.
3. If the supplier is right and the order is wrong, click **Ask to pay it anyway**, write the reason and click **Send**. A different person decides, under **Admin** → **Approvals**.
4. If the invoice is wrong, click **Cancel**.

## Reconcile with a supplier and with the store

1. Open **Reconciliation**. **Received not invoiced** lists, per order line, goods accepted and services confirmed that have no invoice yet (and invoices for more than was received), with their age and totals by supplier. Pick a date and click **Show**; **Export** gives the list as a file.
2. Under **Supplier statements**, choose the supplier, the statement file (CSV or Excel in the template) and click **Import and match**. Each line comes back **Matched**, **Amount differs**, **Only on theirs** or **Only on ours**, with the reason. The same file twice is imported once.
3. **Order trace** shows the whole chain of any document: request, quotation, order, receipts, invoices and payment, with dates, amounts and who acted.

## What you cannot do

You cannot release a block on an invoice you entered, and you cannot approve payments.

## If you are stuck

If the goods were never received, the Store has to post the receipt first. Use the filters **Blocked**, **Approved for payment** and **Paid** to find an invoice.

## Exceptions

**Exceptions** lists everything stuck that needs a person, oldest first, with the reason, who must act, the next step and the age in working days; tick **Only mine** for what waits on you. Blocked invoices show there with what to do next.

## Bills without an order
On **Invoices**, answer **No** to **Is there a purchase order?** for a utility, a telephone bill or a professional fee. Pick the category (its limit is shown), the cost centre and GL, attach the bill copy and any supporting document the category names, and enter the lines. A bill that is not a duplicate goes to the cost centre's authoriser, then up the approval chain. **Without an order** lists them. A header or arithmetic fault can only be corrected and matched again: a bill is never released by proposal.
