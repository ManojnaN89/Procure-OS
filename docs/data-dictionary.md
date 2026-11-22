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

**location** · code, name, type enum(office,plant,warehouse), gstin →gst_registration, address, city, state_code, pincode, is_stock_location, working_days (e.g. Mon-Sat), receiving_hours, holiday_scope (central + state), head_user →user (the plant or office head; approver role source `location_head`), region_code →region · *ship-to, stock, calendar*

**region** · code, name · *a named group of locations; a location is in at most one (D44). A supplier approval or a contract price limited to a region applies at its locations only*

**department** · code, name, head_user →user

**cost_centre** · code, name, department_code, location_code, owner_user, active

**gl_account** · code, name, type enum(asset,inventory,expense,liability), is_capex, is_tax_account, parent_code

**project** · code, name, type enum(capex,internal), cost_centre_code, budget_amount, start_date, end_date, status enum(open,closed) · *capital spend goes to capital work in progress by project*

**number_series** · document_type, location_code, fy, prefix, next_no · *PO, request, GRN and payment numbering per unit and year*

### B2. People and control

**user** · code, employee_no, name, designation, email, phone, department_code, location_code, manager_code, approval_limit money, notify_by enum(email,in_app,both), active

**user_role** · user_code, role enum(requester,approver,buyer,store,ap,finance,admin,supplier_portal), scope_location, scope_department

**delegation** · from_user, to_user, start_date, end_date, reason

**approval_rule** · rule_code, document enum(request,po,po_amendment,price_variance,payment_run,supplier_activation), min_value, max_value, category_code|ANY, department_code|ANY, spend_type enum(opex,capex,ANY), location_code|ANY, approver_chain (e.g. `manager>dept_head>director`, `+cfo`), version, effective_from · *the most specific row whose chain does not start with `+` gives the chain (a named dimension earlier in the setting `approval_precedence`, default `category>department>location>spend_type`, outweighs all later ones; two equal rules are refused); every fitting `+` row adds its roles at the end*

**budget** · cost_centre_code, gl_code, project_code (optional), period (YYYY-MM), amount, version, policy enum(warn,block), location_code, kind enum(plan,adjustment), reason, proposal_id · *a row is held by a cost centre or, with location_code and no cost centre, by a location as a whole; an adjustment row is written only when another holder of budget.manage approves it*

**budget_period_lock** · period (YYYY-MM), locked_by, locked_at, reason · *a locked month is frozen: no actual is booked in it and no budget row for it is added*

**forward_buy_rule** · category_code, item_code, location_code (with an item only), version, effective_from, effective_to, enabled, min_gap_pct, min_trend_months, max_cover_months, holding_cost_pct_month, min_net_saving_inr, shelf_life_months, storage_max_qty, note · *the company row has every value; a narrower row restates only what it changes and inherits the rest; versions are numbered per scope*

**setting** · key, value · *auto_approve_limit, budget_policy, working Saturday rule, default RFQ response days, reminder intervals, cost_of_capital_pct, default service level, duplicate-check window, autonomy levels*

**document_template** · code, type enum(po_pdf,rfq_email,po_email,reminder,ack_request,invoice_query,payment_advice,approval_request,alert), version, subject, body (with slots) · *templates replace AI-written text (decision D20)*

**terms_template** · code, type enum(po_goods,po_service,po_capex,rfq), version, text · *terms printed on PO and RFQ*

### B3. Catalogue

**category** · code, name, parent_code, kind enum(goods,service), default_gl, stocked, capex, min_quotes, buyer_user, approval_category, spec_template json (required attributes), required_supplier_docs (list), inspection_required, default_tds_section, reverse_charge_default, sac_or_hsn_hint

**item** · code, name, description, category_code, kind, brand, manufacturer, mpn (manufacturer part no.), base_uom, purchase_uom, pack_size, moq, hsn_sac, gst_rate_override, standard_price money, last_price money, attributes json (per spec_template), abc_class enum(A,B,C), stocked, capex, inspection_required, serialised (one serial number per unit on receipt), hazardous, shelf_life_days (healthcare pack only), active

**uom_conversion** · item_code, from_uom, to_uom, factor qty

**item_substitute** · item_code, substitute_code, note · *supplier offers an alternate (AG02), intake suggestions*

**item_location** · item_code, location_code, reorder_policy enum(none,rop,minmax), reorder_point, safety_stock, min_qty, max_qty, service_level_pct, lead_days_override · *planning (P3); netting (P1)*

### B4. Suppliers

**supplier** · code, legal_name, trade_name, legal_form enum(pvt_ltd,public_ltd,llp,partnership,proprietorship), pan, pan_status enum(operative,inoperative), cin_llpin, gst_reg_type enum(regular,composition,unregistered), turnover_band, e_invoice_applicable, hsn_digits enum(4,6), msme_category enum(micro,small,medium,none), udyam_no, msme_agreement bool, agreed_payment_days, payment_terms_code, tds_section_default, lower_tds_rate, lower_tds_valid_to, lower_tds_limit, reverse_charge, status enum(draft,active,blocked,prospect), blocked_reason, website, registered_by (who registered a prospect), invited_by (the buyer who first invited it), identity_checked_by, identity_checked_at, identity_method (one of the setting `identity_check_methods`), identity_document_ids, identity_note · *a prospect (D-03) may be invited and quote; its quote counts only after an identity check by a person other than its registerer or inviter and with no open flag; it is activated by the usual proposal*

**supplier_address** · supplier_code, type enum(registered,dispatch,billing), gstin (a supplier can hold one per state), address, city, state_code, pincode · *the dispatching GSTIN decides IGST vs CGST+SGST*

**supplier_contact** · supplier_code, name, role enum(sales,accounts,dispatch,owner), email, phone, is_primary

**supplier_bank_account** · supplier_code, bank_code, account_no (fictional; lists show the last four digits), ifsc, verified bool, verified_on, submitted_by (who entered it or asked for the change: never its verifier), verification_method (one of the setting `bank_verification_methods`), evidence_document_id (the proof), verified_by, verified_at · *a new or changed account is unverified and never put in a payment run until a second person verifies it*

**supplier_category** · supplier_code, category_code, approved_from · *for a prospect: the category it may be invited for, not an approval*

**supplier_category_scope** · supplier_code, category_code (→supplier_category), location_code or region_code (exactly one) · *no rows: approved everywhere; rows: only at those locations and regions (D44). Changed through the proposal `supplier.approve_category` (permission `supplier.activate`, never the asker)*

**supplier_flag** · supplier_code, kind enum(same_contact,same_address,same_domain,staff_contact,blocked_match,same_quote), other_ref (the other supplier or staff member), detail, raised_at, cleared_by, cleared_at, clear_reason · *a possible conflict for independent review (D-03 R8), never a verdict; an open flag keeps a prospect's quote from counting, and two suppliers an open flag links count as one*

**supplier_document** · supplier_code, doc_type enum(gst_cert,pan,udyam,msme_agreement,iso,licence,insurance,lower_tds_cert,cancelled_cheque), number, issued_on, expires_on, verified, file_ref

**supplier_status_history** · supplier_code, at, from_status, to_status, reason, by_user

**sanction_entry** · name, country, list_name, listed_on · *small fictional list to test screening (P2)*

**payment_terms** · code, name, days, baseline enum(invoice_date,receipt_date,acceptance_date), advance_pct, discount_pct, discount_days

### B5. Prices and rules

**contract** · code, supplier_code, type enum(rate_contract,service_contract,annual_maintenance), valid_from, valid_to, max_value, payment_terms_code, delivery_terms enum(for_destination,ex_works,door_delivery), freight_terms, warranty_months, sla_text, status enum(draft,active,expired,terminated), created_by (never activates it)

**price_record** · supplier_code, item_code, contract_code (optional), uom, price, min_qty (quantity break), price_basis enum(ex_works,for_destination), freight_included, valid_from, valid_to, lead_days, version, status enum(draft,active,withdrawn), created_by, activated_by, activated_at. A contract price is a version (D48): entered as a draft, used only once someone who neither stored the contract nor entered a price of that version activates it (with the contract, or on an active contract through a proposal); from its first day it ends the version in force the day before. Two contract prices of one supplier for the same item, unit and quantity break never apply on the same day in a place both cover. A price may carry one location_code or one region_code (none: everywhere) and is never used outside it (D44)

**tolerance_version** · version, effective_from, effective_to, note, created_by, created_at · *the tolerance set is versioned as a whole (task 3.2); version 1 is the company's set from 2000-01-01; a change makes the next version from a date, ending the one in force the day before*

**tolerance** · version →tolerance_version, key enum(price_pct,price_abs,qty_pct,small_diff_abs,receipt_over_pct,supplier_change_pct,supplier_date_days,supplier_price_pct), value, category_code|ANY · *an invoice is judged under the version in force on its received date, a receipt under its business date, a supplier change under the day the buyer decides; `match_result`, `invoice` and `goods_receipt` keep `tolerance_version`*

