# Capability matrix (Phase 0 and Phase 1)

One row per function of the plan. Status: todo, verified (works, evidence given), fixed (was missing or broken, now
works and has a test), deferred (not Phase 1, with the reason) or blocked (needs the owner, with what).
Only a row with evidence may be `verified` or `fixed`.

## Analytics & Insight

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 11.1.1 | Control tower: Requests, POs, deliveries and invoices by unit and stage | Rules | P1-21 | todo |  |  |
| 11.1.2 | Control tower: Ageing and drill-down | Rules | P1-21 | todo |  |  |
| 11.1.3 | Control tower: Home page per role showing what needs action | Platform | P1-21 | todo |  |  |
| 11.1.4 | Control tower: Alerts on stale requests, overruns and late orders | Rules | P1-23 | todo |  |  |
| 11.2.1 | KPIs: Cycle time, spend under management, savings per RFQ, touchless share, duplicates avoided, MSME on-time payment | Rules | P1-21 | todo |  |  |
| 11.4.1 | Assistant: Ask about status, pending items, suppliers and spend | AI agent | P1-22 | todo |  |  |
| 11.4.2 | Assistant: Safe actions by chat: draft a request, remind an approver | AI agent | P1-22 | todo |  |  |

## Automation & AI Control

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 12.1.1 | Agents: Four agents: intake, supplier communication, invoice exceptions, assistant | AI agent | P1-08, P1-14, P1-18 | todo |  |  |
| 12.1.2 | Agents: Agent runtime that pauses for people and resumes | Platform | P0-15 | todo |  |  |
| 12.1.3 | Agents: Viewer of every agent run, model call and cost | Platform | P0-23 | todo |  |  |
| 12.2.1 | AI steps: Single-call AI steps with code checks and fallback to a person | AI | P0-16 | todo |  |  |
| 12.2.2 | AI steps: Read PDFs, scans, Excel and Word | Platform | P0-18 | todo |  |  |
| 12.3.1 | Human confirmation: Sensitive actions wait for an allowed person | Person decides | P0-13 | todo |  |  |
| 12.4.1 | Autonomy: Level per decision type, value cap and instant off switch | Person decides | P1-24 | todo |  |  |
| 12.5.1 | AI quality: Model gateway and cost log | AI | P0-14 | todo |  |  |

