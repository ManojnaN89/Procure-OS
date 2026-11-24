# Demo script: Apex Pumps

Apex Pumps is the main demo company: one pump plant at Chakan, nine people, sixteen items, six suppliers, and twelve months of stock history (nothing open). This script is exactly what eval E34 does in a real browser (`python evals/run.py --only E34`), so every step below is known to work. By hand it takes about twenty-five minutes. The second demo company, Brightwave Technologies (`brightwave`, IT services), is made the same way.

**Make it (any time, safe to repeat):** `python -m p2p.cli demo reset apex` from `backend/`. It only runs against a database on this machine. **All passwords: `flow123456`** (a throwaway development password for synthetic data; the platform needs 10 characters). The logins are also in `.dev-credentials` under `apex`. Start the app with `python scripts/dev.py`, open http://127.0.0.1:8000/ and use two browser windows (one normal, one private) so you can switch role quickly. Test emails land in Mailpit at http://127.0.0.1:8025.

| Log in as (`@apexpumps.example`) | Role in the app | Does |
|---|---|---|
| production@ | Requester (Production Manager) | Asks for things |
| planthead@ | Approver (Plant Head) | Approves requests from Rs 10,000 to Rs 1,00,000 |
| director@ | Approver (Director) | Approves requests above Rs 1,00,000, payment runs |
| purchasemanager@ | Approver (Purchase Manager) | Approves orders up to Rs 5,00,000 that are not touchless, price variances |
| buyer@ | Buyer | Sources, quotes, sends orders |
| store@ | Store (Storekeeper) | Receives and inspects goods |
| ap@ | AP (AP Executive) | Enters invoices |
| finance@ | Finance (Finance Manager) | Plans and pays |
| admin@ | Admin | Setup only |

