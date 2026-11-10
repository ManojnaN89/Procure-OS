# Data dictionary (version 2)

The single reference for every table the platform stores and the generator produces. The backend's Pydantic models are built from this file; the generator imports those models.

**Types:** `code` readable key · `text` · `int` · `money` (2 decimals, text in CSV) · `qty` (3 decimals) · `pct` (percent, up to 3 decimals) · `date` (YYYY-MM-DD) · `ts` (ISO timestamp +05:30) · `bool` · `enum(...)` · `json` · `→table` reference by code.

**Every table** also has `created_at`, `created_by`, `updated_at`. One database per client, so there is no client column.

**Who fills it:** **R** = real reference data · **G** = generator · **S** = produced by the running system (not generated; listed so nothing is forgotten).

---

## A. Reference data (R, shared by all clients)

| Table | Columns | Used by |
|---|---|---|
| gst_state | code, name | GSTIN checks, tax split |
| uqc_unit | code, name | units on items and invoices |
| hsn_sac | code, kind enum(goods,service), description | item master, invoice checks |
| gst_rate | hsn_pattern (code, range or chapter), exclusions, kind, rate_pct, cess_pct, itc_condition, reverse_charge, effective_from, effective_to, source | tax on PO and invoice (FR25), the 22 Sep 2025 change |
| holiday | date, name, scope (central or state code) | business calendar, SLAs, due dates |
| tds_section | act enum(1961,2025), section, new_act_ref, nature, payee_type, rate_pct, rate_no_pan_pct, threshold_single, threshold_annual, base_rule, effective_from, effective_to | TDS (FR26) |
| rbi_bank_rate | rate_pct, effective_from | MSME interest (FR44) |
| compliance_parameter | key, value, effective_from, source | e-invoice turnover limit, e-way bill limit, HSN-digit rule, invoice-number length |
| bank | code, name, ifsc_prefix | valid-format supplier and company bank details |
| pincode | pincode, district, state_code | addresses consistent with state |

## B. Master data (G, one set per client)

### B1. Organisation

**company** · legal_name, short_name, pan, cin, registered_address, turnover_band enum(<5cr,5-10cr,10-500cr,>500cr), fy_start_month int, currency, logo_ref, letterhead_text · *switches: TDS on goods, e-invoice duty for our own sales (not used), document headers*

**company_bank_account** · code, bank_code, account_no (fictional), ifsc, purpose enum(payments,receipts), payment_file_format enum(generic_csv,bank_specific) · *payment file (P1), bank reconciliation*

**gst_registration** · gstin, state_code, legal_address, pincode, is_default · *buyer GSTIN on PO and invoice must match the delivery state*

**location** · code, name, type enum(office,plant,warehouse), gstin →gst_registration, address, city, state_code, pincode, is_stock_location, working_days (e.g. Mon-Sat), receiving_hours, holiday_scope (central + state) · *ship-to, stock, calendar*

**department** · code, name, head_user →user

**cost_centre** · code, name, department_code, location_code, owner_user, active

**gl_account** · code, name, type enum(asset,inventory,expense,liability), is_capex, is_tax_account, parent_code

**project** · code, name, type enum(capex,internal), cost_centre_code, budget_amount, start_date, end_date, status enum(open,closed) · *capital spend goes to capital work in progress by project*

**number_series** · document_type, location_code, fy, prefix, next_no · *PO, request, GRN and payment numbering per unit and year*

### B2. People and control

**user** · code, employee_no, name, designation, email, phone, department_code, location_code, manager_code, approval_limit money, notify_by enum(email,in_app,both), active

**user_role** · user_code, role enum(requester,approver,buyer,store,ap,finance,admin,supplier_portal), scope_location, scope_department

**delegation** · from_user, to_user, start_date, end_date, reason

**approval_rule** · rule_code, document enum(request,po,po_amendment,price_variance,payment_run,supplier_activation), min_value, max_value, category_code|ANY, department_code|ANY, spend_type enum(opex,capex,ANY), approver_chain (e.g. `manager>dept_head>director`, `+cfo`), version, effective_from