**price_threshold_rule** · category_code (empty: the company), version, effective_from, effective_to, gap_pct, gap_inr_per_unit, min_saving_inr, evidence_max_age_days, min_sources, expiry_review_days, note, created_by, created_at · *when a contract price above the market opens a strategic review (D41, task 12.5); a category row may only be stricter; the default (5%, Rs 10 a unit, Rs 25,000 saving, 45 days, 1 source, 60 days before expiry) needs validating with each company (O2)*

**market_benchmark** · item_code, price (per uom, before GST), uom, basis enum(delivered,ex_works), effective_date, region_code (empty: national), source_kind enum(index,price_list,market_survey,other), source_name, source_ref, document_id, note, status enum(unverified,verified,rejected), entered_by, entered_at, verified_by, verified_at, verify_note, rejected_reason, replaces · *evidence only (D52): never an estimate, a route, a quote ranking or an order price. Counts only when verified by someone other than its enterer, no older than the rule's freshness limit, for the region and in a convertible unit. A verified record is never edited: a correction replaces it*

**stock_opening** · item_code, location_code, qty, unit_cost, as_of

## C. Transactions (G for history; S once the platform runs)

### C1. Requests and approvals

**request** · no, requester, on_behalf_of, department_code, ship_to_location, type enum(goods,service,capex), priority enum(normal,urgent,emergency), project_code, created_at, need_by, channel enum(form,email,chat,planning), source_ref (email id), justification, preferred_supplier, status enum(draft,submitted,approved,rejected,returned,cancelled,closed), total_value

**request_line** · request_no, line_no, item_code (blank for free text), free_text, attributes json, qty, uom, est_price, est_source, need_by, delivery_location, cost_centre_code, gl_code, category_code, project_code, status enum(open,sourcing,ordered,cancelled)

`est_price` is the same estimate whoever raised the request (the form, the plain-words intake, replenishment, the store check): the contract price in force for the quantity, else the last price paid (the latest order that is not a draft and not cancelled), else the item's standard price; `est_source` says which, in words (`contract CNT-0001`, `last price paid on PO-...`, `standard price`), and the request page shows it.

In the platform, `request_line` also has `base_qty` (the quantity in the stock unit, so open requests count as stock on the way) and `reason` (why a planning line was raised). A request raised by replenishment has channel `planning` and no requester. So does one raised by the store check for what a material request is short of; its `source_ref` is the material request number (`MR-...`), each line is priced at the estimate (contract price, else last price paid, else standard price), and two such requests for different material requests are never flagged as repeats of each other. `number_counter` holds the last number used per document type, location and financial year; the format of each series is the `number_series` master.

**approval_event** · document, document_no, step, approver, on_behalf_of (delegation), action enum(approved,rejected,returned,delegated,auto_approved,escalated), at, comment, rule_code

In the platform the approval trail is `approval_step` (request_no, round, step, role, approver, delegate, status, acted_by, acted_at, comment, task_id): one row per person who has to act, one round per submission, so a returned request starts a new round. `request` also carries `rule_code`, `rule_version`, `decision_reason`, `submitted_at`, `decided_at` and `approval_round`, so every decision can be repeated against the rule version it used. `approver_role` says how a role named in a chain becomes a person: the requester's manager, the department's head, the head of a named department, or the holder of a designation. Audit rows carry the actions `request.submit`, `request.auto_approve`, `request.approve`, `request.reject` and `request.return`.

