# Demo script: the small company

A one-plant company, eight people, twelve items, four suppliers, no history. This script is exactly what eval E34 does in a real browser (`python evals/run.py --only E34`), so every step below is known to work. By hand it takes about twenty minutes.

**Make it (any time, safe to repeat):** `python -m p2p.cli demo reset flow` from `backend/`. It only runs against a database on this machine. **All passwords: `flow123456`** (a throwaway development password for synthetic data; the platform needs 10 characters). The logins are also in `.dev-credentials` under `flow`. Start the app with `python scripts/dev.py`, open http://127.0.0.1:8000/ and use two browser windows (one normal, one private) so you can switch role quickly. Test emails land in Mailpit at http://127.0.0.1:8025.

| Log in as | Role in the app | Does |
|---|---|---|
| requester@flow.example | Requester (maintenance engineer) | Asks for things |
| manager@flow.example | Approver (Maintenance Manager) | Approves requests up to Rs 1,00,000 |
| approver@flow.example | Approver (Plant Head, the owner level) | Approves orders, price variances, payment runs and anything larger |
| buyer@flow.example | Buyer | Sources, quotes, sends orders |
| store@flow.example | Store (Storekeeper) | Receives goods |
| ap@flow.example | AP (Accounts Payable) | Enters invoices |
| finance@flow.example | Finance (Finance Manager) | Plans and pays |
| admin@flow.example | Admin | Setup only |

**What is in the company.** Four items are below their reorder point: ball bearing 6205-2RS, mechanical seal 25 mm, contactor 32 A, safety shoes. Four suppliers: Sai Bearings (micro enterprise, 60-day terms but 45 days by agreement), Western Electricals (has the rate contract for the contactor, MCB and cable; invoices carry an e-invoice number), Deccan Industrial Supplies, and Gujarat Safety and Tools (other state, so IGST). Rules: up to Rs 10,000 a request approves itself; up to Rs 1,00,000 the maintenance manager; above that the plant head. Orders, price variances and payment runs go to the plant head.

