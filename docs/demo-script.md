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

**What is in the company.** The V-belts (SP-01) are below their reorder point. Suppliers include Deccan Castings (rate contract for castings, impellers and shafts), Gujarat Bearings and Seals (micro enterprise in another state: IGST, 45 days by agreement), Shree Wires (issues e-invoices), Pune Copper and Western Industrial Supplies (the only spares supplier). Rules: up to Rs 10,000 a request approves itself, then the plant head, above Rs 1,00,000 the director. Orders up to Rs 1,00,000 need no approval, above that the purchase manager. Payment runs go to the director. The ERP's production orders are loaded too: MO-1 (50 pumps of 1 HP by 5 December) and MO-2 (100 pumps of 0.5 HP by 15 December), each with its material request waiting for the store check (see [Production orders](#production-orders-the-store-check) below; the journey does not need it).

## The journey

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | buyer@ | **Requests** (nothing yet, it says to click **Check stock levels**) → **Check stock levels** → read the preview → **Create and submit** | "1 item at or below their reorder point would become 1 request, about ₹2,310 in all." The V-belt request (11 belts) approves itself |
| 2 | production@ | **Requests** → **New request** → **Copper winding wire**, 120 → **Save as draft** → **Submit for approval**. The same for **Mechanical seal 18 mm**, 120, and **Impeller 1 HP**, 200 | The wire (₹1,10,400) waits for Meera Iyer (director); the seals (₹11,400) and impellers (₹44,000) wait for Vikram Joshi (plant head) |
| 3 | planthead@ | **Dashboard → My work** lists both. Open one: **Approve**, **Approve less…**, **Return for changes** and **Reject** are offered. Then **Requests** → **Needs my decision** → tick both → **Approve selected** | Both approved |
| 4 | director@ | Open the **link in the email** about the wire (Mailpit) → **Approve** | The page decides nothing until you click; the same link does not work a second time |
| 5 | buyer@ | **Requests** → open the seal request → **Find suppliers** | A quotation request. The impellers go straight to an order on the castings contract (the worker does it) |
| 6 | production@ | The bell shows the wire request approved. **Assistant** → "Where is my request?" | The answer names the request and its status |
| 7 | buyer@ | **Dashboard → My work → RFQs waiting to be sent** → **Admin → Approvals → Approve**, for each of the three | Emails to the suppliers in Mailpit (belts to Western Industrial; seals and wire to three suppliers each) |
| 8 | the supplier, then buyer@ | Western Industrial replies to the belt RFQ by email (205 a belt). **Quotations** → "Replies read from email" → **Confirm this quote**. One quote where two are wanted: write a reason → **Award the selected suppliers** | The award is refused without a reason |
| 9 | buyer@ | Seals: **Enter a quote** for Gujarat (94), Deccan (99), Shree (97); wire: Shree (905), Pune Copper (915), Deccan (930) → **Award the selected suppliers** | The cheapest is recommended: Gujarat for seals, Shree for wire |
| 10 | purchasemanager@ | **Dashboard → My work → Orders waiting for my approval** → open the wire order → **Approve** | Only the wire order (₹1,08,600) waits; IGST on Gujarat's order, CGST and SGST on the rest. buyer@ cannot approve |
| 11 | buyer@ | **My work → Approved orders to send** → **Send to supplier**, then **Record acknowledgement** | "Sent", then "acknowledged"; the PDF is in Mailpit |
| 12 | store@ | **Goods receipt** → **Receive goods**: impellers 120 of 200, later the other 80; the rest in full. The seals wait for inspection → **Record inspection** | The impeller order stays open after 120 and the buyer is told. Stock goes up only after inspection for the seals |
| 13 | ap@ | **Invoices** → **Upload an invoice** with Shree's PDF (e-invoice QR code) → **Check and enter**. **Enter an invoice** for Deccan (200 at 220) and Western Industrial (11 at 205) | The QR invoice is read by code alone. Each matches and is approved |
| 14 | ap@, then purchasemanager@ | **Enter an invoice** for Gujarat's seals 12% above the order. It is **Blocked** → **Explain why and what to do** → **Ask to pay it anyway** → reason → **Send**. purchasemanager@: **My work → Blocked invoices to release** → **Approve** | The clerk cannot release their own invoice; afterwards it is approved for payment |
| 15 | finance@ | **My work → Invoices due: plan a payment run** → **Payments** → **See what is due** → **Send the run for approval** | One payment per supplier, the micro supplier first. finance@ cannot approve its own run |
| 16 | director@, then finance@ | director@: **My work → Payment runs to approve** → **Approve**. finance@: **Make the bank file** → **Show the bank file**; then **Import the bank statement** | Invoices paid, orders closed, payment advice with the bank reference to each supplier |
| 17 | director@ | **Control tower**, **Dashboard → Where things stand** | Nothing left in any stage; the cycle KPIs count the four requests |
| 18 | admin@ | **Admin** → **Company setup** → **Policies** → reason, change **Days suppliers get to quote**, **Save**; then **Audit** and **History** | The change is in both, with its reason |

## Production orders: the store check

Steps 1 to 3 of the flow, on their own (after `demo reset apex`; they add two requests, so do them after the journey or on a fresh reset):

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | store@ | **Material requests** | MO-1 and MO-2, each with its materials and nothing set aside yet |
| 2 | store@ | **Run store check** | MO-1 (needed first) gets its stock first: all 50 impellers, shafts, terminal boxes and paint, 40 of 100 bearings, 50 of 90 kg copper wire, 20 of 50 cartons, 20 of 50 seals. One request for what is short goes to Vikram Joshi (plant head): castings 50 (contract ₹650), bearings 60, copper wire 40 kg, cartons 30, seals 30, capacitors 50, ₹80,030 in all. The plan's example has only the first four items (₹72,930); Apex's bill of materials also has seals and capacitors. MO-2 gets what is left; its request (₹2,11,500) goes to Meera Iyer (director) |
| 3 | store@ | **Inventory** | The impellers show 50 of 60 set aside for production; the free 10 are all that can be issued |
| 4 | store@ | **Material requests** → MO-1 → **Issue for production** | Everything set aside is issued; what is short stays on the request until it arrives (run the store check again, then issue) |

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
