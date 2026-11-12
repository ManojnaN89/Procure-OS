# Demo script: Apex Pumps

Apex Pumps is the main demo company: one pump plant at Chakan, nine people, sixteen items, six suppliers, and twelve months of stock history (nothing open). This script is exactly what eval E34 does in a real browser (`python evals/run.py --only E34`), so every step below is known to work. By hand it takes about twenty-five minutes. The second demo company, Brightwave Technologies (`brightwave`, IT services), is made the same way.

**Make it (any time, safe to repeat):** `python -m p2p.cli demo reset apex` from `backend/`. It only runs against a database on this machine. **All passwords: `flow123456`** (a throwaway development password for synthetic data; the platform needs 10 characters). The logins are also in `.dev-credentials` under `apex`. Start the app with `python scripts/dev.py`, open http://127.0.0.1:8000/ and use two browser windows (one normal, one private) so you can switch role quickly. Test emails land in Mailpit at http://127.0.0.1:8025.

| Log in as (`@apexpumps.example`) | Role in the app | Does |
|---|---|---|
| production@ | Requester (Production Manager) | Asks for things |
| planthead@ | Approver (Plant Head) | Approves requests from Rs 10,000 to Rs 1,00,000 |
| director@ | Approver (Director) | Approves requests above Rs 1,00,000, payment runs |
| purchasemanager@ | Approver (Purchase Manager) | Approves orders above Rs 1,00,000, price variances |
| buyer@ | Buyer | Sources, quotes, sends orders |
| store@ | Store (Storekeeper) | Receives and inspects goods |
| ap@ | AP (AP Executive) | Enters invoices |
| finance@ | Finance (Finance Manager) | Plans and pays |
| admin@ | Admin | Setup only |

**What is in the company.** The V-belts (SP-01) are below their reorder point. Suppliers include Deccan Castings (rate contract for castings, impellers and shafts), Gujarat Bearings and Seals (micro enterprise in another state: IGST, 45 days by agreement), Shree Wires (issues e-invoices), Pune Copper and Western Industrial Supplies (the only spares supplier). Rules: up to Rs 10,000 a request approves itself, then the plant head (the head of the plant the goods go to), above Rs 1,00,000 the director; capital purchases above Rs 1,00,000 also go to the purchase manager. Orders up to Rs 1,00,000 need no approval, above that the purchase manager; a contract order within that goes out touchless (sent with no buyer). Payment runs go to the director. The ERP's production orders are loaded too: MO-1 (50 pumps of 1 HP by 5 December) and MO-2 (100 pumps of 0.5 HP by 15 December), each with its material request waiting for the store check (see [Production orders](#production-orders-the-store-check) below; the journey does not need it).