**budget** · cost_centre_code, gl_code, project_code (optional), period (YYYY-MM), amount, version, policy enum(warn,block)

**setting** · key, value · *auto_approve_limit, budget_policy, working Saturday rule, default RFQ response days, reminder intervals, holding_cost_pct, ordering_cost, cost_of_capital_pct, default service level, duplicate-check window, autonomy levels*

**document_template** · code, type enum(po_pdf,rfq_email,po_email,reminder,ack_request,invoice_query,payment_advice,approval_request,alert), version, subject, body (with slots) · *templates replace AI-written text (decision D20)*

**terms_template** · code, type enum(po_goods,po_service,po_capex,rfq), version, text · *terms printed on PO and RFQ*

### B3. Catalogue

**category** · code, name, parent_code, kind enum(goods,service), default_gl, stocked, capex, min_quotes, buyer_user, approval_category, spec_template json (required attributes), required_supplier_docs (list), inspection_required, default_tds_section, reverse_charge_default, sac_or_hsn_hint

**item** · code, name, description, category_code, kind, brand, manufacturer, mpn (manufacturer part no.), base_uom, purchase_uom, pack_size, moq, hsn_sac, gst_rate_override, standard_price money, last_price money, attributes json (per spec_template), abc_class enum(A,B,C), stocked, capex, inspection_required, hazardous, shelf_life_days (healthcare pack only), active

**uom_conversion** · item_code, from_uom, to_uom, factor qty

**item_substitute** · item_code, substitute_code, note · *supplier offers an alternate (AG02), intake suggestions*

**item_location** · item_code, location_code, reorder_policy enum(none,rop,minmax), reorder_point, safety_stock, min_qty, max_qty, service_level_pct, lead_days_override · *planning (P3); netting (P1)*

### B4. Suppliers

**supplier** · code, legal_name, trade_name, legal_form enum(pvt_ltd,public_ltd,llp,partnership,proprietorship), pan, pan_status enum(operative,inoperative), cin_llpin, gst_reg_type enum(regular,composition,unregistered), turnover_band, e_invoice_applicable, hsn_digits enum(4,6), msme_category enum(micro,small,medium,none), udyam_no, msme_agreement bool, agreed_payment_days, payment_terms_code, tds_section_default, lower_tds_rate, lower_tds_valid_to, lower_tds_limit, reverse_charge, status enum(draft,active,blocked), blocked_reason, website

**supplier_address** · supplier_code, type enum(registered,dispatch,billing), gstin (a supplier can hold one per state), address, city, state_code, pincode · *the dispatching GSTIN decides IGST vs CGST+SGST*

**supplier_contact** · supplier_code, name, role enum(sales,accounts,dispatch,owner), email, phone, is_primary

**supplier_bank_account** · supplier_code, bank_code, account_no (fictional), ifsc, verified bool, verified_on

**supplier_category** · supplier_code, category_code, approved_from

**supplier_document** · supplier_code, doc_type enum(gst_cert,pan,udyam,msme_agreement,iso,licence,insurance,lower_tds_cert,cancelled_cheque), number, issued_on, expires_on, verified, file_ref

**supplier_status_history** · supplier_code, at, from_status, to_status, reason, by_user

**sanction_entry** · name, country, list_name, listed_on · *small fictional list to test screening (P2)*

**payment_terms** · code, name, days, baseline enum(invoice_date,receipt_date,acceptance_date), advance_pct, discount_pct, discount_days

### B5. Prices and rules

**contract** · code, supplier_code, type enum(rate_contract,service_contract,annual_maintenance), valid_from, valid_to, max_value, payment_terms_code, delivery_terms enum(for_destination,ex_works,door_delivery), freight_terms, warranty_months, sla_text, status enum(draft,active,expired,terminated)

