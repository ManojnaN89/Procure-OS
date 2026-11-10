# Demo script: the small company

A one-plant company, eight people, twelve items, four suppliers, no history. Everything below runs in about ten minutes.

**Make it (any time, safe to repeat):** `python -m p2p.cli demo reset flow` from `backend/`. It only runs against a database on this machine. **All passwords: `flow123456`** (a throwaway development password for synthetic data; the platform needs 10 characters). The logins are also in `.dev-credentials` under `flow`. Open http://127.0.0.1:8000/ and use two browser windows (one normal, one private) so you can switch role quickly. Test emails land in Mailpit at http://127.0.0.1:8025.

| Log in as | Role in the app | Does |
|---|---|---|
| requester@flow.example | Requester (maintenance engineer) | Asks for things |
| manager@flow.example | Approver (Maintenance Manager, head of Maintenance) | Approves requests up to Rs 1,00,000 |
| approver@flow.example | Approver (Plant Head, the owner level) | Approves orders, price variances, payment runs and anything larger |
| buyer@flow.example | Buyer | Sources, quotes, sends orders |
| store@flow.example | Store (Storekeeper) | Receives goods |
| ap@flow.example | AP (Accounts Payable) | Enters invoices |
| finance@flow.example | Finance (Finance Manager) | Plans and pays |
| admin@flow.example | Admin | Setup only |

**What is in the company.** Four items are below their reorder point: ball bearing 6205-2RS, mechanical seal 25 mm, contactor 32 A, safety shoes. Four suppliers: Sai Bearings (micro enterprise, 60-day terms but 45 days by agreement), Western Electricals (has the rate contract for the contactor, MCB and cable; invoices carry an e-invoice number), Deccan Industrial Supplies, and Gujarat Safety and Tools (other state, so IGST). Rules: up to Rs 10,000 a request approves itself; up to Rs 1,00,000 the maintenance manager; above that the plant head. Orders and payment runs go to the plant head.

## The nine steps

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | buyer@ | **Requests** → **Check stock levels** → read the preview → **Create and submit** | Three requests: safety shoes Rs 9,200 (approves itself), bearing and seal Rs 55,000 and contactor Rs 12,400 (both wait for manager@) |
| 2 | manager@ | **Dashboard → My work** → open each request → **Approve** | Both requests become approved. Only the manager's step is open: the server refuses requester@, buyer@, admin@ and even approver@ |
| 3 | buyer@ | **Requests** → open the approved bearing and seal request → **Find suppliers** (it may already have run) | The contactor goes straight to an order on the contract price (Rs 2,950 each). Shoes and the bearing and seal go to quotation requests, waiting for buyer@ to approve sending |
| 4 | buyer@ | **Admin → Approvals → Approve** on each "Send RFQ" line. Then **Quotations** → open one → **Enter a quote** → **Save the quote** for each supplier; then **Award the selected suppliers** | Emails to the suppliers in Mailpit. Suggested prices: shoes 1,120 (Deccan) and 1,090 (Gujarat); bearing 305 / 315 / 320 and seal 6,450 / 6,350 / 6,400 (Sai / Deccan / Gujarat). The comparison recommends the cheapest per line: four orders in all |
| 5 | approver@ | **Purchase orders** → open each → **Approve** | CGST and SGST on the three in-state orders, IGST on Gujarat's. buyer@ cannot approve an order |
| 6 | buyer@ | **Purchase orders** → open each → **Send to supplier**, then **Record acknowledgement** with a promised date | Status "sent", then "acknowledged"; the supplier's email is in Mailpit |
| 7 | store@ | **Goods receipt** → **Receive goods** on each order → enter the full quantities → **Post receipt** | Stock goes up; orders show "received". Only store@ may receive: buyer@ and ap@ are refused |
| 8 | ap@ | **Invoices** → **Enter an invoice**, one per order: supplier's invoice number, date, the order line, quantity and price (for Western Electricals also an e-invoice number) → **Enter and match** | Each invoice is matched and approved. Due dates: 30 days (Western, Deccan), 45 days (Gujarat), and 45 days after acceptance for Sai instead of its 60-day terms |
| 9 | finance@, then approver@, then finance@ | **Payments** → **See what is due** → **Send the run for approval**. approver@: **Admin → Approvals → Approve**. finance@: **Make the bank file**, then **The bank has paid this run** | One payment per supplier, the small supplier first. finance@ cannot approve its own run. After "paid": invoices paid, orders closed, supplier payment advice in Mailpit |

## Things worth showing

- **Someone else's button is missing.** Open any case as a person who must not act on it (requester@ on its own request, buyer@ on an order, finance@ on its own payment run): the button is not offered, and the server refuses it even if you try.
- **A blocked invoice.** In step 8 enter the shoes invoice at a price 12% above the order: it is blocked. ap@ can open **Explain why and what to do**; releasing the block needs a different person.
- **Controls.** admin@: **Admin → Company setup** (people, approval chains, policies) and **Automation** (the "send quotation requests" decision is set so a buyer approves; payment runs can never be automatic). Every change is in **Audit**.

## Check that it still works

`python evals/run.py --only E33` runs these nine steps with the eight logins and compares every figure with the dataset's own files. `python scripts/check.py` includes `tests/test_flow_demo.py`, which checks the company's shape.