## The journey

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | buyer@ | **Requests** (nothing yet, and it says to click **Check stock levels**) → **Check stock levels** → read the preview → **Create and submit** | "4 items at or below their reorder point would become 3 requests, about ₹76,600 in all." Three requests: safety shoes Rs 9,200 (approves itself), bearing and seal Rs 55,000 and contactor Rs 12,400 (both wait for the maintenance manager) |
| 2 | requester@ | **Requests** → **New request** → item **MCB 32 A single pole C curve**, 40 → **Save as draft** → **Submit for approval** | A draft that opens with the progress strip, then "Waiting for Maintenance Manager" (Rs 12,800) |
| 3 | manager@ | **Dashboard → My work** lists the three requests. Open one: **Approve**, **Approve less…**, **Return for changes** and **Reject** are offered. Then **Requests** → **Needs my decision** → tick the bearing/seal and the contactor → **Approve selected** | Both become approved. The new MCB request is still waiting |
| 4 | manager@ | Open the **link in the email** about the MCB request (Mailpit) → **Approve** | The page shows the request and decides nothing until you click. After **Approve** the request is approved; the same link does not work a second time |
| 5 | buyer@ | **Requests** → open the approved bearing and seal request → **Find suppliers** | A quotation request is made. The contactor and the MCB go straight to orders on the contract price (the worker does it). Shoes also go to a quotation request |
| 6 | requester@ | The bell shows "Request … approved". **Assistant** → "Where is my request?" | The assistant answers from the data, with the request number and its status |
| 7 | buyer@ | **Dashboard → My work → RFQs waiting to be sent** → open → **Admin → Approvals → Approve**, for each quotation request | Emails to the suppliers in Mailpit: shoes to Deccan and Gujarat; bearing and seal to Sai, Deccan and Gujarat |
| 8 | the suppliers | Reply by email (shoes: Deccan 1,120 and Gujarat 1,090 a pair). The buyer opens the **Quotations** page of the shoes request: "Replies read from email" → **Confirm this quote** for each | Two quotes with the supplier's own prices; the cheaper (Gujarat) is recommended → **Award the selected suppliers** |
| 9 | buyer@ | The bearing and seal request: **Enter a quote** → **Save the quote** for Sai (305 / 6,450), Deccan (315 / 6,350) and Gujarat (320 / 6,400) → **Award the selected suppliers** | The comparison recommends the cheapest per line: Sai for the bearing, Deccan for the seal |
| 10 | approver@ | **Dashboard → My work → Orders waiting for my approval** → open each → **Approve** | CGST and SGST on the in-state orders, IGST on Gujarat's. A link to the order document (PDF). buyer@ cannot approve an order |
| 11 | buyer@ | **Dashboard → My work → Approved orders to send** → **Send to supplier**, then **Record acknowledgement** with a promised date | Status "sent", then "acknowledged"; the supplier's email with the PDF is in Mailpit |
| 12 | store@ | **Goods receipt** → **Receive goods** on the bearing order → **30** of 50 → **Post receipt**; later the remaining **20**. The other orders in full | After 30 the order stays open for the rest; the buyer is told "Goods arrived". After the last, every order shows "received" and stock has gone up. requester@ is told the goods for their request arrived. buyer@ and ap@ are refused |
| 13 | ap@ | **Dashboard → My work → Goods received, waiting for an invoice**. **Invoices** → **Upload an invoice** with the PDF from Western Electricals (it has the e-invoice QR code) → **Check and enter**. **Enter an invoice** for Deccan (6 at 6,350) and Sai (50 at 305) → **Enter and match** | The QR invoice is read by code alone, no model. Each invoice matches and is approved. Due dates: 30 days (Western, Deccan), and for Sai 45 days instead of its 60-day terms |
| 14 | ap@, then approver@ | **Enter an invoice** for Gujarat's shoes at a price 12% above the order → **Enter and match**. It is **Blocked**, with the reason and a "Next:" line. **Explain why and what to do** → **Ask to pay it anyway** → write the reason → **Send**. approver@: **Dashboard → My work → Blocked invoices to release** → **Admin → Approvals → Approve** | The clerk cannot release the block on an invoice they entered. After the plant head approves, the invoice is approved for payment |
| 15 | finance@ | **Dashboard → My work → Invoices due: plan a payment run**. **Payments** → **See what is due** → **Send the run for approval** | One payment per supplier, the small supplier first. finance@ cannot approve its own run |
| 16 | approver@, then finance@ | approver@: **Dashboard → My work → Payment runs to approve** → **Admin → Approvals → Approve**. finance@: **Make the bank file** → **Show the bank file**; then choose the bank's statement (CSV: date, bank reference, narration with the payment number, debit) → **Import the bank statement** (or **The bank has paid this run**) | The bank file is there only after approval. After the statement: invoices paid, orders closed, every supplier has a payment advice with the bank reference, buyer@ is told "paid and closed" |
| 17 | approver@ | **Control tower**, **Dashboard → Where things stand** | While the shoes invoice was blocked the tower showed one blocked invoice and four waiting for payment; at the end nothing is left in any stage. The cycle KPIs count the four requests |
| 18 | admin@ | **Admin** (in the menu) → **Company setup** → **Policies** → write a reason at the top, change **Days suppliers get to quote**, **Save**. Then **Audit** and **Company setup** → **History** | The change is in both, with its reason. Nobody else may change a policy |

## Things worth showing

- **Someone else's button is missing.** Open any case as a person who must not act on it (requester@ on its own request, buyer@ on an order, finance@ on its own payment run): the button is not offered, and the server refuses it even if you try.
- **Controls.** admin@: **Admin → Automation** (the "send quotation requests" decision is set so a buyer approves; paying, activating a supplier and releasing a block can never be automatic) and the kill switch.
- **The guides.** **Help** opens the guide of your role; the guides are in `docs/guides/` and E34 follows each one's first task.

## Check that it still works

`python evals/run.py --only E34` runs everything above in a real browser, then follows each role's guide, then opens every screen of every role at laptop and phone width and fails on any error, blank panel, raw code or sideways scroll (about three minutes). `python evals/run.py --only E33` does the money side through the API and compares every figure with the dataset's own files. `python scripts/check.py` includes `tests/test_flow_demo.py`, which checks the company's shape.