**price_record** · supplier_code, item_code, contract_code (optional), uom, price, min_qty (quantity break), price_basis enum(ex_works,for_destination), freight_included, valid_from, valid_to, lead_days

**tolerance** · key enum(price_pct,price_abs,qty_pct,small_diff_abs,receipt_over_pct,supplier_change_pct,supplier_date_days), value, category_code|ANY

**stock_opening** · item_code, location_code, qty, unit_cost, as_of

## C. Transactions (G for history; S once the platform runs)

### C1. Requests and approvals

**request** · no, requester, on_behalf_of, department_code, ship_to_location, type enum(goods,service,capex), priority enum(normal,urgent), project_code, created_at, need_by, channel enum(form,email,chat,planning), source_ref (email id), justification, preferred_supplier, status enum(draft,submitted,approved,rejected,returned,cancelled,closed), total_value

**request_line** · request_no, line_no, item_code (blank for free text), free_text, attributes json, qty, uom, est_price, need_by, delivery_location, cost_centre_code, gl_code, category_code, project_code, status enum(open,sourcing,ordered,cancelled)

In the platform, `request_line` also has `base_qty` (the quantity in the stock unit, so open requests count as stock on the way) and `reason` (why a planning line was raised). A request raised by replenishment has channel `planning` and no requester. `number_counter` holds the last number used per document type, location and financial year; the format of each series is the `number_series` master.

**approval_event** · document, document_no, step, approver, on_behalf_of (delegation), action enum(approved,rejected,returned,delegated,auto_approved,escalated), at, comment, rule_code

In the platform the approval trail is `approval_step` (request_no, round, step, role, approver, delegate, status, acted_by, acted_at, comment, task_id): one row per person who has to act, one round per submission, so a returned request starts a new round. `request` also carries `rule_code`, `rule_version`, `decision_reason`, `submitted_at`, `decided_at` and `approval_round`, so every decision can be repeated against the rule version it used. `approver_role` says how a role named in a chain becomes a person: the requester's manager, the department's head, the head of a named department, or the holder of a designation. Audit rows carry the actions `request.submit`, `request.auto_approve`, `request.approve`, `request.reject` and `request.return`.

### C2. Sourcing

**rfq** · no, buyer, created_at, due_date, type enum(commercial,combined), weights json, delivery_terms, payment_terms_proposed, terms_version, status enum(draft,sent,closed,awarded,cancelled), sent_at

**rfq_line** · rfq_no, line_no, request_no, request_line_no, item_code, description, attributes json, qty, uom, need_by

**rfq_supplier** · rfq_no, supplier_code, invited_at, reminders_sent int, responded_at, declined bool, decline_reason

**quote** · no, rfq_no, supplier_code, revision int (negotiation round), received_at, valid_until, payment_terms_code, delivery_terms, packing_forwarding money, freight money, taxes_extra bool, warranty_months, deviations, document_ref

**quote_line** · quote_no, rfq_line_no, item_code, offered_brand, qty, moq, unit_price, discount_pct, gst_rate, delivery_days, deviation

**award** · rfq_no, rfq_line_no, supplier_code, quote_no, qty, price, reason, overridden bool, by_user, at

In the platform an `rfq` is made for one approved request and one category (`request_no`, `category_code`), with `proposal_id` while sending waits for a buyer, and `note` when fewer suppliers are approved than the category asks for. `rfq_supplier` also keeps the address used (`email`) and `send_error`. `sourcing_decision` holds, for each request line, how it is bought: `contract` (supplier, contract, price, the reason in words), `rfq` (the RFQ number) or `manual` (nobody could be asked; a buyer has to act). Only a price record that belongs to an active contract in force on the date, for an active supplier, counts as a contract price; a price record without a contract is a price list. Sending an RFQ is the decision type `rfq.send` of the autonomy gate: a proposal for a buyer unless an administrator has set it to automatic.