## The journey

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | buyer@ | **Requests** (nothing yet, it says to click **Check stock levels**) → **Check stock levels** → read the preview → **Create and submit** | "1 item at or below their reorder point would become 1 request, about ₹2,310 in all." The V-belt request (11 belts) approves itself |
| 2 | production@ | **Requests** → **New request** → **Copper winding wire**, 120 → **Save as draft** → **Submit for approval**. The same for **Mechanical seal 18 mm**, 120, and **Impeller 1 HP**, 200 | The wire (₹1,10,400) waits for Meera Iyer (director); the seals (₹11,400) and impellers (₹44,000) wait for Vikram Joshi (plant head) |
| 3 | planthead@ | **Dashboard → My work** lists both. Open one: **Approve**, **Approve less…**, **Return for changes** and **Reject** are offered. Then **Requests** → **Needs my decision** → tick both → **Approve selected** | Both approved |
| 4 | director@ | Open the email about the wire in Mailpit: it shows the line, the estimated total, the budget and why it came to the director, with **Approve**, **Reject** and **Send back for changes**. Click **Approve** → **Approve** | The page decides nothing until you click; the same link does not work a second time. Reject and send back ask for a reason; above Rs 5,00,000 approving also asks for a one-time code (**Email me a code**). Not deciding brings one reminder after a working day and escalation to the manager after two |
| 5 | buyer@ | **Requests** → open the seal request → **Find suppliers**. Then **Dashboard → My work → Sent orders waiting for the supplier's acknowledgement** → open the impeller order | A quotation request. The impellers go straight to an order on the castings contract (₹44,000, the worker does it), and it goes out **touchless**: approved by the rule and sent to Deccan Castings with no buyer. The order shows the **Touchless** badge, "Issued without a buyer" with the reason and the facts checked, and "Sent to"; there is no **Send to supplier** button. The order and its PDF are already in Mailpit |
| 6 | production@ | The bell shows the wire request approved. **Assistant** → "Where is my request?" | The answer names the request and its status |
| 7 | buyer@ | **Dashboard → My work → RFQs waiting to be sent** → **Admin → Approvals → Approve**, for each of the three | Emails to the suppliers in Mailpit (belts to Western Industrial; seals and wire to three suppliers each) |
| 8 | the supplier, then buyer@ | Western Industrial replies to the belt RFQ by email (205 a belt). **Quotations** → "Replies read from email" → **Confirm this quote**. One quote where two are wanted: write a reason → **Select these suppliers** | The selection is refused without a reason |
| 9 | buyer@ | Seals: **Enter a quote** for Gujarat (94), Deccan (99), Shree (97); wire: Shree (905), Pune Copper (915), Deccan (930) → **Select these suppliers** | The cheapest is recommended: Gujarat for seals, Shree for wire |
| 10 | purchasemanager@ | **Dashboard → My work → Orders waiting for my approval** → open the wire order → **Approve** | Only the wire order (₹1,08,600) waits; IGST on Gujarat's order, CGST and SGST on the rest. buyer@ cannot approve |
| 11 | buyer@, the suppliers | **My work → Approved orders to send** → **Send to supplier** for the belt, seal and wire orders (not touchless: they came from quotations, and the wire order is in the purchase manager's tier). Deccan Castings opens the link in its order email → **Accept the order**. Gujarat Bearings opens its link → **Ask for a change**: a date nine days on → **Send the change to the buyer**; buyer@ opens the seal order → **Supplier's answer** → **Accept**. **Record acknowledgement** on the belt and wire orders | "Sent", then "acknowledged"; the seal order becomes version 2 with the new date and the production manager is told; the PDFs are in Mailpit |
| 12 | store@ | **Goods receipt** → **Receive goods**: impellers 120 of 200, later the other 80; the rest in full. The seals wait for inspection → **Record inspection** | The impeller order stays open after 120 and the buyer is told. Stock goes up only after inspection for the seals |
| 13 | ap@ | **Invoices** → **Upload an invoice** with Shree's PDF (e-invoice QR code) → **Check and enter**. **Enter an invoice** for Deccan (200 at 220) and Western Industrial (11 at 205) | The QR invoice is read by code alone. Each matches and is approved |
| 14 | ap@, then purchasemanager@ | **Enter an invoice** for Gujarat's seals 12% above the order. It is **Blocked** → **Explain why and what to do** → **Ask to pay it anyway** → reason → **Send**. purchasemanager@: **My work → Blocked invoices to release** → **Approve** | The clerk cannot release their own invoice; afterwards it is approved for payment |
| 15 | finance@ | **My work → Invoices due: plan a payment run** → **Payments** → **See what is due** → **Send the run for approval** | One payment per supplier, the micro supplier first. finance@ cannot approve its own run |
| 16 | director@, then finance@ | director@: **My work → Payment runs to approve** → **Approve**. finance@: **Make the bank file** → **Show the bank file**; then **Import the bank statement** | Invoices paid, orders closed, payment advice with the bank reference to each supplier |
| 17 | director@ | **Control tower**, **Dashboard → Where things stand** | Nothing left in any stage; the cycle KPIs count the four requests |
| 18 | admin@ | **Admin** → **Company setup** → **Policies** → reason, change **Days suppliers get to quote**, **Save**; then **Audit** and **History** | The change is in both, with its reason |
| 19 | store@ | **Material requests** (MO-1 and MO-2) → **Load from the ERP** → kind **Production orders**, a CSV with MO-3 (20 pumps of 1 HP, 20 Jan) → **Load the file**; the same for **Production order lines** | "Loaded 1 row"; MO-3 gets its material request |
| 20 | store@ | **Run store check**, then **Issue for production** on MO-1 → **Issue what is set aside** | Stock is set aside earliest need-by first; what is short goes for approval, priced at the contract price, else the last price paid (the wire and seals the journey bought), else the standard price |
| 21 | planthead@, then buyer@ | Open MO-1's request (lines "Short for MO-1", the budget) and approve it by the emailed link; buyer@ opens it | Each line shows its buying route and why; the castings go straight to an order on the contract |

## Reconciliation (step 13)

After step 16, everything is paid. **Reconciliation** is in the menu of finance@ and ap@:

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | finance@ | Before the run in step 15: **Reconciliation** → **Small suppliers** | Gujarat's seal invoice, due by law 45 days after the seals were accepted (the days agreed in writing) |
| 2 | finance@ | **Order trace** → the impeller order's number → **Trace** | Every step of the case: the request, the order, both receipts (120, then 80), the invoice and the payment, with dates, amounts and who acted |
| 3 | ap@ | **Received not invoiced** → as of the payment day → **Show** | Nothing received and not invoiced; one line invoiced and not received: the seals, invoiced ₹1,353.60 above the order price (the block that was released) |
| 4 | finance@ | **Bank lines** (if the bank statement had a line no payment fits, such as the bank's charge) | The line, why it is open, and the payments of the same amount it could be |
| 5 | ap@ | **Supplier statements** → Deccan Castings → a statement (template: **Download the template**) with its invoice DC/2611/0207 at ₹1,180 more than ours and our payment → **Import and match** | The payment **Matched**; the invoice **Amount differs** by ₹1,180, with ours and theirs side by side |

Every step of the plan's 13 has a picture in `evals/results/journeys/steps/` (Apex and Brightwave), and `journey.md` says which step of the journey shows it; the reconciliation screens have one each too (`journey-13-1-…` onwards).

## The supplier's answer (step 8)

Instead of **Record acknowledgement** in step 11, let the suppliers answer. Each order email in Mailpit has a link (it works once):

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | the suppliers | Open the belt and wire order emails → the link → **Accept the order** | The orders are acknowledged; each supplier gets a second email with a link to record the dispatch |
| 2 | Gujarat Bearings and Seals | Open the seal order email → the link → **Ask for a change**: new date 6 Dec → **Send the change to the buyer** | The order is unchanged: version 1 |
| 3 | buyer@ | **Purchase orders** → the seal order → **Supplier's answer**: "delivery date ... to 06 Dec 2026" → **Accept** | Version 2 with the new date; the supplier gets the revised order. A price rise above 2% would go on to purchasemanager@ instead |
| 4 | Western Industrial | The dispatch link → part of the belts, a vehicle and LR number → **Record the dispatch** | store@ sees **On the way** on **Goods receipt**; the supplier gets a link for the rest |

Two working days before a promised date the supplier is reminded, and while nothing has arrived after it (the job worker sends these each morning).

## Production orders: the store check

Steps 1 to 3 of the flow, on their own (after `demo reset apex`; they add two requests, so do them after the journey or on a fresh reset):

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | store@ | **Material requests** | MO-1 and MO-2, each with its materials and nothing set aside yet |
| 2 | store@ | **Run store check** | MO-1 (needed first) gets its stock first: all 50 impellers, shafts, seals, capacitors, terminal boxes and paint, 40 of 100 bearings, 50 of 90 kg copper wire, 20 of 50 cartons. One request for what is short goes to Vikram Joshi (plant head): castings 50 (contract ₹650), bearings 60, copper wire 40 kg, cartons 30, ₹72,930 in all, as in the plan. MO-2 gets what is left; its request (₹2,09,700) goes to Meera Iyer (director) |
| 3 | store@ | **Inventory** | The impellers show 50 of 60 set aside for production; the free 10 are all that can be issued |
| 4 | store@ | **Material requests** → MO-1 → **Issue for production** | Everything set aside is issued; what is short stays on the request until it arrives, and is set aside for MO-1 by itself when it does (the store gets a notice), then issue it |

## Services and goods with no order (Brightwave)

On `python -m p2p.cli demo reset brightwave` (logins `@brightwave.example`, same password). Brightwave buys services (housekeeping, guards, AC maintenance on a service contract with Clean and Secure; software and cloud) and laptops, which are serialised. Eval E43 checks the same rules through the API on Brightwave's history; eval E46 walks the whole flow in a browser (`evals/results/journeys/brightwave.md`): employee@ asks for a laptop, five headsets and the housekeeping of the engineering floor; depthead@, ithead@ and cfo@ approve (the last step by the emailed link); the laptop and housekeeping go on their contracts, the headsets to two quotes; cfo@ approves the housekeeping order (above ₹1,00,000 with GST); buyer@ sends every order (Brightwave has no touchless orders); the laptop supplier accepts by its link; facilities@ receives the laptop with its serial; the housekeeping invoice waits until employee@ (who asked for it) confirms the service; finance@ plans the run, cfo@ approves it, the bank statement pays and closes everything.

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | facilities@ | **Requests** → **New request** → **Housekeeping**, 1 → submit; once it is approved the order goes to Clean and Secure on the contract | The order for the month, sent |
| 2 | ap@ | **Enter an invoice** for the housekeeping order before anyone confirms it | **Blocked**: "Service not confirmed" |
| 3 | facilities@ | **Confirm services** → the housekeeping line → percent 100 → **Confirm** (or the link in the end-of-period email: `python -m p2p.cli confirm-services brightwave`) | The confirmation is listed; the blocked invoice is matched again and approved. 100.01% is refused |
| 4 | facilities@ | **Receive without order** → two laptops (LP-01) that came against a phone call, with their two serial numbers → **Record and ask the purchase manager** | Waiting; stock unchanged |
| 5 | ithead@, then buyer@ | ithead@: **Receive without order** → **Approve**. buyer@: **Raise the order** at ₹62,000 a laptop, then send it | An order for ₹1,24,000 plus GST, linked to the receipt |
| 6 | facilities@ | **Post the receipt** | Two laptops in stock with their serials; the same serial again is refused |

## Start with cases in flight

`python -m p2p.cli demo populate apex --reset` (about 20 seconds, local database only) makes the company and then does what the people would do, through the real API, so every figure, email, notification and audit row is real:

| Where to look | What is there |
|---|---|
| buyer@ **Requests** → **Check stock levels** | Two spares used up with nothing on the way (lathe inserts, hydraulic oil) |
| buyer@ My work | The V-belt quotation request waiting to be sent; the capacitor quotation request with three quotes, not yet awarded |
| production@ **Requests** | A draft (bearings) to submit |
| planthead@ / director@ My work | Winding wire (₹18,400) for the plant head; motor bodies (₹1,30,000) for the director; the payment run for the director |
| purchasemanager@ My work | The copper wire order from an award; the blocked invoice to release |
| later stages | An order approved and waiting to be sent, one acknowledged, one part received (store@), one received and waiting for an invoice (ap@), an invoice not due yet, a run with its bank file made, and one case paid and closed |

It refuses a company that already has requests. To go back to the empty company (what E34 and the journey above expect), run `demo reset apex` again.

## Check that it still works

`python evals/run.py --only E34` runs the journey in a real browser, then follows each role's guide, then opens every screen of every role at laptop and phone width (about three minutes). `python evals/run.py --only E33` does the money side through the API against the dataset's own files. `tests/test_demo_company.py` and `tests/test_demo_populate.py` check the company and the cases in flight.