Email approvals (plan step 4, D36). Each `approval_step` also keeps the rule row behind it (`rule_id`, `rule_code`, `rule_version`) and `route_reason`, why it came to this person in words; `opened_at` (the step became this person's turn) and `reminded_at`. When a step opens the person gets an email (a template, no model) with the lines, the estimated total, the budget, why, and approve / reject / return links (signed, one step, one person, work once, expire in 7 days; reject and return ask for a reason; audit `request.approval_email`, never the link). One reminder goes after `approval_reminder_days` working days (default 1, audit `request.remind` by `approval-reminder`); after `approval_escalation_days` (default 2) the task moves to the approver's manager, else the next approver, never the requester, who may then decide too (`request.escalate`) and gets the email. Both count working hours on the business calendar with holidays. Every decision from a link (approve, reject, return) needs a one-time code emailed to the login the link names, at any value (D-07, block 4); the page and the email also link to the request in the app, where the approver signs in instead (`approval_otp_above`, default 500000.00, now governs only the order approval code): `one_time_code` (purpose, ref, user_id, code_hash (HMAC, never the code), created_at, expires_at (10 minutes), attempts (5 at most), status open/used/replaced/expired/locked, used_at), audited as `otp.issue`, `otp.wrong`, `otp.expired`, `otp.use`. An approver names a deputy for a date range (`delegation`, `delegation.create`/`delegation.remove`); a step that opens while the approver is away goes to the deputy, who must be able to approve and never decides their own request.

A request is taken back and ended by its requester, not only by an approver. `request.withdraw` puts a request waiting for approval back to `draft` (its open steps and the approvers' tasks are cancelled, the next submission starts a new round); `request.cancel` ends a draft, returned, waiting or approved request with a reason, as long as the buyer has not started buying it (`sourcing_decision` or a line in `sourcing`/`ordered`), and its open lines become `cancelled`; `request.copy` makes a new draft in the copier's name with the same lines (a cancelled request is copied whole, a live one without its struck-out lines), the date needed moved to a week from today unless one is given, and `source_ref` `copy:<number>`. Only the requester may withdraw or cancel (permission `request.cancel_own`); a restock request nobody raised is cancelled by a buyer (`replenishment.run`). The requester is told, in the app and by email, when the request is approved (also when it approves itself), rejected or returned, and an approver whose step was open is told when it is withdrawn or cancelled; an automatic restock request has no requester and tells nobody.

### C2. Sourcing

**rfq** · no, buyer, created_at, due_date, type enum(commercial,combined), weights json, delivery_terms, payment_terms_proposed, terms_version, status enum(draft,sent,closed,awarded,cancelled), sent_at, delivery_location (one RFQ per category and delivery location, D44), min_quotes and min_quotes_rule (kept when the RFQ is made, e.g. `category RAW`, `setting min_quotes_default` or a buying route version; every reader uses them, D-03 R12)

**rfq_line** · rfq_no, line_no, request_no, request_line_no, item_code, description, attributes json, qty, uom, need_by

**rfq_supplier** · rfq_no, supplier_code, invited_at, reminders_sent int, responded_at, declined bool, decline_reason

**quote** · no, rfq_no, supplier_code, revision int (negotiation round), received_at, valid_until, payment_terms_code, delivery_terms, packing_forwarding money, freight money, taxes_extra bool, warranty_months, deviations, document_ref

**quote_line** · quote_no, rfq_line_no, item_code, offered_brand, qty, moq, unit_price, discount_pct, gst_rate, delivery_days, deviation

**award** · rfq_no, rfq_line_no, supplier_code, quote_no, qty, price, reason, overridden bool, by_user, at, split bool, selection_rule, conditional bool (a checked prospect: no order until it is active), lapsed_at, lapse_reason (not activated within `prospect_qualification_days` working days, or its activation rejected; nothing is awarded to anyone else automatically), awarded_on (the business date of the award: the deadline counts from it)

Supplier selection (plan step 6, P1-34). A quote is dropped when it fails a must-have, each with its reason: delivered after the need-by date (on the date is on time), expired (valid until today still counts), a minimum order above the quantity, a GST rate other than the item's, a supplier not `active` or not approved for the RFQ's category on the date (`supplier_category.approved_from`), or a GST-registered supplier (`regular`, `composition`) with no GSTIN. The rest are ranked on total cost: quantity times the net price, plus the line's share of the quote's freight and packing, plus GST only when the input credit cannot be claimed (the rule claims it and the delivery place, or the default registration, has a GSTIN); a composition or unregistered supplier charges no GST. `quote_line.qty` is what the supplier offers, less than asked when it can supply only part. How quotes are compared is a row of `supplier_selection_rule` (version, effective_from, effective_to, `method` `total_cost` or `weighted`, `weight_price`, `weight_delivery`, `weight_quality`, `weight_payment_terms`, `tie_break` e.g. `delivery,vendor_score`, `input_credit`; plan default v1: total cost, 40/30/20/10, claim the credit); a change adds the next version and closes the old one (audit `supplier_selection.change`). The weighted score is price (lowest cost over this cost), delivery and quality (the vendor score's parts) and payment terms (the quote's or the supplier's days over the longest on offer). Equal results go by the tie-break, then the supplier code. A line is split between suppliers only when the best split (every group of two or more offers, cheapest per unit first, within what each offers) saves more on the goods than it adds in freight, or when no one quote covers the quantity. A selection needs a written reason when it is not the recommended, cheapest qualifying offer (or the recommended split), or when the line has fewer qualifying quotes than `rfq.min_quotes`. Each selected supplier is one `award` row with its part (`split` true when shared) and the rule version; the audit row `rfq.award` carries the ranking, the dropped reasons and the reason in words. Orders follow one per supplier; a shared line is one order line per quote (`po_line_request_line` is unique per request line and `source_ref`). The printable RFQ is a PDF per supplier asked (`/api/rfqs/{no}/suppliers/{code}/pdf`).

In the platform an `rfq` is made for one approved request and one category (`request_no`, `category_code`), with `proposal_id` while sending waits for a buyer, and `note` when fewer suppliers are approved than the category asks for. `rfq_supplier` also keeps the address used (`email`) and `send_error`. `sourcing_decision` holds, for each request line, how it is bought: `contract` (supplier, contract, price, the reason in words), `rfq` (the RFQ number) or `manual` (nobody could be asked; a buyer has to act). Only a price record that belongs to an active contract in force on the date, for an active supplier, counts as a contract price; a price record without a contract is a price list. Sending an RFQ is the decision type `rfq.send` of the autonomy gate: a proposal for a buyer unless an administrator has set it to automatic.

The buying route (D37) picks, per line, the first that applies: `contract` (a contract price in force with money left under the ceiling), `single_source` (the item is in `item_single_source`: item_code, supplier_code, reason), `emergency` (the request's `priority` is `emergency`), `reorder` (the item's last approved order is at most `reorder_within_days` old, from a supplier still usable and on time at least `reorder_on_time_pct` of its judged receipts, the value under `reorder_below`, and the price on file within `reorder_price_pct` of the last price), else `rfq`. The three direct routes are `sourcing_decision.method` `direct` and become order lines with `po_line.source` `direct` (`source_ref` the route); single source above `agree_above` and every emergency set `needs_agreement` and wait for the proposal `sourcing.agree_direct` (permission `sourcing.route_approve`, the Approver role) in `proposal_id`. A quotation asks for 1 quote under `two_quotes_from`, 2 up to and including `three_quotes_above`, 3 above (never fewer than the category's `min_quotes` or `min_quotes_default`), kept in `rfq.min_quotes` and used by the comparison. The value is counted per item over `window_days`: the line (contract or single-source price, else the estimate, else the item's standard price, else the last supplier's price) plus the `line_value` of the item's other decisions with `decided_on` in the window, cancelled lines left out. The thresholds are rows of `buying_route_rule` (version, effective_from, effective_to; no category is the company's rule, plan defaults 25,000 / 90 days / 80% / 25,000 / 2% / 25,000 / 2,00,000 / 30 days): a change adds the next version and closes the old one (audit `buying_route.change`), and a category row only names thresholds it makes stricter (looser values are ignored). Each decision keeps `route`, `route_rule` (e.g. `buying_route v1 + RAW v2`), `checks` (every test with passed and its detail), `line_value`, `window_value`, `min_quotes` and `decided_on`. A buyer may send a line not yet ordered to quotation at once (`sourcing.route_change`); buying it directly instead (a named supplier and price) is the proposal `sourcing.loosen_route` for the purchase manager, refused once its RFQ has gone out.

In the platform `quote_draft` holds what was read from a supplier's email (one per email): the intent (`quote`, `decline`, `question`, `other`), what the model wrote (`reading`), the quote built from it by code (`proposal`), the `problems` that stop it being applied and the `assumptions` a person should know about (a GST rate taken from the item, a GST-inclusive price converted). Status: `pending` (a person confirms it, or the autonomy gate `quote.read` does when it is clean), `needs_person` (could not be read reliably; the quote is entered by hand), `applied` or `dismissed`. Mail from anyone but a supplier invited to that RFQ is never read by a model.

In the platform `rfq_supplier` also keeps `last_reminded_at`: a silent supplier is reminded after `rfq_reminder_after_days` working days, at most `rfq_max_reminders` times (setting, default 2), never after the reply date, and never once it has replied, declined or the kill switch is on. `request.budget_check` keeps the latest budget test (made at submission and again at each approval step), one entry per GL account and budget holder (`level` cost_centre, department, cost_centres or location; budget, committed, requested, `used_pct`, status ok/warn/over/unchecked, the thresholds used, policy, the months counted): the budget runs from 1 April to the current month; a line with no cost centre is held against the cost centres of the request's department at its delivery location (all the cost centres there when the department has none), and every line is also held against its location's own budget when it has one; a check warns at `budget_warn_pct` used (default 90) and is over above `budget_block_pct` (default 100); a request over budget follows the policy (`warn` or `block`, from the budget rows or the setting `budget_policy`) and never approves itself while `auto_approve_requires_budget` is on. Planned request lines (material request shortfalls, restocks) for the same item and delivery location needed within `combine_window_days` (default 7) are bought on one line while its request is a draft; `material_request_buy` keeps each material request line's own quantity and `request_line.stock_qty` what the line buys for stock, the line's quantity is worked out from them, cancelling a material request takes its part off a shared line, and replenishment counts only the stock part as already requested. A contract's `max_value` is a ceiling before tax: sourcing sends a line to quotation when the contract has not enough left, and an order that would pass the ceiling is held for a person (proposal `po.accept_variance`). Required supplier documents: PAN and bank proof for everyone, the GST certificate for regular GST registration, the Udyam certificate for micro, small and medium suppliers; each must be present, verified and not expired (`supplier_document_warn_days` only warns about other documents). A supplier without them is not asked to quote and not used on a contract. Supplier status changes (`draft`, `active`, `blocked`) are proposals (`supplier.set_status`, permission `supplier.activate`), asked for with `supplier.propose`, and write `supplier_status_history`.

In the platform `po` also keeps `sent_to` (the address the order went to), `acknowledged_at` and `ack_note`. An approved order, not held, for an active supplier with good documents is sent by a buyer (`po.send`), or by the worker when the setting `po_send_requires_confirmation` is `false` and the kill switch is off; it then becomes `sent`. The supplier's acknowledgement (`acknowledged`) may carry a promised date, copied to the open lines; a date later than the need-by date by more than `supplier_date_days` is reported. A sent order not acknowledged within `supplier_ack_due_days` working days is overdue.

Supplier confirmation (plan step 8). The order email carries a signed link (`supplier_link`: one order, one supplier, purpose `respond` or `dispatch`, the order version, expiry, `used_at`; it works once and is refused for another supplier's order or an order that has changed since) to a page with no login where the supplier accepts (the acknowledgement), declines or asks for a change. A reply by email is read by the supplier agent. Every change is a `po_change` row (kind `date`, `qty`, `price` or `decline`, `old_value` and `new_value`, `source` link or email, status `proposed`, `waiting_approval`, `accepted`, `rejected` or `withdrawn`, the buyer's `task_id`, the `version_made`): the buyer accepts or rejects it, and an accepted date, quantity or price makes a new order version (`po_revision`) and tells the requester. A price rise above `supplier_price_pct` (tolerance, default 2%) is accepted only by the person the `price_variance` rule names (proposal `po.supplier_price`); an accepted decline cancels the order and marks its lines `manual` for the buyer to source again. A `dispatch_note` (date shipped, expected arrival, vehicle, LR/AWB number, the supplier's invoice number; `dispatch_note_line` the quantity per order line, never more than is still to ship) is shown to the store as on the way; it receives nothing. `delivery_reminder` records each reminder about a date: one `delivery_reminder_before_days` working days before it (default 2), and, while nothing of it has arrived, `delivery_overdue_after_days` after it (default 1) then every `delivery_overdue_every_days` (default 3), at most `delivery_overdue_max` (default 3); each is sent and recorded once per order and date.

In the platform a goods receipt (`goods_receipt`, `gr_line`, numbered GRN-<location>-<FY>-<seq>) is posted against a `sent`, `acknowledged` or `part_received` order by a store person who is not the order's buyer, an approver or the requester. Over-delivery is accepted up to `receipt_over_pct` (the category's row in `tolerance`, else the ANY row); what has been accepted plus what waits for inspection counts against it, and rejected goods do not. A rejection needs a reason. An item with `inspection_required` waits (`inspection_status` pending, `qty_accepted` empty) until the inspection decides accepted and rejected quantities (`passed`, or `failed` if anything was rejected). What is accepted raises `po_line.received_qty` (the line becomes `part_received` or `received`; the order follows) and, for stocked items, writes a `stock_movement` of type receipt (`source` platform, `ref_doc` the receipt, quantity in stock units, cost of one stock unit). `accepted_on` is the date the accepted goods were taken in, which starts the MSME payment clock.

Stock also moves from the store's own screen. An issue (`stock.issue`, audit `stock.issue`) takes stock out for use and may not take more than is on the shelf; a count (`stock.adjust`, audit `stock.adjust`, with a reason) makes the books say what was counted: the server works out the difference (`formulas/stock.count_difference`) and writes one `adjust` movement of that size (nothing when the books already agree). Both write `stock_movement` rows with `source` platform, an id `SM-ISS-` or `SM-ADJ-` plus the date and a random suffix, and the last known unit cost; the Store role holds both permissions. Any list can be exported: `GET /api/export/<requests|quotations|orders|receipts|invoices|payment-runs|vendors|contracts|stock>` returns the rows the screen shows as a UTF-8 CSV file with a byte-order mark (cells a spreadsheet would run as a formula are prefixed with an apostrophe) and writes an `export.list` audit row. The master data import reads an Excel file (first sheet, first row the column names) wherever it reads a CSV file of the same name, and the two can be mixed in one folder; a dry run reports exactly what a real run would load.

In the platform an `invoice` (numbered PI-<FY>-<seq>) is entered by AP from the supplier's invoice, with `invoice_line`, `invoice_charge` and what the match found in `match_result`. `dedupe_key` is the supplier's invoice number without case, spaces or punctuation: the same key for the same supplier in the same financial year is a copy, kept as a `cancelled` invoice with `payment_block` duplicate and `duplicate_of`. A credit note carries positive amounts and `ref_invoice_no`. The match per line: the invoice price may be above the order price by `price_pct` of the price or by `price_abs` on the line (either lets it through), and the quantity may not exceed what was accepted at receipt and not yet invoiced by other booked invoices, plus `qty_pct` (all from `tolerance`, the category's row overriding ANY); within tolerance is paid, outside it is `blocked` with a reason (`price_variance`, `qty_over_received`; `irn_missing`, `gstin_mismatch`, `tax_not_allowed` for the invoice as a whole; `arithmetic_mismatch`). A blocked invoice (`payment_block` match or tax) is released only by another person with `invoice.approve`, through the proposal `invoice.release_block`; AP can match it again, cancel it, or ask for the release. The small-difference limit `small_diff_abs` is not applied, because `price_abs` is the larger of the two. Booking (when an invoice is approved) sets `due_date` (the payment terms' days from their baseline; a micro or small supplier within what was agreed in writing, never over 45 days from acceptance, 15 days without a written agreement) and the tax at source (`tds_entry`, section 194Q on the part of a supplier's year above 50 lakh, `tds_amount` on the invoice). A `payment_run` groups approved, unblocked invoices due within `payment_run_window_days` (default 7), small suppliers first, one `payment` per supplier with `payment_allocation` rows (credit notes as negative rows); the net is the invoice total less tax and credit notes, with late interest to small suppliers (three times the `ref_rbi_bank_rate`, compounded monthly) on top. The run goes to the owner as the proposal `payment_run.approve` (permission `payment.approve`, never the proposer); the bank file is made only for an approved run; when the bank has paid, invoices are `paid`, `budget_actual` rows are written for the lines that are not stocked items and for freight (stocked lines were spent at receipt, D-10), order lines received, invoiced and paid in full are closed with their order, and a payment advice is emailed. Advance payments and early-payment discounts are not built yet.

In the platform `invoice_draft` holds an invoice read from a document (a mail to the invoice mailbox, or an upload): one per file (`document_hash`, so the same file twice is one draft). `path` says how it was read (`qr`: the e-invoice QR code and the page's rows by code, no model; `model`: the AI step `invoice_reading`, every value found in the text; `none`: not read), `qr` keeps what the code said and whether its IRN recomputes, `proposal` is the invoice built from it by code (lines mapped to order lines), `problems` stop it being entered and `assumptions` are what a person should know. Status: `pending` (waiting for AP), `needs_person` (nothing reliable was read, or something is wrong), `applied` (`invoice_no`, entered by AP or, only when the automation `invoice.read` is automatic and nothing is in doubt, by the platform) or `dismissed`. Mail from anyone who is not a known supplier (by contact address or the GSTIN on the page) is never shown to a model.

### C3. Purchase orders

**po** · no, po_type enum(standard,service,capex,contract_release), supplier_code, supplier_gstin, buyer_gstin, ship_to_location, buyer, project_code, contract_code, created_at, approved_at, sent_at, payment_terms_code, advance_pct, delivery_terms, freight_terms, terms_version, version int, taxable, cgst, sgst, igst, total, status enum(draft,approved,sent,acknowledged,part_received,received,closed,cancelled), cancel_reason

**po_line** · po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, unit_price, gst_rate, cgst, sgst, igst, need_by, promised_date, received_qty, invoiced_qty, cost_centre_code, gl_code, price_record_id (the contract price version a contract line was bought at), status enum(open,part_received,received,closed,cancelled)

**po_schedule** · po_no, line_no, delivery_date, qty · *staggered deliveries*

**po_charge** · po_no, type enum(freight,packing_forwarding,installation,other), amount, gst_rate, sac

**po_event** · po_no, type enum(acknowledged,date_change,qty_change,price_change,rejected,amended,cancelled), at, field, old_value, new_value, email_ref, applied_by enum(agent,buyer)

In the platform `quote` is numbered `<rfq>-<supplier>-R<revision>` (a new revision is a negotiation round; only the latest is compared) and `quote_line` holds the unit price, discount, GST rate and delivery days. An `award` row is kept per RFQ line with the net price, the reason and whether it overrode the recommendation (the lowest price among offers that are still valid, within the supplier's minimum order, on time and at the expected GST rate). `po` also has `request_no`, `rfq_no`, the status `submitted` (waiting for approvers), `rule_code`, `rule_version`, `decision_reason`, `hold_reason` and `hold_proposal_id` (a price above the estimate by more than `price_band_pct` holds the order until a person accepts it); `po_line` has `base_qty` (the quantity in the stock unit, so open orders count as stock on the way), `est_price`, `source` (contract or award) and `source_ref`; `po_approval_step` is the approval trail, as `approval_step` is for requests. Prices on a purchase order are the net price after discount, to four decimals; the taxable value of a line is quantity times that price rounded to paise; freight and packing take the highest GST rate among the lines. A rejected order is cancelled and its lines can be ordered again. Lines of one request are ordered together per supplier and per set of payment and delivery terms (a contract's terms, else the quote's, else the supplier's own), so a contract line and a quoted line of the same supplier become two orders when their terms differ. `po.version` starts at 1 and goes up with each revision (a change of quantity or need-by date of open lines, never of a price); `po_revision` keeps one row per version (who, why, what changed, the total before and after, whether it went for approval again (`reapproval`), the approved version it was measured from (`baseline_version`) and its material reasons (`material`, a list of plain sentences)). `po_line.short_closed_qty` is the part of a line closed as not coming, and `po.closed_reason` and `po.closed_at` say why and when the balance was closed. `po.budget_accepted` marks an order over budget at its final price that a person accepted, so it is not held again.

PO tiers and touchless orders (plan step 7, P1-35, D35, D36). The tier is the `po` rows of `approval_rule` by the order's total: an automatic band (`auto`) is the touchless band, where only a touchless order is approved, by the rule; any other order there goes up to the next band's chain. Above it the chain's roles approve (Apex: touchless up to 1,00,000, purchase manager to 5,00,000, director above; both ends of a band are inside it). The people who made an order (`po.buyer` and whoever submitted it, `orders/workflow.makers`) never approve it at any value, nor stand in as a deputy, nor accept its price variance: the chain skips them and climbs a band if nobody is left (v1.1, C1). `po.approval_tier` keeps `buyer` for the touchless band or the chain's last role. An order is one per supplier, set of terms and delivery location: a line's place is `request_line.delivery_location`, else the request's `ship_to_location`, and the place decides the buyer GSTIN, the place of supply and the receipt, so two places are two orders (printed on the order PDF and in the email with the address). A touchless order is issued and sent with no buyer when every test passes: every line `contract` or `direct`/`reorder` (single source, emergency and quotation lines never), the order in the touchless band, its total within the touchless limit, the supplier `active`, no contract on it over its ceiling (reaching it exactly is fine), each price at most `price_pct` above the requester's estimate (else the item's standard price; no estimate fails), the budget check on final prices not over, and the autonomy gate `po.issue` set to automatic (kill switch off). The limit and tolerance are rows of `touchless_rule` (version, effective_from, effective_to, `value_limit` empty for the top of the touchless band, `price_pct`; plan default v1: the touchless band, 2%); a change adds the next version (audit `touchless_rule.change`). `po.touchless`, `touchless_checks` (every test with passed and its detail), `touchless_note` (why it was or was not touchless) and `touchless_rule` (the version) are written when the order is submitted, with the audit row `po.touchless_check` (actor the rule, `rule_version` e.g. `touchless_po v1; PO-01:v1`); the send is `po.send` by the same rule actor, and `autonomy_outcome` records it. An order that is not touchless goes to its approvers, never the buyer; one that could not be sent is marked not touchless with the reason. Approving an order whose total is above `approval_otp_above` needs a one-time code (`one_time_code` purpose `po_approval`, ref `<po>:<version>:<round>:<step>`) emailed to the person whose turn it is; rejecting and approval by the rule need none.

### C4. Receiving and stock

**goods_receipt** · no, po_no, location_code, gate_entry_at, received_by, delivery_challan_no, supplier_invoice_ref, eway_bill_no, vehicle_no, inspection_status enum(not_required,pending,passed,failed), accepted_on (date: starts the MSME clock), remarks

**gr_line** · gr_no, po_no, po_line_no, qty_received, qty_accepted, qty_rejected, reject_reason

**service_receipt** · no, po_no, po_line_no, period_from, period_to, description, amount_accepted, accepted_by, accepted_on

**return_to_vendor** · no, gr_no, item_code, qty, reason, debit_note_no, at

**stock_movement** · id, item_code, location_code, date, type enum(receipt,issue,return,adjust,transfer_in,transfer_out), qty, unit_cost, cost_centre_code (issues), ref_doc

Service entry, receipt without an order and serial numbers (plan step 9, P1-37). In the platform the dataset's `service_receipt` is `service_entry` (numbered SRN-<location>-<FY>-<seq>): the confirmation by the person who asked for a service (`request.requester` or `on_behalf_of`) that it was done, fully or in part, on a `sent`, `acknowledged` or `part_received` order. A line is a service when its item's `kind` is `service`, or (no kind, or a free-text line) its category's. `basis` says how it was given (`qty`; `pct` of the ordered quantity; `amount` for a lump sum) and `value` what was typed; `qty_accepted` is what it counts in the line's unit (amount / price, half up to four places) and `amount_accepted` its value at the order price; `via` is `app` or `emailed link`, `accepted_by` the person's code, `period_from`/`period_to` the period it covers. It raises `po_line.received_qty` as a goods receipt does (never stock), so the invoice is matched against the order and the confirmations (a two-way match): a service line invoiced beyond what was confirmed is blocked `service_not_confirmed` (payment block `match`) and matched again when a confirmation follows. More than the order plus `receipt_over_pct` is refused. The buyer and the order's approvers may not confirm; the requester or confirmer of a line may not release its invoice's block. The store cannot post a goods receipt on a service line. The emailed link (`/confirm-service/<token>`) is signed like the approval links, names one person and one line, expires after 7 days, and stops working once the line has another confirmation.

**unordered_receipt** (platform) · no (URN-<location>-<FY>-<seq>), supplier_code, item_code, qty, uom, location_code, reason, challan, serials (json list), received_by, posted_by, received_on, status enum(pending, approved, rejected, ordered, received, returned), proposal_id, decided_by, decided_at, decision_note, po_no, gr_no, returned_on, return_ref. Goods that came without an order, taken in by the store as a flagged exception. They stay out of stock (no quarantine location: the record holds them) while the purchase manager decides through the proposal `receipt.unordered` (permission `grn.unordered_approve`). Approved, the buyer raises the order after the fact: a `request` with channel `unordered_receipt` (source_ref the receipt) and an order from it (`po_line.source` `direct`, `source_ref` the receipt), approved like any order; once it is sent the store posts the goods receipt on it (`gr_no`) and the goods enter stock. Rejected goods go back and the store records the return. A proposal rejected elsewhere counts as rejected.

**serial_number** (platform) · item_code, serial (unique per item, kept in capitals), gr_no, po_no, po_line_no. An item with `serialised` true needs one serial per unit received, in whole units; a serial already on file for the item is refused.

In the platform, `stock_movement` also has `source` (`import` for a loaded history, `platform` for movements it records itself; a re-import replaces only `import`). The view `stock_level` gives on hand per item and location: opening balance plus receipts, transfers in and adjustments (signed), less issues, returns to the vendor and transfers out, counting movements from the opening date.

Stock quality (task 4.1). Every `stock_movement` has a `quality` (usable, quarantine, blocked; type `requalify` moves stock between qualities as a pair of signed rows with one `ref_doc`: the receipt for an inspection, `HOLD-<id>` otherwise; the plus row on usable carries the cost). `stock_level.on_hand` is usable stock only, so held stock is never set aside, issued or counted; the view `stock_quality_level` (item, location, quality, qty) shows every quality. Goods that need inspection go into stock as quarantine at posting (`SM-<gr>-<line>-Q`; `po_line.received_qty` is unchanged until the inspection), then the inspection moves the accepted part to usable and the rejected part to blocked. A dataset's `history/stock_held.csv` (`id, item_code, location_code, date, quality, qty, reason, reference`) is loaded by `import stock` after the movements: a receipt of that quality and one hold each (`stock_hold.import_id` is the row's id).

Safety stock (C4): free stock for setting aside and for an issue by hand is usable less set aside less `item_location.safety_stock`. Using it takes the proposal `stock.safety_override` (asker, reason; kind `issue` or `material`), approved by someone in the asker's manager chain (`org_user.manager_code` upwards) who holds `request.approve`, never the asker. Planning values of an item at a place (safety stock, reorder policy, point, maximum) change through `POST /api/setup/item-planning`: a `setup_change` of area `item_planning` that a second administrator approves.

Time-phased cover (rule Q17): supply bought for a material request line counts for its need only when due by its latest arrival (need-by less `receiving_buffer_days` working days on the location's calendar). The supply date is the order line's promised date, else its need-by; a request line not yet ordered (open or on an RFQ) is dated by its need-by; a date passed with the goods not in counts as today. Of what a line lacks, what only late supply covers is at risk (free stock goes to it before a later need; it is never bought again), the rest short (bought). Settings: `receiving_buffer_days` (0), `late_supply_case_days` (2), `auto_store_check` (true: the store check job at 05:30 IST), `store_check_submit` (false: requests made by the job or after an event wait as drafts).

### C5. Invoices, tax and payments

**invoice** · no, type enum(invoice,credit_note,debit_note), supplier_code, supplier_gstin, buyer_gstin, place_of_supply, supplier_invoice_no, invoice_date, fy, irn, ack_no, ack_date, signed_qr_ref, eway_bill_no, reverse_charge, received_at, received_on (date), channel enum(email,upload,portal), taxable, cgst, sgst, igst, cess, round_off, total, ref_invoice_no, due_date, payment_block enum(none,match,manual,duplicate,tax), status enum(received,matched,blocked,approved,part_paid,paid,cancelled), document_ref, document_hash

**invoice_line** · invoice_no, line_no, po_no, po_line_no, description, hsn_sac, qty, uom, unit_price, discount, taxable, gst_rate, cgst, sgst, igst

**invoice_charge** · invoice_no, type, amount, gst_rate, sac

**match_result** · invoice_no, invoice_line_no, result enum(matched,within_tolerance,blocked), reason_codes, price_var, qty_var, resolved_by, resolved_at, resolution, tolerance_version (the version it was judged under; the invoice keeps the version of its latest match and a re-match uses it)

**tds_entry** · invoice_no or payment_no, section, base, rate, amount, booked_on, quarter

**tds_challan** · period, amount, paid_on, bsr_code, challan_no · *TDS register (P2)*

**payment_run** · no, run_date, company_bank_account, proposed_by, approved_by, approved_at, status enum(proposed,approved,file_generated,paid)

**payment** · no, run_no, type enum(invoice,advance), supplier_code, po_no (advances), amount, tds_amount, msme_interest, mode enum(neft,rtgs,imps), reference, utr, paid_on, remittance_email_ref

**payment_allocation** · payment_no, invoice_no, amount · *one payment covers several invoices; credit notes netted*

**advance_adjustment** · advance_payment_no, invoice_no, amount, taxable *(taxable is platform-only: the part of the advance's taxable value this invoice takes off)*

**advance** *(platform only)* · payment_no, po_no, gross, taxable, used_gross, used_taxable, paid_on · *one row per advance payment: the advance is `advance_pct` of the order's total and of its taxable value; `used_*` grows as invoices of the order take it off*

Platform additions to the tables above: `invoice.advance_used`, `invoice.advance_taxable_used` (what the invoice took off from advances; tax at source is worked out on taxable less `advance_taxable_used`, because the advance already carried its own), `invoice.discount_taken` (the early-payment discount earned when it was paid); `payment_allocation.discount` (the discount in that payment: amount is total less tax at source and advance, the cash paid is amount less discount). The discount is `payment_terms.discount_pct` of the taxable value still to pay, for payment on or before the last day of `discount_days` from the baseline; tax and tax at source stay as invoiced.

**bank_statement_line** · account, value_date, amount, utr, reference, narration, matched_payment_no

**vendor_statement_line** · supplier_code, statement_date, supplier_invoice_no, invoice_date, amount, our_invoice_no (empty: an invoice the supplier says it sent that we never received) · *the supplier's side of a statement of account*

Reconciliation in the platform (plan step 13, P1-39): received not invoiced is worked out when asked, per order line, from goods accepted (at the order price, on `goods_receipt.accepted_on`) and service entries (`amount_accepted` on `accepted_on`) against invoice lines (`taxable`, on `invoice.received_on`; credit notes take off, cancelled invoices count nothing). Small suppliers are checked against the MSMED Act deadline: the days agreed in writing (`supplier.agreed_payment_days`, at most the setting `msme_max_days`, 45) or, without an agreement, `msme_no_agreement_days` (15), counted from the latest acceptance before the invoice came; `msme_warn_days` (7) lists those reaching it soon. The platform's statement tables are in C8.

### C6. Messages and files

**email_message** · id, message_id, in_reply_to, thread_id, mailbox, direction enum(in,out), from, to, cc, subject, body_ref, attachments, at, plus_tag, linked_doc_type, linked_doc_no

**document_file** · id, type enum(quote,invoice,credit_note,certificate,po_pdf,delivery_challan,request_attachment,other), file_path, mime, hash, source enum(email,upload,portal,generated), linked_doc_type, linked_doc_no

### C7. Produced by the running system only (S, not generated)

task, notification, alert, proposal, audit_event, agent_run, agent_step, model_call, rule_evaluation, purchase_case, case_event, job (control database). *Listed so the database design accounts for them; the generator does not create them.* (An earlier version of this list named `approval_task`, `case`, `correction_label` and `kpi_snapshot`; those were never built under those names: approvals are `approval_step` and `po_approval_step`, cases are `purchase_case`, and the key figures are worked out when asked.)

**The complete list of tables and columns as the migrations make them is `database-schema.md`** (generated, and a test keeps it current). This dictionary explains what the data means; the tables below were not described anywhere else.

### C8. Platform tables, by their database names

| Table | What it holds |
|---|---|
| org_user, org_user_role | The people of the organisation (code, department, location, manager, approval limit) and the roles they hold, with the location or department each role is limited to. The generator's `user` and `user_role` become these |
| user_profile, user_limit | A login's display name and role, and its approval limits by document, category and location |
| role_permission | Which action each role may take (the permission table behind every screen and endpoint) |
| rule_table | Versioned decision tables (JDM) for approval rules: key, version, status (draft or published), who and when |
| setup_change | The history of company-setup changes: who proposed it (`by_user`), who approved it (`approved_by`, empty for a change that applies at once), area, key, old and new value, and the reason. Insert only, and hash-chained like `audit_event` |
| audit_event (protection) | Insert only (triggers refuse update, delete and truncate). `prev_hash` and `row_hash` chain every row to the one before it (SHA-256 of the previous hash and the row); `audit.verify_chain` names the first row that was changed or taken out. Same for `setup_change` (migration 0059, D-25) |
| model_call.masked | What the gateway masked before the call, as counts per kind (`bank_account`, `ifsc`, `pan`, `aadhaar`); empty when nothing was masked. The values themselves are never logged or sent (D-25) |
| app_setting, org_setting | Settings. `org_setting` holds the organisation's policy values (tolerances, budget policy, reminder days, mailboxes); `app_setting` holds system switches |
| autonomy_setting, autonomy_global, autonomy_outcome | Per decision type: level, value cap, minimum confidence, tolerated wrong rate; the global kill switch; and each automatic decision's outcome for tracking mistakes |
| agent_step | One step (a tool call or a thought) inside an agent run |
| model_cache | Model answers kept by request hash so the same input is not paid for twice |
| intake_pending | A plain-words request waiting for an answer to one question |
| request_check | A repeat-request or similarity flag on a request, and who overrode it and why |
| invoice_diagnosis | The explanation and fix the exceptions agent worked out for a blocked invoice |
| purchase_case | The case that follows one purchase through its stages (state, title, data) |
| item_category | Item categories: parent, default account, whether stocked or capex, minimum quotes, buyer, required documents, inspection, default TDS section |
| ref_gst_state, ref_gst_rate, ref_hsn_sac, ref_uom, ref_holiday, ref_tds_section, ref_load | Reference data loaded into each company's database: states, GST rates with dates, HSN and SAC codes, units, holidays, TDS sections (rate, no-PAN rate, thresholds, validity) and a record of each load. Rates are data (rule 8) |
| production_order | A production order from the ERP (`order_no` is the ERP's number): product, quantity, need-by date, reason (sales order or plan), location (the company's main stock location, the first by code, when the file names none), status (`open` or `cancelled`: the ERP sent it cancelled, or it was cancelled on Material requests), the import it came with |
| material_requirement | Material still wanted, one row per ERP document line: a production order's component (`source_kind` production_order, `line_key` the item), an MRP result (`source_kind` mrp, `line_key` its line number) or a kit item for joiners (`source_kind` joiner, `source_doc_no` plan/department, `line_key` kit/start date/item, `department_code` the joiners' department); quantity and unit as the ERP gave them, `base_qty` in the item's unit, need-by, location, `basis` (`gross`, the platform nets stock; `net`, an MRP line the ERP already netted: no free stock is set aside for it), status (open, issued, cancelled). Unique on (source_kind, source_doc_no, line_key), so the same ERP line never loads twice; a line sent again with other values is a change (`demand_change`). The next step reserves stock for the open ones, earliest need-by first |
| material_request | What one production order (the joiners of one plan and department, or one MRP plan at one location) needs from the store, made when its lines are imported: `source_kind` (production_order, joiner or mrp), order (plan/department, or `<plan>/<location>` for MRP), product (kit; none for MRP), location, need-by (its earliest live line's), `department_code` (the joiners' department, whose request buys the shortfall; else the setting `replenishment_department`), status (open, issued, cancelled; a cancelled one keeps its reason). Lines loaded after it is issued or cancelled get a new material request |
| demand_change | A change the ERP sent to demand already loaded (rule Q21), one row per file row (or one per cancel on Material requests, `kind` manual): kind of file, document and line, the requirement (when one), the material request, `what` (qty_down, qty_up, earlier, later, cancelled, unit, basis, header_qty, reference), `before` and `after` (quantity, need-by, status...), `effects` (stock released per item, request lines cut, requests cancelled, need-by dates moved, cases opened, whether the store check ran again), when |
| demand_change_case | A case a change left for a person, one per change and document: its task (kind `demand_change`, owner, due in `demand_change_case_days` working days on the place's calendar, escalation to the owner's manager), `owner_kind` (buyer: the excess is on an order, an RFQ or a request the buyer has, or an order promises later than the new date; store: more was issued than is now needed), document, lines and excess per item, amount (excess x the order's price, else the request's estimate; none for the store), and when closed the outcome (reduced, cancelled, kept_for_stock, expedited, not_possible, returned_to_stock, kept_for_production), note, who and when |
| stock_hold | A lot of stock held out of use (task 4.1): item, place, `quality` (quarantine or blocked), `qty` held and `left_qty` still held (open holds by quality add up to `stock_quality_level`), reason, reference, `source` (receipt: waits for the inspection of `gr_no`/`gr_line_no`; manual: held by the store; import: from `stock_held.csv`, `import_id` its id; decision: blocked from a quarantine decision, `parent_id` that hold), who held it and when, `closed_at` once nothing is left |
| supply_risk | A late-supply case (rule Q17), one open per order and material request: its task (kind `late_supply`, owner the order's buyer, else the category's, else the first buyer; due in `late_supply_case_days` working days on the place's calendar; escalation to the owner's manager), `lines` (`mr_line_no`, `item_code`, `at_risk`, `promised`, `latest_arrival`, `need_by`, `po_line_no`, updated by each evaluation), `buffer_days` it was judged with, and when closed the outcome (expedited, accepted_late, bought_elsewhere by a person, a note for the last two; resolved by the system when nothing on it is at risk), note, who and when |
| planning_note | An explanation of a material request by the planning capability (task 11b.1): the agent run, `status` (accepted, or facts_only with `reasons`), summary and line texts when accepted, `snapshot` (the engine's figures it was given) and its hash: unique per request, so the same figures are never explained twice |
| joiner_kit | An office's kit per kind of new joiner (from `joiner_kit.csv`): kit, item, quantity per joiner, unit and `base_qty`; unique on (kit, item) |
| material_request_line | One component of a material request, in the item's base unit: need, `reserved_qty` (stock set aside, earliest need-by first, never more than is free: by the store check, and by the system whenever stock goes up), `issued_qty` (issued at production start), status. On an open line set aside plus issued never exceeds the need (a line cut below what was issued closes as issued); free stock is on hand less what open lines have set aside. Free stock goes first to what no purchase request covers, then to what a request not yet with the buyer covers, and that request line is cut by as much: set aside plus still requested never exceeds the need |
| material_request_buy | Which purchase request line buys a material request line's shortfall, and how much of it (cut, down to 0, when stock takes over). The view `material_request_cover` adds to each line what live purchase requests still bring for it (`requested_qty`: rejected or cancelled ones no longer count, and what already arrived on their orders, or was closed short, no longer counts, given to the lines earliest need-by first) |
| product_demand | Sales orders and demand plan lines (finished products): why production orders exist; kept for reference |
| demand_import | One ERP import: source file or folder, per file what loaded, what changed, what was loaded before, what was left out and the mapping version used, `changed` (rows applied as changes), who and when. A cancel on Material requests is recorded as an import of its own |
| erp_column_map | The client's column names for each kind of ERP file (`columns`: our column -> theirs), versioned; an import uses the latest version and records which |
| vendor_statement | One supplier statement of account as imported (plan step 13): supplier, the period compared, the file's name and hash (the same file for the same supplier is imported once), how many lines, and how many came out matched, amount differs, only on their side, only on ours; who imported it and when |
| vendor_statement_line | The result of matching a statement, one row per line of theirs (`row_no` is the file's line) and per item of ours in the period that is not on it (`row_no` empty): date, type (invoice, credit_note, debit_note, payment), their number, their amount, our number (PI-... or PAY-...) and amount, the difference (theirs less ours), the outcome and the reason in words |
| bank_statement_line | Every debit line of an imported bank statement, kept once however often it is imported (`line_key` is a hash of date, bank reference, narration, amount and its count in the file): status `matched` (the run is paid, or was already), `waiting` (its payment is found, the rest of the run is not on a statement yet), `open` (no payment fitted; `why` says why) or `confirmed` (a person said which payment it is: `confirmed_by`, `confirmed_at`); `payment_no` once known |
| cutover_import | One load of the open documents of the old system at cutover (task 7.4; dry runs are not kept): who, the files with their row counts, `loaded` and `already` per kind (po, goods_receipt, service_entry, invoice, advance, supplier_ytd), how many rows were left out, `control` per kind (the old system's count and total against what the company now holds from the files) and `control_ok` (readiness `cutover_import` needs the last load with nothing left out and control ok) |
| cutover_supplier_ytd | What was bought from a supplier earlier in the financial year in the old system (`fy` like 2026-27, `taxable`): tax at source (194Q) counts it, and then leaves out the invoices loaded at cutover, which it already holds |
| Cutover documents | An order loaded at cutover sits on a carrier request `CUT-<order>` (`channel` cutover, approved; its lines ordered, `est_source` cutover); its lines have `source` cutover; its receipts `posted_by` cutover and post no stock; its service confirmations `via` cutover; its invoices `channel` cutover, entered and matched by the platform's own rules; its advances in one paid run `CUT-<yyyymmdd>` |
| alembic_version | The migration revision of the database |
| tenant, tenant_database, tenant_setting, feature_flag, app_user, refresh_token | Control database: the companies, where each one's database is (the connection string is encrypted) and its migration revision, per-company settings, feature flags, logins (with failed-attempt lock) and refresh tokens |
| tenant (state) | `state` setup, cutover, live or suspended (D-04; `company_state.py` is the only writer), `state_before_suspension` (where a resume goes back to), `state_changed_at`. A new company starts in setup; a demo reset ends live |
| tenant_state_change | Every move of a company between states: from, to, when, by (`by_kind` user, operator or system, and who), `approved_by` (the second administrator of a go-live), `kind` of a suspension (commercial or security), reason. Insert only to the app |
| tenant_archive | The setup database a cutover kept (D-05): its name (`<database>__setup_<yyyymmddHHMM>`), when, the operator and reason, its audit row count and last hashes of `audit_event` and `setup_change`. Only the owner reads it; the app login cannot connect |
| app_user.provisioned_at | Set once the person's profile is written in the company's database (DF-36); until then the login cannot sign in. `p2p.cli user repair` finishes or removes logins a creation left half made |
| job (cancelled) | A job of a company moved to cutover is `cancelled` (it pointed at test data); a company in cutover or suspended runs no job |

## D. Hidden truth (G, read only by tests)

**truth_item_demand** · item_code, location_code, pattern enum(steady,seasonal,trend,intermittent,lumpy), mean_per_month, cv, season_peak_month, trend_pct_per_year, stockout_periods

**truth_supplier** · supplier_code, profile enum(reliable,average,poor), lead_days_mean, lead_days_sd, on_time_prob, part_delivery_prob, rejection_rate, price_drift_pct_per_quarter, invoice_error_rate, reply_hours_mean, writing_style

**truth_case** · case_id, type, documents, cause, expected_result, planted_on

The profile companies (`apex`, `brightwave`, written by `datagen/generator/company_profile.py`) write `truth_supplier` with the columns supplier_code, profile, on_time_prob, part_delivery_prob, rejection_rate and writing_style (taken from the profile's supplier behaviour), have no `truth_item_demand`, and add these answer keys:

| File | Columns | Meaning |
|---|---|---|
| truth_shortfall | material_request_no, line_no, item_code, needed, on_hand, on_order, held_for_earlier, reserved, short, request_no, request_line_no | Stock first: what each material request line needed, what was on hand and on order, what earlier requests already held, what it reserved and what was short (the requisition line that asked for it) |
| truth_combined_lines | po_no, po_line_no, request_no, request_line_no, item_code, qty, route | Which requisition lines went into which order line, and the buying route: contract, single_source, emergency, safe_reorder or rfq |
| truth_supplier_selection | rfq_no, rfq_line_no, item_code, qty, value_counted, quotes_required, suppliers_asked, options, selected, reason | Per RFQ: the item's value over 30 days, quotes needed (two up to the threshold, three above), every quote (`supplier:price:delivery days`), who was chosen (two when split) and why |
| truth_grni | month_end, receipt_no, po_no, po_line_no, item_code, qty_accepted, qty_invoiced, qty_not_invoiced, value_not_invoiced | Goods (and services) accepted but not covered by a valid invoice received by each month end, at the order price |
| truth_payment | invoice_no, supplier_code, total, tds_section, tds_amount, net_payable, due_date, msme, status_at_cutoff, paid_on | Per payable invoice: tax at source, the net to pay, the due date (MSME limit for micro and small suppliers) and when it was paid |

Their `truth_case` types: duplicate_invoice, price_mismatch, qty_mismatch, part_delivery, failed_inspection,
receipt_without_po, supplier_date_change, unmatched_bank_line, invoice_not_received, grni_month_end, split_order.
`history/vendor_statement_line.csv` (supplier_code, statement_date, supplier_invoice_no, invoice_date, amount,
our_invoice_no) is a supplier's month-end statement; a blank `our_invoice_no` is an invoice that never reached us. A
receipt sent again after a rejection says so in `goods_receipt.remarks`. `approval_event.action` can be `reminded` and
`po_event.type` can be `dispatched` (the dispatch note in `new_value`).

## E. Answer keys (G, one JSON line per document or case)

quotes.jsonl · invoices.jsonl · credit_notes.jsonl · emails.jsonl · requests.jsonl · mismatch_cases.jsonl · assistant_questions.jsonl · certificates.jsonl · attacks.jsonl

Each line holds: file, layout, hold_out, the true values, and for planted cases the true cause.

## E2. Cutover files (G, in a dataset's `cutover/` folder; never loaded by a demo reset)

The documents still open in the old system at the history's cutoff, for `p2p.cli company cutover-import` (or the upload on
the Go-live tab), derived from `history/` by `datagen/generator/cutover.py`. Columns are those of `history/` so a legacy
extract maps one to one; the templates are `GET /api/company/cutover/templates/<file>`. An order is open when a line is
not fully received or invoiced, or an invoice on it is unpaid; what a paid invoice already settled stays in the old system
(a line is carried with its open quantity, its receipts less what paid invoices covered). `cutover-bad/` (Apex) adds one
planted row per check; `truth/truth_cutover.csv` (company, kind, key, expected loaded or left_out, reason_contains, amount)
names every document and the totals (open payable, received not invoiced, advance balance; overall and per supplier).

| File | Columns | Meaning |
|---|---|---|
| po.csv, po_line.csv | order: no, supplier, ship-to, created, payment terms, status (approved, sent, acknowledged, received), buyer, contract, terms, totals; line: item, qty, unit, price, GST rate, dates, cost centre, GL, received and invoiced | Open orders; totals are recomputed (Rs 1.00 tolerance) |
| goods_receipt.csv, gr_line.csv | as history; `inspection_status` not_required, passed or failed (pending is refused) | What was received and accepted; no stock moves |
| service_entry.csv | no, order line, period, amount accepted, by, on | Services confirmed in the old system (Brightwave) |
| invoice.csv, invoice_line.csv | as history, plus `paid_amount` (a part-paid invoice is refused, OP-9) | Unpaid invoices: received, approved or blocked |
| advance.csv | payment no, order, paid on, gross, taxable, tax at source, recovered gross and taxable, UTR | Advances paid and not fully recovered |
| supplier_ytd.csv | supplier, fy, taxable | Bought this financial year in the old system |
| control.csv | kind (po, invoice, advance), count, total | The old system's own totals |

## F. ERP input files (G, in a dataset's `inputs/` folder)

Our own simple CSV templates (decision D36) for what a company's ERP or planning sheet tells purchasing. They are not
loaded by `import masters`; `import demand` (and the upload `POST /api/demand/import/<kind>`) loads production orders,
their lines, MRP results, sales orders, the demand plan, joiner kits and the joiner plan, as CSV or Excel. A client's own column names are mapped to
ours by `erp_column_map`. Quantities are in the item's unit unless `unit` names another one with a conversion.

| File | Columns | Meaning |
|---|---|---|
| product.csv | product_code, name, unit | What the company makes |
| bom.csv | product_code, item_code, qty, unit | Materials for one product (optional; used to work out the production order lines) |
| sales_order.csv | order_no, customer, product_code, qty, need_date | A customer order |
| demand_plan.csv | plan_no, product_code, qty, period, status | Planned sales for a month (`YYYY-MM`) |
| production_order.csv | order_no, product_code, qty, need_by, reason, location_code, status | What to make by when; reason is the sales order or demand plan; status blank or `open`, or `cancelled` (sent again cancelled) |
| production_order_line.csv | order_no, item_code, qty, unit, need_by, status | The materials an order needs: the bill of materials times the order quantity (unit and need_by may be blank: the item's unit, the order's date) |
| mrp_result.csv | plan_no, line_no, item_code, qty, unit, need_by, location_code, basis, status | What the ERP's material planning says is needed, per plan line; basis `gross` (blank) or `net` (the ERP already netted stock). Apex has MRP-2027-01 for DP-2027-01 |
| joiner_kit.csv | kit, item_code, qty, unit | An office's "materials per joiner": what each kind of new joiner gets (unit may be blank: the item's unit) |
| joiner_plan.csv | plan_no, department, kit, count, start_date, location_code, status | How many people join a department, with which kit, from when; each row becomes its kit's items times the count, needed by the start date, and one material request per plan and department (location may be blank: the main stock location). A moved start date is the old row cancelled and a new row |

A row of these files sent again with other values is applied as a change (rule Q21; refused only for a changed product,
location or item: cancel and send a new one). `changes/<scenario>/` holds the changes of the demo scenarios (Apex A1-A5,
A5b, M2, R1; Brightwave B1-B4; never loaded by a demo reset) and `truth/truth_demand_change.csv` (scenario,
mr_order_no, item_code, location_code, need, reserved and open request before and after, buyer_case_qty,
store_case_qty, new_request_qty) what each must do, worked out by the generator from the raw files: one row per material
request and item touched, `before` after the scenario's setup (a new material request of the same order has need_before
0).

`inputs/history/` holds the same files over the twelve months of history, each with a `status` (sales orders delivered,
production orders completed, lines issued, joiners joined), plus the material requests the platform has no table for
yet: `material_request.csv` (mr_no, source, department, requested_by, raised_on, need_by, status; source is the
production order, `JP-YYYY-MM/<department>` or `replacement`) and `material_request_line.csv` (mr_no, line_no,
item_code, qty, unit, issued_on, status).


---

## Notes on the generated dataset (version 2.1)

Additions and differences between this dictionary and what `datagen/generator` writes:

- **Added tables:** `sanction_entry` (fictional sanctions list, master), `truth_match_expected` (truth: the match result a correct platform must reach for every invoice, with the planted cause), `tds_challan` carries a `section` column (194Q, 194C or 194J), `return_to_vendor`, `po_schedule`, `po_charge`, `invoice_charge`, `advance_adjustment` are generated.
- **Extra columns:** `item` has `gst_rate_before`, `gst_rate_after`, alternative rates, `gst_rate_change_date`, `gst_rate_confidence`; `email_message` has `kind`, `style`, `supplier_code`; `item_location` has `order_qty`; `truth_item_demand` has `seasonal_amplitude`; `payment` has type `advance` for the five 30%-advance suppliers; `tds_entry.payment_no` is filled (and `invoice_no` blank) for TDS deducted on advances.
- **Prospective suppliers:** three suppliers have status `draft` (SUP-0061 to SUP-0063): one matches the sanctions list, one has incomplete documents, one is clean. They have no contracts or orders.
- **Reference tables written to `reference/`:** `gst_state`, `uqc_unit`, `gst_rate_goods`, `gst_rate_services`, `gst_rate_history`, `tds_section`, `rbi_bank_rate`, `compliance_parameter`. The full `pincode_directory` and `hsn_sac_codes` lists stay in `data/reference/`.
- **Not generated:** `document_template`, `terms_template`, `document_file` (the invoice and reply documents themselves come from `datagen/generator/documents.py`, with an answer key `documents/manifest.json`), `match_result` (platform output) and the tables that only the running platform produces.

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


### Supplier communication and intake additions (platform)

`supplier_message` keeps each supplier email read by the supplier communication agent (class, reading, what was applied, the buyer task it opened); `po_commitment` records a quantity a supplier committed to within the accepted percentage (the order itself is never changed by an email); `supplier_chase` records each reminder or part-delivery chase so repeats are limited by `supplier_chase_days` and `supplier_chase_max`. A promised date within `supplier_date_days` moves the open lines' promised date through the gate `supplier.change`; reminders go through `supplier.remind`; anything else (price, substitution, late date, quantity beyond `supplier_change_pct`, questions, bank-detail changes) becomes a buyer or AP task with a drafted reply. The intake agent reads attachments of request emails, makes service and capex drafts as free-text lines, answers a follow-up message from the requester's own open request, and warns about a split order within `split_order_window_days`.

Approval bound to the version (block 4, task 0.2; DF-07, DF-08, Q19). Each `po_approval_step` keeps the `version` of the order it approves; an approver states the version they saw and is refused if the order changed since. When an order becomes approved, `po.approved_version` is its version and **po_approved_version** keeps what it said · id, po_no, version (unique per order), approved_at, approved_by (org codes joined by `,`, or `rule:PO-01` for a touchless order, `backfill` for orders approved before the block), rule_code, rule_version, band_max (the approving `po` rule's top; empty for the top band), total, snapshot (supplier_code, ship_to_location, payment_terms_code, lines: line_no, item_code, qty, unit_price, need_by). A revision is measured from the last approved version: a price up, a quantity above the approved one, an item changed or a new line, another supplier, delivery location or payment terms, a need-by moved more than `supplier_date_days` either way, a total above `band_max`, or a higher total is material and approved again on the `po` rule of the new total (`po_amendment` rows are not read); anything else carries the approval. A materially revised order is never touchless (check `touchless.revision`) and is not sent until approved. A supplier's price change is judged from the first approved price (`po_change.base_value`), so small rises add up; above `supplier_price_pct` of it the variance proposal is (new - base) x quantity.

## Budget engine and forward buying (block 3: tasks 3.7 and 13.5)

What counts against a budget (FR27): for a GL account and a holder (cost-centre scope or location), `used = held by
requests + held by orders + spent`, read from the view `budget_use` (part, request_no, request_line_no, po_no,
po_line_no, gl_code, cost_centre_code, department_code, charge_location, location_code, period, amount). Held by requests:
request lines `open` or `sourcing` on requests `submitted` or `approved`, at qty x estimate, whatever their date (open
commitments carry into a new year). Held by orders: order lines not cancelled or closed on orders not cancelled, rejected
or closed, at (qty - short closed) x price less what is already actual on the line, never below 0, before GST, charges
never. Spent: `budget_actual` rows of the financial year up to the month checked. The request checked counts in none of
them. A closed request no longer counts at its estimate: its orders and actuals do. `request.budget_check` entries and
`GET /api/budgets/check/{no}` carry `held_by_requests`, `held_by_orders`, `spent` and `committed` (their sum).

When spend is actual (D-10): a stocked item when its goods are accepted (at once, or when inspection accepts them), at the
order price, in the month of acceptance (`budget_actual.source` receipt, `gr_no`); a rejected quantity never. Anything
else when its invoice is paid, at the line's taxable value, in the month the invoice was received (`source` payment); an
invoice's freight and packing less a discount taken are one more row (no order line). Invoice price differences on stocked
lines are not posted (PPV, task 13.2). `budget_actual` keeps `po_no`, `po_line_no`, `request_no`, `request_line_no`,
`location_code`, `event_period` (the month it happened) and `period` (the month it is booked in) and `posted_at`. One
writer: `approvals/budget_ledger.py` (audit `budget.actual`).

Months (D-09): a month is `locked`, `open` (ended and not locked) or `future` (not ended: the current month, which
still takes postings, and the months after it). Finance (`budget.manage`) locks a month that has ended once every earlier month of its year is
locked (`budget.period_lock`); a lock is never undone in Phase 1. An actual whose month is locked is booked in the first
open month after it, keeping `event_period` (`budget.actual_moved`). A masters import leaves out a new budget row for a
locked month ("period 2026-11 is locked"). The year end (`budget_year_end`: fy, closed_by, closed_at, note, carried,
totals; from 1 April, `budget.year_close`) locks the year's months not yet locked and keeps the open commitments carried
per account, cost centre and location; unspent budget lapses. A budget adjustment (supplement, return, transfer) is the
proposal `budget.adjust`, decided by another holder of `budget.manage`, written as `budget` rows of kind `adjustment`.

Forward buying (13.5): `forward_buy` keeps one recommendation (item, location, the anchor request line, status open,
accepted, approved, declined, rejected, superseded or lapsed; the rule label, the `rule_evaluation` and benchmark it used;
price now, trend now, slope, dip, average use, cover, the months with expected price, net saving per unit and quantity;
the quantity in purchase units and stock units, estimate, value, gross saving, holding cost, net saving, the limit that
set the quantity; the explanation and its source template or ai; the buyer's decision, accepted quantity and net saving,
the proposal `forward_buy.approve`, the approver, the request it made, superseded_by, lapse_reason). At most one is open or
accepted per item and location. The limit, when several bind: budget or storage, then holding cost, then shelf life, then
the cover months; `need` when the cover (stock and open supply) already meets part of the need ahead (need ahead in a
month = max(open material requirements due then, 12-month average use)). The rule versions are changed from the admin
app (`/api/setup/forward-buy`, answered 202 with a proposal a second administrator approves, D-22; audit
`forward_buy_rule.change`); the approver of the extra quantity is the `forward_buy` row of `approval_rule`.