In the platform `quote_draft` holds what was read from a supplier's email (one per email): the intent (`quote`, `decline`, `question`, `other`), what the model wrote (`reading`), the quote built from it by code (`proposal`), the `problems` that stop it being applied and the `assumptions` a person should know about (a GST rate taken from the item, a GST-inclusive price converted). Status: `pending` (a person confirms it, or the autonomy gate `quote.read` does when it is clean), `needs_person` (could not be read reliably; the quote is entered by hand), `applied` or `dismissed`. Mail from anyone but a supplier invited to that RFQ is never read by a model.

In the platform `rfq_supplier` also keeps `last_reminded_at`: a silent supplier is reminded after `rfq_reminder_after_days` working days, at most `rfq_max_reminders` times (setting, default 2), never after the reply date, and never once it has replied, declined or the kill switch is on. `request.budget_check` keeps the budget test made at submission, one entry per GL account (budget, committed, requested, status ok/over/unchecked, policy): the budget runs from 1 April to the current month, a line with no cost centre is held against the cost centres at its delivery location, and a request over budget follows the policy (`warn` or `block`, from the budget rows or the setting `budget_policy`) and never approves itself while `auto_approve_requires_budget` is on. A contract's `max_value` is a ceiling before tax: sourcing sends a line to quotation when the contract has not enough left, and an order that would pass the ceiling is held for a person (proposal `po.accept_variance`). Required supplier documents: PAN and bank proof for everyone, the GST certificate for regular GST registration, the Udyam certificate for micro, small and medium suppliers; each must be present, verified and not expired (`supplier_document_warn_days` only warns about other documents). A supplier without them is not asked to quote and not used on a contract. Supplier status changes (`draft`, `active`, `blocked`) are proposals (`supplier.set_status`, permission `supplier.activate`), asked for with `supplier.propose`, and write `supplier_status_history`.

In the platform `po` also keeps `sent_to` (the address the order went to), `acknowledged_at` and `ack_note`. An approved order, not held, for an active supplier with good documents is sent by a buyer (`po.send`), or by the worker when the setting `po_send_requires_confirmation` is `false` and the kill switch is off; it then becomes `sent`. The supplier's acknowledgement (`acknowledged`) may carry a promised date, copied to the open lines; a date later than the need-by date by more than `supplier_date_days` is reported. A sent order not acknowledged within `supplier_ack_due_days` working days is overdue.

In the platform a goods receipt (`goods_receipt`, `gr_line`, numbered GRN-<location>-<FY>-<seq>) is posted against a `sent`, `acknowledged` or `part_received` order by a store person who is not the order's buyer, an approver or the requester. Over-delivery is accepted up to `receipt_over_pct` (the category's row in `tolerance`, else the ANY row); what has been accepted plus what waits for inspection counts against it, and rejected goods do not. A rejection needs a reason. An item with `inspection_required` waits (`inspection_status` pending, `qty_accepted` empty) until the inspection decides accepted and rejected quantities (`passed`, or `failed` if anything was rejected). What is accepted raises `po_line.received_qty` (the line becomes `part_received` or `received`; the order follows) and, for stocked items, writes a `stock_movement` of type receipt (`source` platform, `ref_doc` the receipt, quantity in stock units, cost of one stock unit). `accepted_on` is the date the accepted goods were taken in, which starts the MSME payment clock.