## Demand & Requisition

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 1.1.1 | Request intake: Request by form | Platform | P1-04 | verified | tests/test_request_form.py; browser tests/test_demand_screens.py (New request form to a draft, then submit) |  |
| 1.1.2 | Request intake: Request by email or free text, turned into a draft | AI agent | P1-08 | verified | tests/test_intake_agent.py (a colleague's email to the requests mailbox is drafted and answered; a stranger's is not even read; attachments; split-order flag); eval E24 | Live models (Groq/NVIDIA, 10 Nov): right action 93%, one extra line in a draft out of 30. A draft is always checked by the requester before it goes anywhere. |
| 1.1.3 | Request intake: Request by chat | AI agent | P1-08 | verified | tests/test_intake_agent.py (chat sends requests to the intake agent and questions to the assistant); eval E24 |  |
| 1.1.4 | Request intake: Ask the requester for missing details | AI agent | P1-08 | verified | tests/test_intake_agent.py (a missing date is asked once, questions come item then quantity then date, the answer completes the draft, 'cancel' drops the question); eval E24 |  |
| 1.2.1 | Items & catalogue: Item master: category, unit, tax code | Platform | P1-01 | verified | tests/test_masters_import.py (items with category, unit and HSN/SAC load and are checked); tests/test_reference_data.py (HSN to GST rate by date, longest code wins) |  |
| 1.2.2 | Items & catalogue: Catalogue and contract items | Platform | P1-01, P1-04 | verified | tests/test_request_form.py (the form offers the active catalogue); tests/test_sourcing.py and tests/test_masters_documents_contracts.py (contract items are priced from active contracts); eval E03 | The form lists catalogue items; whether an item is bought on contract is decided at sourcing (contract price, else quotation). |
| 1.2.3 | Items & catalogue: Match free-text items to the item master | AI | P1-08 | verified | tests/test_intake_agent.py (words and codes settle an item without a model, typos by similarity, the model sees only the best candidates, a non-match never reaches it); eval E24 (lines mapped right 100%) |  |
| 1.2.4 | Items & catalogue: Default GL account and cost centre by category | Rules | P1-01 | verified | tests/test_request_defaults.py (new: item GL account, else the category's, on form and restock requests); tests/test_budget.py (a line with no cost centre is held against the cost centres of its delivery place) | The behaviour already existed; the GL default test was added. |
| 1.3.1 | Pre-order checks: Stock and open-order check | Rules | P1-02, P1-05 | verified | tests/test_duplicates.py (open requests, open orders and goods on the way are counted); tests/test_replenishment.py (open requests and orders count as stock on the way; an item with an open order is not requested again) |  |
| 1.3.2 | Pre-order checks: Duplicate request check | Rules | P1-05 | verified | tests/test_duplicates.py (same item, place and close dates flagged; a reason is needed and kept with the name); eval E23 (planted repeat requests caught) |  |
| 1.3.3 | Pre-order checks: Suggested net quantity | Rules | P1-05 | verified | tests/test_duplicates.py (20 ordered, 10 received, 20 requested suggests 10; never below zero; in stock units) |  |
| 1.3.4 | Pre-order checks: Stock movements from receipts, issues and adjustments | Rules | P1-02, P1-15 | fixed | tests/test_stock_posting.py (new); tests/test_stock.py (balances, history load); tests/test_receiving.py (receipts post stock); browser tests/test_demand_screens.py (Issue and Count on Inventory) | Receipts already posted stock; issues and adjustments could only be imported as history. Built: Issue and Count on the Inventory screen for the Store role (permissions stock.issue and stock.adjust, migration 0039). Issues cannot go below zero; a count needs a reason and the server works out the difference; both are audited. For the owner: whether a large adjustment should need an approver (not built). |
| 1.4.1 | Request lifecycle: Draft, submit, withdraw, cancel, copy a previous request | Platform | P1-04 | fixed | tests/test_request_lifecycle.py (new); browser tests/test_demand_screens.py (raise, submit, withdraw, submit, cancel with a reason, copy) | Only draft, submit, return and reject existed. Built withdraw (waiting request back to draft), cancel (with a reason, until the buyer starts buying) and copy (new draft in the copier's name), as buttons on the request page, with audit rows request.withdraw, request.cancel and request.copy. |
| 1.4.2 | Request lifecycle: Status tracking for requesters | Platform | P1-04 | verified | tests/test_approvals.py (the list shows status and who it waits for; the trail on the page); tests/test_request_lifecycle.py (what each person may do now); the 'My requests' tab and the progress strip, browser tests/test_demand_screens.py |  |
| 1.4.3 | Request lifecycle: In-app and email notifications | Platform | P0-12 | fixed | tests/test_request_lifecycle.py (new): in-app notification and a queued email for each decision; approvers' tasks and reminders already tested in tests/test_approvals.py and tests/test_email.py | The approver was told when a step opened, but the requester was told nothing when the request was approved, rejected or returned. Now they are, in the app and by email; approvers are told when a request is withdrawn or cancelled. |

## India Compliance

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 9.1.1 | GST: HSN/SAC codes, GST rate table and state-based tax split | Rules | P0-11, P1-13 | todo |  |  |
| 9.1.2 | GST: Composition, unregistered and reverse-charge cases | Rules | P1-01, P1-13 | todo |  |  |
| 9.2.1 | TDS: Rate and threshold rules | Rules | P1-19 | todo |  |  |
| 9.3.1 | MSME: MSME payment deadline tracking | Rules | P1-19 | todo |  |  |
| 9.4.1 | E-invoice: IRN and QR verification | Rules | P1-16 | todo |  |  |

## Integration & Data

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 13.1.1 | Data import and export: Excel import with a dry run; export of any list | Platform | P1-01 | fixed | tests/test_masters_excel.py (new: an Excel dataset loads exactly what the CSV one does, dry run writes nothing, bad rows reported by sheet line); tests/test_masters_import.py (dry run, audit); tests/test_export.py (new) and browser tests/test_demand_screens.py (Export downloads a CSV) | Import took CSV files only and nothing exported. Now the master import also reads .xlsx files (mixed with CSV if wanted) and every main list has an Export button (CSV with a byte-order mark, formula cells defanged, audited). Import is by folder from the command line (python -m p2p.cli import masters <tenant> <dataset> --dry-run); there is no upload screen. |
| 13.1.2 | Data import and export: Reference data: HSN/SAC, GST states, units, holidays | Platform | P0-21 | verified | tests/test_reference_data.py (a new tenant comes with the GST states, units, HSN/SAC codes and rates, TDS sections and holidays; the GST rate depends on the date; holidays feed the business calendar) | A unit count assertion was added to the tests. |
| 13.2.1 | Email: Email in and out, linked to cases | Platform | P0-19 | verified | tests/test_email.py (a reply to an RFQ is stored linked to it and adds a case event; reading a mail twice stores it once; threading by reply address; the poll job; IMAP read); tests/test_intake_agent.py (request mail drafted and answered by email) |  |

## Invoice & Matching

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 6.1.1 | Invoice capture: Invoice by email or upload | Platform | P1-16 | todo |  |  |
| 6.1.2 | Invoice capture: E-invoice QR code read in code | Rules | P1-16 | todo |  |  |
| 6.1.3 | Invoice capture: Read other invoices | AI | P1-16 | todo |  |  |
| 6.1.4 | Invoice capture: Credit notes accepted and netted against payments | Rules | P1-16, P1-17, P1-19 | todo |  |  |
| 6.2.1 | Validation: GSTIN matches the supplier record | Rules | P1-16 | todo |  |  |
| 6.2.2 | Validation: GST arithmetic and totals | Rules | P1-16 | todo |  |  |
| 6.2.3 | Validation: Duplicate invoice check | Rules | P1-16 | todo |  |  |
| 6.2.4 | Validation: Every value read by AI must exist in the document | Rules | P0-16 | todo |  |  |
| 6.3.1 | Matching: Three-way match: PO, goods received, invoice | Rules | P1-17 | todo |  |  |
| 6.3.2 | Matching: Price and quantity tolerances set by rules | Rules | P1-17 | todo |  |  |
| 6.4.1 | Exceptions: Matching exceptions screen | Platform | P1-17 | todo |  |  |
| 6.4.2 | Exceptions: Find the cause of a mismatch | AI agent | P1-18 | todo |  |  |
| 6.4.3 | Exceptions: Propose a fix and draft the supplier query | AI agent | P1-18 | todo |  |  |
| 6.4.4 | Exceptions: Request a credit note | AI agent | P1-18 | todo |  |  |
| 6.4.5 | Exceptions: Person releases the payment block | Person decides | P0-13, P1-18 | todo |  |  |

## Payments

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 7.1.1 | Due date: Due date from payment terms | Rules | P1-19 | todo |  |  |
| 7.1.2 | Due date: MSME payment-day rule: agreed days up to 45, otherwise 15 days | Rules | P1-19 | todo |  |  |
| 7.2.1 | Tax at payment: TDS calculation | Rules | P1-19 | todo |  |  |
| 7.3.1 | Payment run: Propose the payment run by due date, MSME first | Rules | P1-19 | todo |  |  |
| 7.3.2 | Payment run: Finance approves the payment batch | Person decides | P0-13, P1-19 | todo |  |  |
| 7.3.3 | Payment run: Bank file and bank statement import | Rules | P1-19 | todo |  |  |
| 7.3.4 | Payment run: Remittance advice to the supplier | Platform | P1-20 | todo |  |  |
| 7.3.5 | Payment run: Close the case with full history | Rules | P1-20 | todo |  |  |

## Platform & Administration

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 14.1.1 | Client separation: Separate secure space for each client | Platform | P0-05, P0-06 | todo |  |  |
| 14.2.1 | Users and security: Sign-in and roles | Platform | P0-07, P0-08 | todo |  |  |
| 14.2.2 | Users and security: Security testing and hardening | Platform | P1-26 | todo |  |  |
| 14.3.1 | Rules and flow: Business rules in tables an admin can change | Rules | P0-10, P0-11 | todo |  |  |
| 14.3.2 | Rules and flow: Case flow, tasks and timers | Platform | P0-12 | todo |  |  |
| 14.4.1 | Audit and storage: History of every action | Platform | P0-09 | todo |  |  |
| 14.4.2 | Audit and storage: File storage per client | Platform | P0-17 | todo |  |  |
| 14.5.1 | Web app: Web app shell and admin pages | Platform | P0-20 | todo |  |  |
| 14.6.1 | Demo and test data: Demo company with a year of history | Platform | P0-21 | todo |  |  |
| 14.6.2 | Demo and test data: Test documents and cases with answer keys | Platform | P1-03 | todo |  |  |

## Purchase Orders

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 4.1.1 | PO creation: Group lines by supplier, ship-to and terms | Rules | P1-13 | todo |  |  |
| 4.1.2 | PO creation: Price taken from the contract or the award | Rules | P1-13 | todo |  |  |
| 4.1.3 | PO creation: GST split: CGST+SGST or IGST | Rules | P1-13 | todo |  |  |
| 4.1.4 | PO creation: PO numbering and versions | Rules | P1-13 | todo |  |  |
| 4.1.5 | PO creation: PO document (PDF) | Platform | P1-13 | todo |  |  |
| 4.2.1 | PO approval & release: Re-check budget and approval limit on the final amount | Rules | P1-13 | todo |  |  |
| 4.2.2 | PO approval & release: Approval through the approval matrix | Person decides | P1-13, P1-07 | todo |  |  |
| 4.2.3 | PO approval & release: Person confirms sending; PO emailed to the supplier | Person decides | P0-13, P1-13 | todo |  |  |
| 4.3.1 | Supplier confirmation: Record the supplier's acknowledgement | AI agent | P1-14 | todo |  |  |
| 4.3.2 | Supplier confirmation: Accept small changes within agreed limits | AI agent | P1-14 | todo |  |  |
| 4.3.3 | Supplier confirmation: Chase when there is no reply or the date slips | AI agent | P1-14 | todo |  |  |
| 4.4.1 | PO changes: Cancel or close a PO | Rules | P1-13, P1-20 | todo |  |  |

## Receiving

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 5.1.1 | Goods receipt: Record a full delivery | Rules | P1-15 | verified | tests/test_receiving.py::test_a_full_receipt_closes_the_line_the_order_and_puts_the_goods_into_stock; E18; E30 typed run (Goods receipt > Receive goods) |  |
| 5.1.2 | Goods receipt: Part delivery keeps the balance open | Rules | P1-15 | verified | test_receiving.py::test_a_part_receipt_leaves_the_order_open_for_the_rest, ::test_what_is_received_in_parts_counts_together_against_the_tolerance; E18; E30 part-delivery run | The order closes only when every line is complete. |
| 5.1.3 | Goods receipt: Over and under delivery tolerance | Rules | P1-15 | verified | test_receiving.py::test_over_delivery_is_accepted_up_to_the_tolerance_of_the_category_and_not_a_unit_more, ::test_a_category_with_its_own_tolerance_uses_it, ::test_two_receipts_at_the_same_moment_cannot_together_take_in_more_than_the_order_allows; formulas tests; E18 | Tolerance is data (receipt_over_pct, by category). |
| 5.1.4 | Goods receipt: Rejected or damaged quantity | Rules | P1-15 | verified | test_receiving.py::test_rejected_goods_need_a_reason_stay_out_of_stock_and_leave_the_line_open_for_a_replacement, ::test_an_item_that_needs_inspection_waits_and_nothing_counts_until_it_is_inspected; E18 |  |
| 5.1.5 | Goods receipt: Tell the requester what arrived | Platform | P1-15 | fixed | tests/test_receiving.py::test_the_requester_and_the_buyer_are_told_what_arrived_and_what_was_rejected, ::test_what_is_waiting_for_inspection_is_said_so_and_the_result_is_told_again, ::test_a_failure_to_tell_people_never_undoes_the_receipt, ::test_the_api_lets_a_store_person_receive_and_nobody_else | Nobody was told when goods arrived. Now `goods.tell_requesters` notifies each requester and the buyer (app and email: what was accepted, rejected or waits for inspection), from the receipt and inspection endpoints. `notifications.login_of` is the shared lookup (payment closing uses it too). |
| 5.3.1 | Short or bad goods: Chase the supplier for the balance of a part delivery | AI agent | P1-14 | verified | tests/test_supplier_agent.py::test_the_open_balance_of_a_late_part_delivery_is_chased_with_the_quantities, ::test_a_part_delivery_not_yet_late_is_not_chased; E28; E30 part-delivery run |  |

## Sourcing

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 3.1.1 | Source determination: Use a valid contract or price record, otherwise raise an RFQ | Rules | P1-09 | verified | tests/test_sourcing.py: each_line_is_bought_on_contract_or_goes_into_a_quotation_request, only_a_contract_price_in_force_on_the_date_counts, a_quantity_below_the_first_price_break_is_quoted; E03; E30 |  |
| 3.1.2 | Source determination: Skip blocked suppliers, expired documents, unapproved categories | Rules | P1-09 | verified | tests/test_sourcing.py: a_supplier_that_is_no_longer_active_has_no_contract_price, a_supplier_with_a_document_missing_or_expired_is_not_asked_to_quote, a_contract_supplier_with_bad_documents_is_not_used, the_suppliers_asked_are_those_approved_for_the_category; E16 |  |
| 3.2.1 | RFQ (request for quotation): Create an RFQ from approved request lines | Rules | P1-10 | verified | tests/test_sourcing.py: the_rfq_carries_its_lines_the_due_date_and_the_buyer, one_quotation_request_is_made_for_each_category_in_the_request, lines_nobody_can_be_asked_about_are_left_for_a_buyer; E03; E30 |  |
| 3.2.2 | RFQ (request for quotation): Select approved suppliers for the category | Rules | P1-10 | verified | tests/test_sourcing.py: the_suppliers_asked_are_those_approved_for_the_category_with_a_history_first, the_number_of_suppliers_asked_is_a_setting, fewer_approved_suppliers_than_the_category_wants_is_flagged_on_the_rfq |  |
| 3.2.4 | RFQ (request for quotation): Prepare the RFQ text from an editable template | Rules | P1-10 | fixed | tests/test_rfq_template.py (templates, saved template used in the sent email, bad template refused, unusable one falls back); E22 (setting validated and logged); Admin > Company setup > Policies textarea | Was missing: the wording was fixed in code. Now the policy `rfq_email_template` (fields {contact} {company} {reference} {due_date} {deliver_to} {items}; {items} and {reference} required; stray braces refused). Empty = standard wording. Also built the plan's Excel quote form: every RFQ email carries `<RFQ>-quotation-form.xlsx` (sourcing/quote_sheet.py, tested). |
| 3.2.5 | RFQ (request for quotation): Person approves before sending | Person decides | P0-13, P1-10 | verified | tests/test_sourcing.py: by_default_the_rfq_waits_for_a_buyer_to_approve_sending, a_buyer_who_approves_the_proposal_sends_the_emails, the_kill_switch_stops_automatic_sending, a_value_above_the_autonomy_cap_waits_for_a_person; E30 (approval under Admin > Approvals) |  |
| 3.2.6 | RFQ (request for quotation): Send by email and track replies | Rules | P0-19, P1-10 | verified | tests/test_sourcing.py: the_rfq_really_arrives_in_the_suppliers_mailbox_with_the_reply_address, one_supplier_whose_email_fails_does_not_hold_up_the_rest_and_is_retried; tests/test_quote_reading.py: mail_that_arrives_for_an_rfq_queues_a_job_to_read_it; E30 (document runs) |  |
| 3.2.7 | RFQ (request for quotation): Answer supplier questions and send reminders | AI agent | P1-14 | fixed | tests/test_sourcing.py: suppliers_who_stay_silent_are_reminded_twice_and_then_left_alone (+ 6 more reminder tests); tests/test_quote_reading.py: a_question_comes_with_a_drafted_reply_that_restates_the_request_and_is_never_sent | Reminders were done. Answering was half there: a question on an RFQ made a buyer task with nothing prepared. Now the task carries a drafted reply (the question, a promise of an answer, reply-by date, delivery place, items). Never sent by itself: a person answers (by design). Questions on orders already had drafts (E28). |
| 3.3.1 | Quote capture: Read quotes from email: PDF, Excel, Word | AI | P1-11 | verified | tests/test_quote_attachments.py: a quotation in a PDF, Excel and Word attachment is read into a draft; same reading with no attachment goes to a person; an unreadable attachment does not stop the email; E13 (live 94.9% exact earlier, 0 silent errors) | PDF, Excel and Word were read already but had no test; the attachment tests are new. Scanned or photographed quotes use the same OCR parser as scanned invoices (tests/test_invoice_reading.py); no quote-specific scan test. |
| 3.3.2 | Quote capture: Manual quote entry | Platform | P1-10 | verified | tests/test_orders.py: a_quote_is_stored_as_a_revision_and_marks_the_supplier_as_replied, a_quote_with_nonsense_numbers_is_refused, a_buyer_enters_a_quote_and_awards_over_the_api_and_a_requester_cannot; E30 (Enter a quote on the Quotations page) |  |
| 3.4.1 | Evaluation & award: Landed cost: price, freight, tax | Rules | P1-12 | fixed | tests/test_comparison.py (freight split to the paisa, landed cost and GST); tests/test_quote_comparison.py: a_dearer_unit_price_can_still_be_the_cheapest_once_freight_and_tax_are_counted, a_quotes_freight_is_shared_over_the_lines...; tests/test_orders.py rfq_page_data; E05 | Was missing: offers were ranked on unit price only, freight and packing were ignored. Now every offer is costed to our door (price + its share of the quote's freight and packing + GST) and compared on that. Quotations page shows 'TO OUR DOOR' with the parts. The shared test fixture was changed on purpose (S2 190 -> 200) because S1's 75.50 freight now rightly makes it dearer than 190. |
| 3.4.2 | Evaluation & award: Compare with last price and other quotes | Rules | P1-12 | fixed | tests/test_quote_comparison.py: a_price_far_below_the_other_quotes_is_a_question..., a_price_far_below_the_last_price_paid_is_a_question_even_with_one_quote, a_recommendation_with_a_question_is_never_awarded_by_itself, the_question_can_be_switched_off; tests/test_comparison.py boundaries | Was missing. Each offer now carries the last price paid for the item and a question when its price is 40% or more below the median of the other quotes or below the last price (setting `low_price_flag_pct`, 0 = off). A recommendation with a question is never auto-awarded. Savings per RFQ stay in the control tower KPIs (row 11.2.1). |
| 3.4.3 | Evaluation & award: Weighted score with knock-out rules | Rules | P1-12 | fixed | tests/test_quote_comparison.py: the_weighted_comparison_recommends_the_best_score_and_says_how_it_was_scored, in_the_weighted_comparison_a_knocked_out_offer_is_never_recommended, the_default_comparison_is_by_price_alone; E22 (settings) | Knock-outs (expired, below minimum order, too late, wrong GST rate) existed; the weighted score did not. Settings: `comparison_mode` (price = default, weighted), weights `score_weight_price/delivery/quality` (40/40/20), using the vendor delivery and quality record (suppliers/scoring.py) and landed cost for price. |
| 3.4.4 | Evaluation & award: Recommendation with reasons from the score breakdown | Rules | P1-12 | fixed | tests/test_quote_comparison.py (reason text for landed cost, for the score with its price/delivery/quality parts and weights, and for a price needing a question); tests/test_orders.py (price wording unchanged) | Reasons come from a template over the numbers that decided it, no model. In weighted mode they list the score breakdown. |
| 3.4.5 | Evaluation & award: Person chooses the supplier | Person decides | P1-12 | verified | tests/test_orders.py: choosing_another_supplier_needs_a_reason_and_is_marked_as_an_override, a_non_compliant_offer_can_only_be_awarded_with_a_reason, too_few_quotes_need_a_reason; E05; E30 (Award the selected suppliers) |  |
| 3.5.1 | Contracts & price records: Price records and validity dates | Rules | P1-01, P1-09 | verified | tests/test_sourcing.py: only_a_contract_price_in_force_on_the_date_counts, a_supplier_is_held_to_the_best_break_the_quantity_reaches; tests/test_masters_documents_contracts.py (price validity follows the contract) |  |
| 3.5.2 | Contracts & price records: Contract expiry alerts | Rules | P1-23 | verified | tests/test_workspace.py: contracts_that_end_soon_or_are_nearly_used_alert_the_buyer; E29 |  |

## Spend Control

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 2.1.1 | Budget: Budgets by cost centre and GL account | Rules | P1-06 | todo |  |  |
| 2.1.2 | Budget: Reserve on approval, move to PO, actual on invoice | Rules | P1-06 | todo |  |  |
| 2.1.3 | Budget: Release on cancel | Rules | P1-06 | todo |  |  |
| 2.1.4 | Budget: Warn or block when over budget, set per client | Rules | P1-06 | todo |  |  |
| 2.2.1 | Approval workflow: Approval route by value, category and department | Rules | P0-10, P1-07 | todo |  |  |
| 2.2.2 | Approval workflow: Approver inbox: approve, edit, reject, return with comment | Person decides | P1-07 | todo |  |  |
| 2.2.3 | Approval workflow: Approve from email | Person decides | P1-07 | todo |  |  |
| 2.2.4 | Approval workflow: Delegation with dates | Rules | P1-07 | todo |  |  |
| 2.2.5 | Approval workflow: Reminders and escalation | Rules | P0-12, P1-07 | todo |  |  |
| 2.2.6 | Approval workflow: Separation of duties (no one approves their own request) | Rules | P0-08 | todo |  |  |
| 2.3.1 | Auto-approval: Small in-policy requests approved by the system | Rules | P1-07 | todo |  |  |
| 2.3.2 | Auto-approval: Reason written down for every auto-approval (the rule that matched) | Rules | P1-07 | todo |  |  |

## Supplier Management

| ID | Function | How | Plan step | Status | Evidence (test, eval or screen) | Notes |
|---|---|---|---|---|---|---|
| 8.1.1 | Supplier records: Create and maintain supplier records | Platform | P1-01 | fixed | tests/test_supplier_master.py (service and API tests); tests/test_supplier_screens.py (browser: Onboard vendor, details panel) | There was no way to create or edit a supplier except import. Built `masters/suppliers.py`: create (always a draft, with address, contact, bank), edit (PAN/GST/MSME locked once in use), audit rows; API POST/PUT /api/vendors; the Vendors page has a real onboarding form and a Details panel. |
| 8.1.2 | Supplier records: GSTIN, PAN, GST registration type, MSME category and bank checks | Rules | P1-01 | fixed | tests/test_supplier_master.py (boundary tests for GSTIN check character, PAN inside GSTIN, Udyam format, account 9-18 digits, IFSC, duplicates) | New pure rules in `formulas/supplier_master.py`; a duplicate GSTIN, PAN with the same name, or the same bank account is refused. A bank change on a supplier in use is a proposal another person approves, the bank proof is unverified again, and the audit keeps only the last 4 digits. E09 has the new endpoints. |
| 8.1.3 | Supplier records: Payment terms master | Platform | P1-01 | verified | payment_terms table drives due dates (tests/test_payments.py, E20); terms are chosen on the onboarding form (GET /api/payment-terms) and validated | Maintained by master import (CSV or Excel, with dry run). There is no screen to edit the terms themselves: the owner may want one. |
| 8.1.4 | Supplier records: Activate, block and unblock a supplier | Person decides | P0-13, P1-01 | verified | tests/test_supplier_status.py (proposal, another person approves, documents re-checked, block, reactivate); Vendors page status buttons; E16 | Activation, block and unblock are proposals (rule 5). |
| 8.2.1 | Onboarding: Onboarding form with document upload | Platform | P1-01 | fixed | tests/test_supplier_screens.py::test_a_buyer_onboards_a_vendor_adds_a_document_with_its_scan_and_a_second_person_verifies_it; tests/test_supplier_master.py::test_a_scan_is_kept_with_its_document_and_only_a_scan_is_accepted | The Onboard vendor button used to open the assistant with a placeholder sentence. Now a form saves a draft, documents can be added and a PDF/PNG/JPG scan attached to each (POST /api/vendors/{id}/documents/{doc}/file). Verification by a second person and activation by an approver are unchanged. |
| 8.3.2 | Qualification & scorecards: Document expiry alerts | Rules | P1-23 | verified | tests/test_workspace.py::test_a_supplier_document_that_is_wrong_or_about_to_expire_alerts_the_buyer; E29; E16 (expired documents block use) |  |
| 8.4.1 | Communication: Every supplier email linked to its RFQ or PO | Platform | P0-19 | verified | tests/test_email.py::test_a_reply_to_an_rfq_is_stored_linked_to_the_rfq_and_adds_a_case_event, ::test_our_outgoing_mail_carries_the_reply_address_and_a_reply_without_a_tag_is_linked_by_thread; test_supplier_agent.py; E30 document runs; Supplier messages panel on the order and RFQ pages |  |

## Plan steps (for reference)

- P0-01: Prepare the machine: Free 40-50 GB on C:. Confirm Docker Desktop, Python 3.12, Node 24, Git. Create free API keys (Groq and Gemini) and keep them in backend/.en
- P0-02: Create the repository: Folders: backend/ (FastAPI app with one package per area: masters, requests, approvals, sourcing, orders, receiving, invoices, payments, 
- P0-03: Docker Compose for services: Services: PostgreSQL (enable pg_trgm for text similarity) and Mailpit (test mailbox). App code runs natively. Named volumes, health
- P0-04: Write CLAUDE.md for the repo: Rules for Claude Code: package boundaries; money is always Decimal; an AI model never computes a number a decision uses; every wri
- P0-05: Control database and tenant provisioning: SQLAlchemy 2 + Alembic. Control DB tables: tenant, tenant_database (connection string encrypted with a key from env), 
- P0-06: Per-request tenant routing: FastAPI dependency resolves the tenant from the login token, fetches its connection, keeps a small cache of engines, gives the reque
- P0-07: Login and users: Own minimal auth: argon2 password hashes, short-lived JWT access token with tenant and role claims, refresh token, lockout after repeated failu
- P0-08: Roles, limits and segregation of duties: Roles: Requester, Approver, Buyer, Store, AP, Finance, Admin, Supplier (later). Permission check functions by role + un
- P0-09: Tamper-proof audit log: Table audit_event: actor type (user, agent, service, rule), actor id, action, entity, before and after JSON, reason, rule version, model
- P0-10: Rules engine with editable decision tables: GoRules ZEN engine (Python package) evaluates JDM decision tables stored in rule_table (key, version, status). Every
- P0-11: Formula library: Reference-data loader first (HSN/SAC codes, a versioned GST rate table seeded from the GST rate notifications, GST state codes, units of measur
- P0-12: Case flow engine and human tasks: Deterministic state machine, one case per purchase: request, approval, sourcing, order, supplier, receipt, invoice, payment, c
- P0-13: Human confirmation mechanism: A 'proposal' record for any sensitive action: action, payload, who or what proposed it, risk, status (pending, approved, edited, r
- P0-14: Model gateway: One interface: complete and extract(schema). Provider adapters: OpenAI-compatible (Groq, Gemini, OpenRouter, local Ollama) and Anthropic. Model c
- P0-15: Agent runtime: LangGraph (Python) with Postgres checkpoints. Tool registry: each tool is a service function with name, description and JSON-schema parameters (s
- P0-16: AI step runner: Shared wrapper for single-call AI steps: render prompt, call gateway with schema, validate with Pydantic, evidence check (FR38: every extracted 
- P0-17: File storage: Files on local disk in a per-tenant folder behind a small storage interface (put, get, delete); download only through an authenticated endpoint; d
- P0-18: Document parsing: Docling with RapidOCR (or Tesseract) wrapped as a backend function: PDF, scans, Excel, Word into text and tables with page positions. Original
- P0-19: Email in and out: Send with smtplib (Mailpit locally). Read mail behind a mailbox interface: from Mailpit's REST API in development (Mailpit has no IMAP) and th
- P0-20: Web shell and admin pages: React + Vite + TypeScript, shadcn/ui, TanStack Router and Query. Login, tenant name, navigation by role, light theme, Indian number f
- P0-21: Data generator v0: Generator (datagen/) with seed and version, grown with each P1 schema step: org units, departments, cost centres, GL accounts, users with rol
- P0-22: Scenario test harness: pytest tests named after scenarios, with given, when and then in the test body. The first 15 describe SAP and Dynamics 365 behaviour we c
- P0-23: Logs and run viewer: Structured logs. Admin viewer of agent runs (steps, tool calls, waits), model calls (tokens, cost) and rule evaluations.
- P1-01: Masters, thin: Org units and locations, departments, cost centres, GL accounts. Items: goods or service, category, UoM and conversions, pack size and MOQ, HSN o
- P1-02: Stock and open supply: stock_balance and movements: receipts are posted by the goods receipt step; issues and adjustments are imported or entered by stores. Inv
- P1-03: Documents generator v1: Render quotes (PDF, Excel, Word, email text) in several layouts, GST invoices with QR and deliberate errors, supplier replies of each ki
- P1-04: Request screens: Create a request from catalogue search or free fields: lines with quantity, UoM, need date, ship-to, cost centre, GL account, justification, at
- P1-05: Check before ordering: Show stock, open requests, open PO balance and inbound for each line and the suggested net quantity (FR01); override needs a reason. Dupl
- P1-06: Budget: Budgets by cost centre and GL account per period. Commit on approval, move to PO, actual on invoice, release on cancel (FR27). Policy per tenant: warn o
- P1-07: Approvals: Approval matrix as a ZEN decision table (FR28): value band, category, unit, department, spend type to ordered approver roles. Delegation with dates, 
- P1-08: Slice B: Intake agent (AG01): Implement AG01 from the Agents sheet: plain-language request by chat or email becomes a draft request (free-text items are matched
- P1-09: Source determination: FR11: valid contract, then approved price record, else RFQ. Skips blocked vendors, expired vendor documents, unapproved categories. Quota 
- P1-10: RFQ, thin: Create RFQ from request lines; pick approved vendors for the category; the text comes from an editable template; proposal for a person to approve bef
- P1-11: Slice B: Quote reading: AI step S01 reads PDF, scan, Excel, Word and email-body quotes into quote lines; review screen shows document and fields side by side; a
- P1-12: Comparison and award: Landed cost (FR13), simple benchmark vs last price and other quotes (FR15), weighted score with knock-outs (FR16). Comparison screen with 
- P1-13: Purchase order: Group lines by vendor, ship-to, currency and terms (FR43); price from contract or award (FR10); GST split (FR25); terms from template; PO number
- P1-14: Slice B: Supplier communication agent (AG02): Implement AG02: reads supplier emails (quotes, acknowledgements, change requests, questions), classifies (AI step 
- P1-15: Goods receipt: Receipt against PO lines: partial, over and under tolerance (FR20), damaged quantity, close short. Open balance stays as supply. A stock movement
- P1-16: Invoice intake: Invoice by email or upload (manual entry works without AI). Credit notes are accepted as negative invoices linked to the original invoice or the
- P1-17: Three-way match and exceptions screen: FR21: per line, invoiced quantity within accepted received quantity minus already invoiced; price within tolerance of PO;
- P1-18: Slice B: Invoice exceptions agent (AG03): Implement AG03: investigates why an invoice is blocked using order, receipts and supplier emails; explains in plain wo
- P1-19: Payment planning: Due date from payment terms with the MSME rule (FR23); TDS computed when the invoice is matched and booked, at the earlier of booking and paym
- P1-20: Case closing: On payment: budget actuals, close request and PO lines, remittance advice emailed to the supplier, notify the requester, complete the case history
- P1-21: Role workspaces and control tower: Home per role showing only items needing action. Control tower by unit and stage with ageing buckets (FR46) and drill-down. K
- P1-22: Slice B: Assistant agent (AG04): Implement AG04: chat panel answers from live data (status, what is pending with me, why this vendor, spend) with read-only tool
- P1-23: Alerts: Rule checks (flagged price, stale requests, budget overrun, contract expiry, vendor document expiry, late PO) create notifications through the core buil
- P1-24: Automation controls: Per decision type: level (recommend, act with approval, act within limits), value cap, confidence threshold, kill switch, change history. P
- P1-25: Demo scenarios: Three scripted end-to-end runs in Playwright: (1) normal run request to paid, (2) part delivery, (3) price mismatch on invoice. Each is run with
- P1-26: Security pass: Prompt-injection test documents (agents ignore embedded instructions), tool allow-list per agent, secrets only in env, rate limits, dependency au
- P1-27: Short guides: One page per role in plain language; admin page on rules and automation controls.