**What is in the company.** The V-belts (SP-01) are below their reorder point. Suppliers include Deccan Castings (rate contract for castings, impellers and shafts), Gujarat Bearings and Seals (micro enterprise in another state: IGST, 45 days by agreement), Shree Wires (issues e-invoices), Pune Copper and Western Industrial Supplies (the only spares supplier). Rules: up to Rs 10,000 a request approves itself, then the plant head (the head of the plant the goods go to), above Rs 1,00,000 the director; capital purchases above Rs 1,00,000 also go to the purchase manager. Orders up to Rs 1,00,000 go out touchless when they qualify (a contract order, sent with no buyer); every other order goes to the purchase manager, never the buyer. Payment runs go to the director. The ERP's production orders are loaded too: MO-1 (50 pumps of 1 HP by 5 December) and MO-2 (100 pumps of 0.5 HP by 15 December), each with its material request waiting for the store check, and the ERP's MRP results for January (MRP-2027-01, 120 pumps of 1 HP at Chakan by 10 January) with a third material request, "MRP plan" (see [Production orders](#production-orders-the-store-check) below; the journey does not need it).

## The journey

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | buyer@ | **Requests** (nothing yet, it says to click **Check stock levels**) → **Check stock levels** → read the preview → **Create and submit** | "1 item at or below their reorder point would become 1 request, about ₹2,310 in all." The V-belt request (11 belts) approves itself |
| 2 | production@ | **Requests** → **New request** → **Copper winding wire**, 120 → **Save as draft** → **Submit for approval**. The same for **Mechanical seal 18 mm**, 120, and **Impeller 1 HP**, 200 | The wire (₹1,10,400) waits for Meera Iyer (director); the seals (₹11,400) and impellers (₹44,000) wait for Vikram Joshi (plant head) |
| 3 | planthead@ | **Dashboard → My work** lists both. Open one: **Approve**, **Approve less…**, **Return for changes** and **Reject** are offered. Then **Requests** → **Needs my decision** → tick both → **Approve selected** | Both approved |
| 4 | director@ | Open the email about the wire in Mailpit: it shows the line, the estimated total, the budget and why it came to the director, with **Approve**, **Reject** and **Send back for changes**. Click **Approve** → **Email me a code** → type the code from the new mail in Mailpit → **Approve** | The page decides nothing until you click; the same link does not work a second time. Every decision from an email link needs the one-time code, emailed only to the person the link names, so a forwarded link decides nothing (D-07); or **Open the request in the app** and sign in. Reject and send back also ask for a reason. Not deciding brings one reminder after a working day and escalation to the manager after two |
| 5 | buyer@ | **Requests** → open the seal request → **Find suppliers**. Then **Dashboard → My work → Sent orders waiting for the supplier's acknowledgement** → open the impeller order | A quotation request. The impellers go straight to an order on the castings contract (₹44,000, the worker does it), and it goes out **touchless**: approved by the rule and sent to Deccan Castings with no buyer. The order shows the **Touchless** badge, "Issued without a buyer" with the reason and the facts checked, and "Sent to"; there is no **Send to supplier** button. The order and its PDF are already in Mailpit |
| 6 | production@ | The bell shows the wire request approved. **Assistant** → "Where is my request?" | The answer names the request and its status |
| 7 | buyer@ | **Dashboard → My work → RFQs waiting to be sent**: open the belts RFQ → **Confirm and send**; then on **Quotations** tick the other two → **Confirm and send selected** (D42) | Emails to the suppliers in Mailpit (belts to Western Industrial; seals and wire to three suppliers each) |
| 8 | the supplier, then buyer@ | Western Industrial replies to the belt RFQ by email (205 a belt). **Quotations** → "Replies read from email" → **Confirm this quote**. One quote where two are wanted: write a reason → **Select these suppliers** | The selection is refused without a reason |
| 9 | buyer@ | Seals: **Enter a quote** for Gujarat (94), Deccan (99), Shree (97); wire: Shree (905), Pune Copper (915), Deccan (930) → **Select these suppliers** | The cheapest is recommended: Gujarat for seals, Shree for wire |
| 10 | purchasemanager@ | **Dashboard → My work → Orders waiting for my approval** → open each of the belt, seal and wire orders → **Approve** | All three wait: they came from quotations, so they are not touchless, and the buyer never approves an order they made, even below Rs 1,00,000. IGST on Gujarat's order, CGST and SGST on the rest. buyer@ cannot approve. The panel says which version is being approved; if the buyer changed the order since the page was opened, Approve is refused ("open it again"). An order above Rs 5,00,000 (director@) needs a one-time code: "Email me a code", type it, Approve |
| 11 | buyer@, the suppliers | **My work → Approved orders to send** → **Send to supplier** for the belt, seal and wire orders, now approved. Deccan Castings opens the link in its order email → **Email me a code**, types the code from its Mailpit mail → **Accept the order** (a date per line, as ordered). Gujarat Bearings opens its link → **Email me a code** → **Ask for a change**: a date nine days on, with the code → **Send the change to the buyer**; buyer@ opens the seal order → **Supplier's answer** → **Accept**. **Record acknowledgement** on the belt and wire orders: the version, each line's date and a note of how the supplier confirmed | "Sent", then "acknowledged"; the seal order becomes version 2 with the new date and the production manager is told; the PDFs are in Mailpit |
| 12 | store@, then quality@ | **Goods receipt** → **Receive goods**: impellers 120 of 200, later the other 80; the rest in full. The seals wait for inspection; quality@ opens the receipt → **Record inspection** (the store person who received them cannot) | The impeller order stays open after 120 and the buyer is told. Stock goes up only after inspection for the seals. Above the order, the receipt says "held in quarantine; the buyer decides" |
| 13 | ap@ | **Invoices** → **Upload an invoice** with Shree's PDF (e-invoice QR code) → **Check and enter**. **Enter an invoice** for Deccan (200 at 220) and Western Industrial (11 at 205) | The QR invoice is read by code alone. Each matches and is approved |
| 14 | ap@, then purchasemanager@ | **Enter an invoice** for Gujarat's seals 12% above the order. It is **Blocked** → **Explain why and what to do** → **Ask to pay it anyway** → reason → **Send**. purchasemanager@: **My work → Blocked invoices to release** → **Approve** | The clerk cannot release their own invoice; afterwards it is approved for payment |
| 15 | finance@ | **My work → Invoices due: plan a payment run** → **Payments** → **See what is due** → **Send the run for approval** | One payment per supplier, the micro supplier first. finance@ cannot approve its own run |
| 16 | director@, then finance@ | director@: **My work → Payment runs to approve** → **Approve**. finance@: **Make the bank file** → **Show the bank file**; then **Import the bank statement** | Invoices paid, orders closed, payment advice with the bank reference to each supplier |
| 17 | director@ | **Control tower**, **Dashboard → Where things stand** | Nothing left in any stage; the cycle KPIs count the four requests |
| 18 | admin@ | **Admin** → **Company setup** → **Policies** → reason, change **Days suppliers get to quote**, **Save**; a second administrator presses **Approve** under **Changes waiting for a second administrator**; then **Audit** and **History** | The change waits until another administrator approves it (D-22), then it is in both, with its reason. The demo companies have one administrator, so a second Admin login has to be set up outside the screens first (open decision) |
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

Instead of **Record acknowledgement** in step 11, let the suppliers answer. Each order email in Mailpit has a link (it works once). Every answer needs the one-time code: on the page press **Email me a code**, open the code mail in Mailpit (it goes only to the address the order went to, s****@... on the page) and type it in; a wrong code gives "wrong code; 4 tries left":

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | the suppliers | Open the belt and wire order emails → the link → **Email me a code** → the code from Mailpit; each line shows its delivery date as ordered (change one by a day or two) → **Accept the order** | The orders are acknowledged (**Versions**: "by link with a code" and the line dates); a date more than 3 days later, a Sunday or 24 Nov is refused with the reason; each supplier gets a second email with a link to record the dispatch |
| 2 | Gujarat Bearings and Seals | Open the seal order email → the link → **Email me a code** → **Ask for a change**: new date 6 Dec, with the code → **Send the change to the buyer** | The order is unchanged: version 1 |
| 3 | buyer@ | **Purchase orders** → the seal order → **Supplier's answer**: "delivery date ... to 06 Dec 2026" → **Accept** | Version 2 with the new date; the supplier gets the revised order. A date moved more than 3 days from the approved one, a higher quantity or price, or a higher total goes for approval again; anything else carries the approval. A price rise above 2% of the first approved price (rises add up) goes to purchasemanager@ instead |
| 4 | Western Industrial | The dispatch link → part of the belts, a vehicle and LR number → **Record the dispatch** | store@ sees **On the way** on **Goods receipt**; the supplier gets a link for the rest |

Two working days before a line's date (promised, else needed) the supplier is reminded, and while nothing has arrived after it, on the plant's working days (Monday to Saturday at Chakan; the job worker sends these each morning). An order not acknowledged two working days after its version was sent is chased with a fresh link every three working days, three times at most; then buyer@ gets a task. An old version's link says which version is current and offers **Email me the current version**.

## Production orders: the store check

Steps 1 to 3 of the flow, on their own (after `demo reset apex`; they add three requests, so do them after the journey or on a fresh reset):

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | store@ | **Material requests** | MO-1, MO-2 and MRP-2027-01/PLT-CKN · MRP plan, each with its materials and nothing set aside yet |
| 2 | store@ | **Run store check** | MO-1 (needed first) gets its stock first: all 50 impellers, shafts, seals, capacitors, terminal boxes and paint, 40 of 100 bearings, 50 of 90 kg copper wire, 20 of 50 cartons. One request for what is short goes to Vikram Joshi (plant head): castings 50 (contract ₹650), bearings 60, copper wire 40 kg, cartons 30, ₹72,930 in all, as in the plan. MO-2 gets what is left; its request (₹2,09,700) goes to Meera Iyer (director); the MRP plan (needed 10 January) gets the rest (10 impellers, 50 seals, 5 L paint) and its request (₹3,69,490) goes to the director too |
| 3 | store@ | **Inventory** | The impellers show 50 of 60 set aside for production; the free 10 are all that can be issued |
| 4 | store@ | **Material requests** → MO-1 → **Issue for production** | Everything set aside is issued; what is short stays on the request until it arrives, and is set aside for MO-1 by itself when it does (the store gets a notice), then issue it |

## Returns, goods above an order and a reversal (block 9)

1. store@: **Goods receipt** → **Receive goods** on an order whose item allows a little over: enter two more than ordered. The receipt says "2 above the order held in quarantine; the buyer decides"; **Quality hold and returns** shows the excess hold.
2. buyer@: **Dashboard → My work** has "2 NOS of ... arrived above PO-... on GRN-..." with **Accept by revision** and **Return**. Accept it with a reason: the proposal waits for the purchase manager (never the buyer), and once approved the order shows version 2 at the higher quantity, the goods are usable, and the supplier has the new version by mail. Or return it: the store dispatches the draft and the supplier is mailed the return challan.
3. quality@: reject a few units in an inspection, or store@: **Hold** some usable stock as blocked. store@: **Quality hold and returns** → tick the hold → **Make the draft return** → challan and vehicle → **Dispatch**. The stock leaves, the hold closes, and the buyer decides **Replace** or **No replacement** in **My work**. No replacement with an invoice already booked raises a **recovery claim**, listed under **Invoices** for AP.
4. store@: open a receipt posted in the last three working days → **Reverse** with a reason: the whole receipt goes back, the line and budget follow, and the order returns to sent or acknowledged. After the window the button asks the purchase manager, who approves it on Approvals. A receipt already invoiced or whose goods were issued is refused.
5. store@: **Receive without order** → reject a receipt → the return form (challan and vehicle) → **Mark returned**: one return document, one mail to the supplier. **Inventory** → a blocked hold → **Scrap** with a reason for what cannot go back.

## Held stock, safety stock and late supply (block 2)
After `demo reset apex` and **Run store check**. The scenarios and their expected figures are in `data/datasets/apex-v1/truth/stock_scenarios.json`.

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | store@ | **Inventory**, row Mechanical seal | "20 in quarantine" (the Gujarat lot waiting for the seal face check); bearings "8 blocked" (rust). Neither is ever set aside |
| 2 | store@ | Seals → **Holds** → **Release** 14 to usable, 6 to blocked, "Seal face check passed; six with a cut lip" | 74 usable, 6 blocked. MO-2 gets the 14 at once: its request is cut 90 -> 76 seals (₹2,08,370) |
| 3 | store@ | V-belt → **Issue** 3 ("Lathe 2 main drive") | "Only 2 is free; 2 is safety stock". **Ask to use safety stock** → "Sent to Vikram Joshi"; planthead@ approves on My work and the 3 are issued (1 left, below safety) |
| 4 | planthead@, buyer@ | On a fresh reset and store check: approve MO-1's request, `python -m p2p.cli source apex`, then the castings order → **Record acknowledgement**, promised 8 Dec | MO-1's castings: "at risk 50", "50 on PO-..., promised 8 Dec (late)"; nothing bought again. buyer@ has "Supply promised too late" on **Exceptions**, due 26 Nov (24 Nov is a holiday), escalating to Sanjay Rao |
| 5 | store@ | **Count** the castings: 20 | The 20 go to MO-1 (stock beats a late order): at risk 30, and the case says 30 |

## Demand changes from the ERP

On a fresh reset, after the store check above. The files are in `data/datasets/apex-v1/changes/` (load them with **Material requests → Load from the ERP**, one kind at a time, headers first, or `python -m p2p.cli import demand apex data/datasets/apex-v1/changes/A1-cut`).

| # | Who | Where and what | You should see |
|---|---|---|---|
| 1 | store@ | A1: **Production orders** `A1-cut/production_order.csv`, then **Production order components** `A1-cut/production_order_line.csv` (Sahyadri moved 40 of the 0.5 HP pumps to February: MO-2 to 60) | "Changed: 10" with each line: MO-2's request (with the director, not yet with the buyer) is cut first (bearings 200 -> 120 and so on, ₹1,24,380 left); the 40 seals and 6 L of paint MO-2 no longer needs go at once to the MRP plan, whose request is cut by as much (₹3,64,990). MO-2's card says "Changed on ...: 100 -> 60". No case |
| 2 | planthead@, then buyer@ | On another fresh reset: approve MO-1's request (₹72,930), then `python -m p2p.cli source apex` | MO-1's castings go on the contract order, the rest to quotation |
| 3 | store@ | A2: **Production orders** `A2-cancel/production_order.csv` (SO-1001 cancelled: MO-1 `cancelled`) | MO-1's material request is cancelled; its stock goes to MO-2 and the MRP plan (their requests cut). The buyer has a case per order or RFQ holding the castings 50, bearings 60, wire 40 kg and cartons 30 |
| 4 | buyer@ | **Exceptions** → kind **Demand changes to act on** | One row per document, owner "Buyer: Neha Kulkarni", due in two working days, with the amount; **Close** → "Order reduced" closes it. ap@ cannot close it |

Brightwave (`demo reset brightwave` now loads the joiner plan: two material requests, ADM and ENG, on 1 December) has B1-B4 in `brightwave-v1/changes/`: one admin offer withdrawn, the engineering intake on hold (after approval and sourcing: cases for Arjun Reddy), a fifth developer, admin joiners moved to 30 November.

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

## Two plants, prospects, market prices and tolerance versions (block 0b)

The data holds what these show; the screens come with the backend of block 0b.

- **Sanand cartons with a prospect.** Apex has a second plant, Plant 2 at Sanand (Gujarat, region GJ), starting in January 2027 with no history. Western Packaging (SUP-004) is approved for cartons in Maharashtra only and the casting contract CNT-0001 is priced for Maharashtra, so a Sanand request for cartons finds no approved supplier and a Sanand request for RM-01 goes to quotation. Sabarmati Corrugators (SUP-008, Ahmedabad) is a prospect the buyer registered on 2 Nov 2026: it can quote, but its quote counts only after someone other than the buyer checks its identity, and no order is made until it is activated. Chakan Enamel Wires (SUP-009) is a prospect flagged for review: same phone and address as Pune Copper (SUP-005). Shivneri Foundry Works (SUP-007) is blocked since 15 Jun 2026.
- **The October copper benchmark to verify.** The copper wire reference rate for October 2026 (897.00 a kg, entered 2 Nov) is unverified, so on 20 Nov the latest counting benchmark is September's, 51 days old and stale at 45 days: the item shows no market evidence until purchasemanager@ verifies October. The RM-01 price list (628.00 in October) is 3.38% under the contract's 650.00: below the 5% threshold, no review. A Gujarat price (600.00) never counts for the Maharashtra contract.
- **The LP-01 5% boundary (Brightwave).** The laptop channel price fell to 58,900.00 on 31 Oct 2026, exactly 5.0000% under CNT-0001's 62,000.00: not above the threshold, so no review opens.
- **Hyderabad housekeeping (Brightwave).** The new Hyderabad office (region TS) opens in January 2027. Clean and Secure (SUP-005) and its contract CNT-0002 cover Karnataka only, so housekeeping for Hyderabad never uses CNT-0002; Deccan Facility Management (SUP-006) is the prospect registered to quote for it.
- **Tolerance versions (Apex).** Version 2 from 1 Jul 2026 removed the quantity tolerance and cut the rupee tolerance to 300.00. Bearings invoiced 3% over what was accepted pass in June (version 1) and are blocked in August (version 2); a line Rs 400 over passes in March and is blocked in August.

## Go-live (block 0d)

`python -m p2p.cli demo reset apex --state setup` makes the company and leaves it in setup (a plain reset ends live).

1. admin@ **Company setup** → **Go-live**: state setup, readiness with no blocker; warnings for the bank accounts verified by a file.
2. Make some test data in setup (a request, an order). Then, as the platform team: `python -m p2p.cli company cutover apex --reason "go-live rehearsal" --operator "Your name"`. The setup database is kept as `..._apex__setup_<time>` (`company archives apex`); the test documents are gone from the company, numbers start again.
3. store@ cannot sign in now ("Sign-in opens at go-live ..."); finance@ can. As finance@, on the Go-live tab, load the files of `data/datasets/apex-v1/cutover/` (dry run, then load): 31 orders, 45 receipts, 44 invoices (Rs 6,05,557; PI-2627-000247 blocked as in the old system), the advance on PO-CKN-2627-00202 and the control totals, all ok. `cutover-bad/` shows every refusal with its reason.
4. admin@ proposes going live; admin2@ approves it. The company is live: everyone signs in, the jobs run, the first payment run takes the cutover invoices.

## Budget and forward buying (block 3)

After `demo reset apex` and the store check (A0), on 23 Nov 2026:

1. **Budget.** As Anil Mehta (finance), open the MRP request: account 1410 of department PRD at PLT-CKN, Apr to Nov 2026,
   Rs 1,16,00,000 in all; Rs 2,76,780 held by requests, Rs 0 by orders, Rs 0 spent; this request asks for Rs 3,64,090,
   5.52% used. Approve MO-1, buy the wire directly from SUP-005 at Rs 880 (the purchase manager agrees), make the orders:
   MO-2's check now shows Rs 69,980 held by orders (the order prices, not the estimates). Receive the 40 kg of wire:
   Rs 35,200 is spent in November at once (D-10). Pay its invoice later: only the Rs 500 freight is added.
2. **Months.** The control tower's budgets panel shows the months: April to October locked, November open. On 7 Dec
   finance locks November with a reason; a payment for a November invoice is then booked in December. An adjustment is
   sent for another finance person to approve. From 1 April 2027 finance closes FY 2026-27: Rs 6,52,120 of open requests
   is carried into the new year.
3. **Forward buying.** As Neha Kulkarni (buyer), enter the Pune wire bulletin at Rs 828 a kg (16 Nov); Sanjay Rao
   verifies it. The MRP request shows "Buy more now while the price is low": 746 kg more for February to April, net saving
   Rs 44,982.36, with the evidence. The buyer accepts ("Copper dip; Q4 plan firm"); Sanjay Rao approves the extra
   quantity, and a new planning request for 746 kg goes to the director for approval with its own budget check (11.44%
   used). Command line: `python -m p2p.cli forward-buy apex --item RM-08 --dry-run`.

## Strategic sourcing (block 5)

After `demo reset apex`, on Mon 23 Nov 2026 (scenario P1, AT-02; the figures are in `truth/truth_period_sourcing.csv`):

1. **Period RFQ.** As Neha Kulkarni (buyer), **Quotations** → **New period RFQ**: Plant, Chakan, 1 Jan to 31 Mar 2027,
   copper winding wire. **Suggest** gives 753 kg (3,009.5 kg bought in the last 12 months, 752.375 for 3 months; 216 kg
   of open requirements); type 900. SUP-003, SUP-005 and SUP-001 are asked (three quotes above Rs 2,00,000). Nothing goes
   out until she ticks it and clicks **Confirm and send selected** (D42): the mail says the period and that the quantity
   is an estimate, not a commitment.
2. **Award.** Enter the quotes (SUP-005 Rs 880, SUP-003 Rs 895, SUP-001 Rs 910, valid to 31 Dec 2026) and **Propose
   award** to SUP-005: Rs 7,92,000 before GST, Rs 9,34,560 with it, so the director (PO-03) decides; no order, no budget.
   As Meera Iyer (director), **Approve award**: one award letter with draft contract CNT-0002 (ceiling Rs 7,92,000, NET45,
   Rs 880 a kg at Chakan) and two regret notes with no prices. Approving on 1 Jan 2027 instead says the award is stale.
3. **Contract.** Open the award letter's link (Mailpit), **Email me a code**, accept. The director cannot activate it
   (she approved the award), nor the buyer; Sanjay Rao (purchase manager) activates it.
4. **Use.** On 4 Jan 2027 a request for 120 kg is bought on CNT-0002 at Rs 880: the order is Rs 1,24,608 with GST for the
   purchase manager, and the contract has Rs 6,86,400 left. Brightwave (B1): only two IT suppliers, so the award needs a
   reason and the purchase agreement before the CFO decides; CNT-0003 is activated by the IT head.

## Quotes and supplier selection (block 6)

Business date Mon 23 Nov 2026. production@ asks for 500 kg of copper winding wire (RM-08) at Chakan, need-by 21 Dec; it is approved and goes to quotation with Shree Wires, Pune Copper and Deccan Castings. buyer@ confirms the send (the RFQ shows **Why these suppliers**).

1. **Negotiation brief.** buyer@ enters Pune Copper 960.00, Shree Wires 975.00 and Deccan Castings 990.00. The RFQ shows **Why this ranking** and a **Negotiation brief**: Pune Copper is lowest at 960.00, above the last price paid, 905.91 on PO-CKN-2627-00199 of 05 Oct 2026 (5.63% gap); ask for 905.91: 27,045.00 less on 500 kg. After purchasemanager@ verifies the October copper benchmark (897.00), the brief adds the market and the target becomes 897.00 (31,500.00 less). A revised 900.00 from Pune Copper withdraws the brief.
2. **Closed short.** On an RFQ where only one supplier quotes by the closing date, the next morning's job gives the buyer a case "closed with 1 of 3 quotes wanted"; **Extend closing** completes it and tells the suppliers.
3. **Path A or B.** With a verified RM-01 benchmark of 600.00 (Maharashtra), a request for 600 castings at Chakan needed in January waits for the buyer: CNT-0001's 650.00 is 7.69% above the market, 30,000.00 on 600. **Ask for quotes (B)** prepares an RFQ; **Buy on the contract (A)** with a reason makes an order that waits for the purchase manager.

## Purchase order versions (block 7)

Business date Mon 23 Nov 2026. Figures: `truth/truth_po_versions.csv`.

1. **Acknowledgement of a version.** An award of 120 kg of copper wire to Pune Copper at 905.00 makes an order of 1,28,148.00 (CGST and SGST 9,774.00 each) that purchasemanager@ approves; buyer@ sends it and the supplier accepts from its link. buyer@ cuts it to 100 kg: version 2 (1,06,790.00) keeps the approval, the supplier gets version 2's document and a new link, and the order shows "Version 2 not yet acknowledged". The first link now says the order changed. **Versions** → **Compare** 1 with 2 shows the quantity, the taxable value, the tax and the total.
2. **A new delivery place (Brightwave).** Two laptops on CNT-0001 for Bengaluru (1,46,320.00 with CGST and SGST), approved by the CFO and acknowledged; buyer@ moves the delivery to Hyderabad: IGST 22,320.00, our Telangana GSTIN, and the order goes back to the CFO.
3. **The contract price version.** An order of 200 castings at 650.00 on CNT-0001 version 1 keeps that price when version 2 (672.00 from 1 Jan 2027) is activated. On 4 Jan 2027 **Use the contract price in force** makes version 2 of the order at 672.00 (1,58,592.00), approved again because the price went up.
4. **A minor change.** Changing the delivery terms of a touchless order to ex works keeps the rule's approval, but the order is no longer touchless; its note says who changed it.

## Start with cases in flight

`python -m p2p.cli demo populate apex --reset` (about 20 seconds, local database only) makes the company and then does what the people would do, through the real API, so every figure, email, notification and audit row is real:

| Where to look | What is there |
|---|---|
| buyer@ **Requests** → **Check stock levels** | Two spares used up with nothing on the way (lathe inserts, hydraulic oil) |
| buyer@ My work | The V-belt quotation request waiting to be sent; the capacitor quotation request with three quotes, not yet awarded |
| production@ **Requests** | A draft (bearings) to submit |
| planthead@ / director@ My work | Winding wire (₹18,400) for the plant head; motor bodies (₹1,30,000) for the director; the payment run for the director |
| purchasemanager@ My work | The copper wire order from an award; the blocked invoice to release |
| later stages | An order approved and waiting to be sent, one acknowledged at version 2 (its delivery terms changed to ex works: on the order, **Versions** → **Compare** version 1 with version 2), one part received (store@), one received and waiting for an invoice (ap@), an invoice not due yet, a run with its bank file made, and one case paid and closed |

It refuses a company that already has requests. To go back to the empty company (what E34 and the journey above expect), run `demo reset apex` again.

## Check that it still works

`python evals/run.py --only E34` runs the journey in a real browser, then follows each role's guide, then opens every screen of every role at laptop and phone width (about three minutes). `python evals/run.py --only E33` does the money side through the API against the dataset's own files. `tests/test_demo_company.py` and `tests/test_demo_populate.py` check the company and the cases in flight.