In the platform an `invoice` (numbered PI-<FY>-<seq>) is entered by AP from the supplier's invoice, with `invoice_line`, `invoice_charge` and what the match found in `match_result`. `dedupe_key` is the supplier's invoice number without case, spaces or punctuation: the same key for the same supplier in the same financial year is a copy, kept as a `cancelled` invoice with `payment_block` duplicate and `duplicate_of`. A credit note carries positive amounts and `ref_invoice_no`. The match per line: the invoice price may be above the order price by `price_pct` of the price or by `price_abs` on the line (either lets it through), and the quantity may not exceed what was accepted at receipt and not yet invoiced by other booked invoices, plus `qty_pct` (all from `tolerance`, the category's row overriding ANY); within tolerance is paid, outside it is `blocked` with a reason (`price_variance`, `qty_over_received`; `irn_missing`, `gstin_mismatch`, `tax_not_allowed` for the invoice as a whole; `arithmetic_mismatch`). A blocked invoice (`payment_block` match or tax) is released only by another person with `invoice.approve`, through the proposal `invoice.release_block`; AP can match it again, cancel it, or ask for the release. The small-difference limit `small_diff_abs` is not applied, because `price_abs` is the larger of the two. Booking (when an invoice is approved) sets `due_date` (the payment terms' days from their baseline; a micro or small supplier within what was agreed in writing, never over 45 days from acceptance, 15 days without a written agreement) and the tax at source (`tds_entry`, section 194Q on the part of a supplier's year above 50 lakh, `tds_amount` on the invoice). A `payment_run` groups approved, unblocked invoices due within `payment_run_window_days` (default 7), small suppliers first, one `payment` per supplier with `payment_allocation` rows (credit notes as negative rows); the net is the invoice total less tax and credit notes, with late interest to small suppliers (three times the `ref_rbi_bank_rate`, compounded monthly) on top. The run goes to the owner as the proposal `payment_run.approve` (permission `payment.approve`, never the proposer); the bank file is made only for an approved run; when the bank has paid, invoices are `paid`, `budget_actual` rows are written (including freight), order lines received, invoiced and paid in full are closed with their order, and a payment advice is emailed. Advance payments and early-payment discounts are not built yet.

### C3. Purchase orders

**po** · no, po_type enum(standard,service,capex,contract_release), supplier_code, supplier_gstin, buyer_gstin, ship_to_location, buyer, project_code, contract_code, created_at, approved_at, sent_at, payment_terms_code, advance_pct, delivery_terms, freight_terms, terms_version, version int, taxable, cgst, sgst, igst, total, status enum(draft,approved,sent,acknowledged,part_received,received,closed,cancelled), cancel_reason

**po_line** · po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, unit_price, gst_rate, cgst, sgst, igst, need_by, promised_date, received_qty, invoiced_qty, cost_centre_code, gl_code, status enum(open,part_received,received,closed,cancelled)

**po_schedule** · po_no, line_no, delivery_date, qty · *staggered deliveries*

**po_charge** · po_no, type enum(freight,packing_forwarding,installation,other), amount, gst_rate, sac

**po_event** · po_no, type enum(acknowledged,date_change,qty_change,price_change,rejected,amended,cancelled), at, field, old_value, new_value, email_ref, applied_by enum(agent,buyer)

In the platform `quote` is numbered `<rfq>-<supplier>-R<revision>` (a new revision is a negotiation round; only the latest is compared) and `quote_line` holds the unit price, discount, GST rate and delivery days. An `award` row is kept per RFQ line with the net price, the reason and whether it overrode the recommendation (the lowest price among offers that are still valid, within the supplier's minimum order, on time and at the expected GST rate). `po` also has `request_no`, `rfq_no`, the status `submitted` (waiting for approvers), `rule_code`, `rule_version`, `decision_reason`, `hold_reason` and `hold_proposal_id` (a price above the estimate by more than `price_band_pct` holds the order until a person accepts it); `po_line` has `base_qty` (the quantity in the stock unit, so open orders count as stock on the way), `est_price`, `source` (contract or award) and `source_ref`; `po_approval_step` is the approval trail, as `approval_step` is for requests. Prices on a purchase order are the net price after discount, to four decimals; the taxable value of a line is quantity times that price rounded to paise; freight and packing take the highest GST rate among the lines. A rejected order is cancelled and its lines can be ordered again.

### C4. Receiving and stock

**goods_receipt** · no, po_no, location_code, gate_entry_at, received_by, delivery_challan_no, supplier_invoice_ref, eway_bill_no, vehicle_no, inspection_status enum(not_required,pending,passed,failed), accepted_on (date: starts the MSME clock), remarks

**gr_line** · gr_no, po_no, po_line_no, qty_received, qty_accepted, qty_rejected, reject_reason

**service_receipt** · no, po_no, po_line_no, period_from, period_to, description, amount_accepted, accepted_by, accepted_on

**return_to_vendor** · no, gr_no, item_code, qty, reason, debit_note_no, at

**stock_movement** · id, item_code, location_code, date, type enum(receipt,issue,return,adjust,transfer_in,transfer_out), qty, unit_cost, cost_centre_code (issues), ref_doc

In the platform, `stock_movement` also has `source` (`import` for a loaded history, `platform` for movements it records itself; a re-import replaces only `import`). The view `stock_level` gives on hand per item and location: opening balance plus receipts, transfers in and adjustments (signed), less issues, returns to the vendor and transfers out, counting movements from the opening date.

### C5. Invoices, tax and payments

**invoice** · no, type enum(invoice,credit_note,debit_note), supplier_code, supplier_gstin, buyer_gstin, place_of_supply, supplier_invoice_no, invoice_date, fy, irn, ack_no, ack_date, signed_qr_ref, eway_bill_no, reverse_charge, received_at, received_on (date), channel enum(email,upload,portal), taxable, cgst, sgst, igst, cess, round_off, total, ref_invoice_no, due_date, payment_block enum(none,match,manual,duplicate,tax), status enum(received,matched,blocked,approved,part_paid,paid,cancelled), document_ref, document_hash

**invoice_line** · invoice_no, line_no, po_no, po_line_no, description, hsn_sac, qty, uom, unit_price, discount, taxable, gst_rate, cgst, sgst, igst

**invoice_charge** · invoice_no, type, amount, gst_rate, sac

**match_result** · invoice_no, invoice_line_no, result enum(matched,within_tolerance,blocked), reason_codes, price_var, qty_var, resolved_by, resolved_at, resolution

**tds_entry** · invoice_no or payment_no, section, base, rate, amount, booked_on, quarter

**tds_challan** · period, amount, paid_on, bsr_code, challan_no · *TDS register (P2)*

**payment_run** · no, run_date, company_bank_account, proposed_by, approved_by, approved_at, status enum(proposed,approved,file_generated,paid)

**payment** · no, run_no, type enum(invoice,advance), supplier_code, po_no (advances), amount, tds_amount, msme_interest, mode enum(neft,rtgs,imps), reference, utr, paid_on, remittance_email_ref

**payment_allocation** · payment_no, invoice_no, amount · *one payment covers several invoices; credit notes netted*

**advance_adjustment** · advance_payment_no, invoice_no, amount

**bank_statement_line** · account, value_date, amount, utr, reference, narration, matched_payment_no

### C6. Messages and files

**email_message** · id, message_id, in_reply_to, thread_id, mailbox, direction enum(in,out), from, to, cc, subject, body_ref, attachments, at, plus_tag, linked_doc_type, linked_doc_no

**document_file** · id, type enum(quote,invoice,credit_note,certificate,po_pdf,delivery_challan,request_attachment,other), file_path, mime, hash, source enum(email,upload,portal,generated), linked_doc_type, linked_doc_no

### C7. Produced by the running system only (S, not generated)

approval_task, notification, alert, proposal, audit_event, agent_run, model_call, rule_evaluation, case, case_event, job, correction_label, kpi_snapshot. *Listed so the database design accounts for them; the generator does not create them.*

## D. Hidden truth (G, read only by tests)

**truth_item_demand** · item_code, location_code, pattern enum(steady,seasonal,trend,intermittent,lumpy), mean_per_month, cv, season_peak_month, trend_pct_per_year, stockout_periods

**truth_supplier** · supplier_code, profile enum(reliable,average,poor), lead_days_mean, lead_days_sd, on_time_prob, part_delivery_prob, rejection_rate, price_drift_pct_per_quarter, invoice_error_rate, reply_hours_mean, writing_style

**truth_case** · case_id, type, documents, cause, expected_result, planted_on

## E. Answer keys (G, one JSON line per document or case)

quotes.jsonl · invoices.jsonl · credit_notes.jsonl · emails.jsonl · requests.jsonl · mismatch_cases.jsonl · assistant_questions.jsonl · certificates.jsonl · attacks.jsonl

Each line holds: file, layout, hold_out, the true values, and for planted cases the true cause.


---

## Notes on the generated dataset (version 2.1)

Additions and differences between this dictionary and what `datagen/generator` writes:

- **Added tables:** `sanction_entry` (fictional sanctions list, master), `truth_match_expected` (truth: the match result a correct platform must reach for every invoice, with the planted cause), `tds_challan` carries a `section` column (194Q, 194C or 194J), `return_to_vendor`, `po_schedule`, `po_charge`, `invoice_charge`, `advance_adjustment` are generated.
- **Extra columns:** `item` has `gst_rate_before`, `gst_rate_after`, alternative rates, `gst_rate_change_date`, `gst_rate_confidence`; `email_message` has `kind`, `style`, `supplier_code`; `item_location` has `order_qty`; `truth_item_demand` has `seasonal_amplitude`; `payment` has type `advance` for the five 30%-advance suppliers; `tds_entry.payment_no` is filled (and `invoice_no` blank) for TDS deducted on advances.
- **Prospective suppliers:** three suppliers have status `draft` (SUP-0061 to SUP-0063): one matches the sanctions list, one has incomplete documents, one is clean. They have no contracts or orders.
- **Reference tables written to `reference/`:** `gst_state`, `uqc_unit`, `gst_rate_goods`, `gst_rate_services`, `gst_rate_history`, `tds_section`, `rbi_bank_rate`, `compliance_parameter`. The full `pincode_directory` and `hsn_sac_codes` lists stay in `data/reference/`.
- **Not generated:** documents (`document_template`, `terms_template`, `document_file`), `match_result` (platform output) and the tables that only the running platform produces.

## Changes after the data audit (version 2.2)

Two independent audits (transactions, tax and payments; master data and realism) found problems that were cleared in this version:

- **Suppliers:** draft suppliers never transact; composition and unregistered suppliers only supply inside their own state; documents (insurance, licence, GST, Udyam, agreement) have varied dates; `turnover_band` is derived from turnover.
- **Master data:** every item HSN/SAC validates against the code directory; cost centre, GL and budget lines agree with the delivery location; budget plan covers every cost centre and project; discrete items (nos, sets, pairs) are planned and requested in whole numbers; contracts respect their ceiling and dates.
- **Orders:** a PO is never created before its request approval plus two days; contract usage is tracked; ordering is spread across weekdays; if a supplier declines a PO the lines are re-sourced; some suppliers never acknowledge (`po_not_acknowledged`); promised dates agree across the lines of one PO.
- **Tax and money:** TDS thresholds are tracked per section; advances are paid before the first invoice of their PO; invoice and credit note totals round half up; credit notes carry an IRN for e-invoice suppliers; e-way bill on invoices follows the goods receipt.
- **Payments:** each payment run has a two-step approval (finance manager, then CFO); MSME late payments produce a supplier follow-up email; challan numbers are unique.
- **Cut-off:** nothing happens after 31 Oct 2026. POs, RFQs and RFQ invitations dated after it have blank timestamps and a `draft` status instead of a leaked date.
- **Emails:** invoice emails arrive in the `invoices@` mailbox; new kinds `substitution`, `bank_change`, `attack` (instructions hidden in an acknowledgement) and `payment_followup`, each with a truth case.
- **Reproducibility:** one set iteration made output depend on Python's hash seed; fixed. Two runs of the same seed are byte-identical.

Accepted simplifications (not fixed on purpose): MSME interest is recorded but not paid in the bank outflow; TDS on the original invoice is not reduced when a credit note follows; no stock returns or adjustments other than returns to vendor; all services use the base unit "OTH"; "blocked but paid" invoices carry no variance-approval record; six request lines for plant cost centres are delivered to the head office.
