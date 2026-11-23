# Database schema (generated)

Every table and column the migrations make, one line per table. **Do not edit by hand:** run `python scripts/gen_schema_doc.py`
after a migration (a test fails when this file is out of date). The meaning of the data is in `data-dictionary.md`.

Reading a line: `name*` is part of the primary key, `name?` may be empty, then the type (`num` is an exact decimal, never a float).


## Control database (one for the platform)

9 tables.

**app_user** · id* int, tenant_id int, email text, password_hash text, active bool, failed_attempts int, locked_until? timestamptz, created_at timestamptz, provisioned_at? timestamptz

**feature_flag** · id* int, tenant_id int, key text, enabled bool

**job** · id* bigint, tenant_slug text, kind text, payload jsonb, run_at timestamptz, status text, attempts int, locked_at? timestamptz, last_error? text, created_at timestamptz, finished_at? timestamptz, dedupe_key? text

**refresh_token** · id* int, user_id int, token_hash text, expires_at timestamptz, revoked_at? timestamptz, created_at timestamptz

**tenant** · id* int, slug text, name text, active bool, created_at timestamptz, state text, state_before_suspension? text, state_changed_at timestamptz

**tenant_archive** · id* int, tenant_id int, database_name text, archived_at timestamptz, operator text, reason text, audit_rows int, audit_last_hash? text, setup_change_last_hash? text

**tenant_database** · tenant_id* int, database_name text, connection_encrypted text, schema_revision? text, migrated_at? timestamptz

**tenant_setting** · id* int, tenant_id int, key text, value json

**tenant_state_change** · id* int, tenant_id int, from_state text, to_state text, at timestamptz, by_kind text, by_id text, approved_by? text, kind? text, reason text


## Tenant database (one per company)

162 tables.

**advance** · payment_no* text, po_no text, gross num, taxable num, used_gross num, used_taxable num, paid_on? date

**advance_adjustment** · id* bigint, advance_payment_no text, invoice_no text, amount num, taxable num

**agent_run** · id* int, thread_id text, agent text, status text, input? text, result? text, waiting_for? text, started_at timestamptz, finished_at? timestamptz, capability? text, started_by? text, user_id? int, via? text, workflow_id? int, event_id? bigint, job_id? bigint

**agent_step** · id* bigint, run_id int, at timestamptz, kind text, name? text, detail? jsonb

**ai_capability** · code* text, enabled bool, checks? jsonb, changed_at timestamptz, changed_by text

**alert** · id* bigint, key text, rule text, subject_type text, subject_id text, severity text, title text, detail? text, link? text, roles text, status text, opened_at timestamptz, resolved_at? timestamptz, last_seen_at timestamptz, times_opened int

**app_setting** · key* text, value text, updated_at timestamptz

**approval_rule** · id* bigint, rule_code? text, document? text, min_value? num, max_value? num, category_code? text, department_code? text, spend_type? text, approver_chain? text, version? int, effective_from? date, effective_to? date, location_code? text

**approval_step** · id* bigint, request_no text, round int, step int, role text, approver text, delegate? text, status text, acted_by? text, acted_at? timestamptz, comment? text, task_id? bigint, rule_id? bigint, rule_code? text, rule_version? int, route_reason? text, opened_at? timestamptz, reminded_at? timestamptz

**approver_role** · role* text, source text, value? text

**audit_event** · id* bigint, at timestamptz, actor_type text, actor_id text, action text, entity text, entity_id text, before? jsonb, after? jsonb, reason? text, rule_version? text, model? text, prompt_version? text, prev_hash text, row_hash text

**autonomy_global** · id* int, kill_switch bool

**autonomy_outcome** · id* bigint, decision_type text, at timestamptz, auto bool, wrong bool, ref? text, recommendation_id? int

**autonomy_setting** · decision_type* text, level text, value_cap? num, min_confidence num, max_wrong_rate num, updated_by? int, updated_at timestamptz

**award** · rfq_no text, rfq_line_no int, supplier_code text, quote_no text, qty num, price num, reason? text, overridden bool, by_user? text, at timestamptz, split bool, selection_rule? text, conditional bool, lapsed_at? timestamptz, lapse_reason? text, awarded_on? date, id* bigint, status text, decision_id? bigint

**award_decision** · id* bigint, rfq_no text, round int, status text, proposed_by text, proposed_at timestamptz, reason? text, value_taxable num, cgst num, sgst num, igst num, value_total num, rule_code? text, rule_version? int, approver? text, delegate? text, proposal_id? bigint, recommendation_id? int, regret_notes bool, note? text, note_source? text, decided_by? text, decided_at? timestamptz, decision_note? text

**award_letter** · id* bigint, decision_id bigint, supplier_code text, kind text, message_id? text, contract_code? text, queued_at timestamptz

**bank** · code* text, name? text, ifsc_prefix? text

**bank_statement_line** · id* bigint, line_key text, row_no int, value_date? date, utr? text, narration? text, amount num, status text, payment_no? text, why? text, imported_by text, imported_at timestamptz, confirmed_by? text, confirmed_at? timestamptz

**budget** · id* bigint, cost_centre_code? text, gl_code? text, project_code? text, period? text, amount? num, version? int, policy? text, location_code? text, kind text, reason? text, proposal_id? bigint

**budget_actual** · id* bigint, invoice_no? text, cost_centre_code? text, gl_code? text, period text, amount num, source text, gr_no? text, po_no? text, po_line_no? int, request_no? text, request_line_no? int, location_code? text, event_period? text, posted_at timestamptz

**budget_period_lock** · period* text, locked_by text, locked_at timestamptz, reason text

**budget_year_end** · fy* text, closed_by text, closed_at timestamptz, note? text, carried jsonb, totals jsonb

**buying_route_rule** · id* bigint, category_code? text, version int, effective_from date, effective_to? date, agree_above? num, reorder_within_days? int, reorder_on_time_pct? num, reorder_below? num, reorder_price_pct? num, two_quotes_from? num, three_quotes_above? num, window_days? int, note? text, created_by? text, created_at timestamptz

**case_event** · id* bigint, case_id int, at timestamptz, event text, from_state? text, to_state? text, actor_type text, actor_id text, detail? jsonb

**checkpoint_blobs** · thread_id* text, checkpoint_ns* text, channel* text, version* text, type text, blob? bytea

**checkpoint_migrations** · v* int

**checkpoint_writes** · thread_id* text, checkpoint_ns* text, checkpoint_id* text, task_id* text, idx* int, channel text, type? text, blob bytea, task_path text

**checkpoints** · thread_id* text, checkpoint_ns* text, checkpoint_id* text, parent_checkpoint_id? text, type? text, checkpoint jsonb, metadata jsonb

**company** · code* text, legal_name? text, short_name? text, pan? text, cin? text, registered_address? text, turnover_band? text, fy_start_month? int, currency? text, email_domain? text, letterhead_text? text

**company_bank_account** · code* text, bank_code? text, account_no? text, ifsc? text, purpose? text, payment_file_format? text

**contract** · code* text, supplier_code? text, type? text, valid_from? date, valid_to? date, max_value? num, payment_terms_code? text, delivery_terms? text, freight_terms? text, warranty_months? text, sla_text? text, status? text, created_by? text, source_rfq? text, award_decision_id? bigint, supplier_accepted_at? timestamptz, supplier_accepted_by? text, supplier_declined_at? timestamptz, decline_reason? text

**contract_link** · id* bigint, contract_code text, supplier_code text, purpose text, sent_to text, issued_at timestamptz, expires_at timestamptz, used_at? timestamptz, used_for? text

**cost_centre** · code* text, name? text, department_code? text, location_code? text, owner_user? text, active? bool

**cutover_import** · id* int, at timestamptz, imported_by int, files jsonb, dry_run bool, loaded jsonb, already jsonb, rejected int, control jsonb, control_ok bool

**cutover_supplier_ytd** · supplier_code* text, fy* text, taxable num

**delegation** · id* bigint, from_user? text, to_user? text, start_date? date, end_date? date, reason? text

**delivery_reminder** · id* bigint, po_no text, kind text, promised_date date, number int, sent_to? text, sent_at timestamptz

**demand_change** · id* bigint, import_id bigint, kind text, doc_no text, line_key? text, requirement_id? bigint, mr_no? text, what ARRAY, before jsonb, after jsonb, effects jsonb, applied_at timestamptz

**demand_change_case** · task_id* int, change_id bigint, owner_kind text, doc_type text, doc_no text, lines jsonb, excess jsonb, amount? num, outcome? text, note? text, closed_by? text, closed_at? timestamptz

**demand_import** · id* bigint, source text, files jsonb, loaded int, already_loaded int, rejected int, imported_by? text, imported_at timestamptz, changed int

**department** · code* text, name? text, head_user? text

**dispatch_note** · id* bigint, po_no text, shipped_on date, expected_on? date, vehicle_no? text, lr_no? text, invoice_no? text, source text, email_id? bigint, recorded_by text, created_at timestamptz

**dispatch_note_line** · note_id* bigint, po_line_no* int, qty num

**document** · id* int, doc_type text, filename text, content_type text, size bigint, sha256 text, storage_key text, linked_type? text, linked_id? text, uploaded_by? int, created_at timestamptz

**email_message** · id* int, message_id text, in_reply_to? text, direction text, from_addr? text, to_addr? text, subject? text, body_text? text, sent_at? timestamptz, plus_tag? text, linked_type? text, linked_id? text, case_id? int, raw_document_id? int, attachment_ids jsonb, created_at timestamptz, status? text, queued_at? timestamptz, attempts int, last_error? text, sealed? text, reply_to? text, sender? text, job_kind? text, received_on? date, auth? jsonb, po_version? int

**erp_column_map** · kind* text, version* int, columns jsonb, created_by? text, created_at timestamptz

**event** · id* bigint, type text, key text, source text, payload jsonb, occurred_at timestamptz, recorded_at timestamptz, status text, workflow_id? int, routed_at? timestamptz, note? text

**forward_buy** · id* bigint, item_code text, location_code text, request_no? text, line_no? int, status text, rule_label text, evaluation_id? bigint, benchmark_id? bigint, price_now? num, trend_now? num, slope? num, dip_pct? num, avg_use? num, cover? num, qty_base? num, qty? num, est_price? num, uom? text, months jsonb, value? num, gross_saving? num, holding_cost? num, net_saving? num, limit_kind? text, explanation? text, explanation_source? text, created_at timestamptz, decided_by? text, decided_at? timestamptz, decision_reason? text, accepted_qty? num, accepted_net_saving? num, proposal_id? bigint, approved_by? text, approved_at? timestamptz, new_request_no? text, superseded_by? bigint, lapse_reason? text

**forward_buy_rule** · id* bigint, version int, category_code? text, item_code? text, location_code? text, effective_from date, effective_to? date, enabled? bool, min_gap_pct? num, min_trend_months? int, max_cover_months? int, holding_cost_pct_month? num, min_net_saving_inr? num, shelf_life_months? int, storage_max_qty? num, note? text, entered_by? text, entered_at? timestamptz

**gl_account** · code* text, name? text, type? text, is_capex? bool, is_tax_account? bool, parent_code? text

**goods_receipt** · no* text, po_no text, location_code text, gate_entry_at timestamptz, received_by text, delivery_challan_no? text, supplier_invoice_ref? text, eway_bill_no? text, vehicle_no? text, inspection_status text, accepted_on? date, remarks? text, posted_by text, created_at timestamptz, tolerance_version? int

**gr_line** · gr_no* text, po_no text, po_line_no* int, qty_received num, qty_accepted? num, qty_rejected? num, reject_reason? text

**gst_registration** · gstin* text, state_code? text, legal_address? text, pincode? text, is_default? bool

**intake_pending** · id* bigint, user_id int, channel text, source_text text, question text, asked text, attempts int, status text, created_at timestamptz, updated_at timestamptz

**invoice** · no* text, type text, supplier_code text, supplier_gstin? text, buyer_gstin? text, place_of_supply? text, supplier_invoice_no text, dedupe_key text, invoice_date date, fy text, irn? text, ack_no? text, ack_date? date, eway_bill_no? text, reverse_charge bool, received_at timestamptz, received_on date, channel text, taxable num, cgst num, sgst num, igst num, cess num, round_off num, total num, ref_invoice_no? text, due_date? date, payment_block text, status text, duplicate_of? text, block_reason? text, cancel_reason? text, document_ref? text, entered_by? text, created_at timestamptz, tds_amount num, paid_on? date, booked_at? timestamptz, advance_used num, advance_taxable_used num, discount_taken num, tolerance_version? int

**invoice_charge** · id* bigint, invoice_no text, type text, amount num, gst_rate num, sac? text

**invoice_diagnosis** · id* bigint, invoice_no text, cause text, fix text, explanation text, vendor_subject? text, vendor_body? text, source text, run_id? bigint, created_by? int, created_at timestamptz

**invoice_draft** · id* bigint, source text, email_id? bigint, document_id? bigint, filename? text, document_hash? text, supplier_code? text, status text, path? text, reading? jsonb, qr? jsonb, proposal? jsonb, problems jsonb, assumptions jsonb, confidence? num, model? text, prompt_version? text, invoice_no? text, decided_by? text, decided_at? timestamptz, decision_note? text, created_by? text, created_at timestamptz

**invoice_line** · invoice_no* text, line_no* int, po_no text, po_line_no int, description? text, hsn_sac? text, qty num, uom? text, unit_price num, discount num, taxable num, gst_rate num, cgst num, sgst num, igst num

**item** · code* text, name? text, description? text, category_code? text, kind? text, default_gl? int, brand? text, base_uom? text, purchase_uom? text, pack_size? int, moq? int, hsn_sac? text, gst_rate_before? int, gst_rate_after? int, gst_rate_alt_before? int, gst_rate_alt_after? int, gst_rate_change_date? date, gst_rate_confidence? text, gst_reverse_charge? text, gst_note? text, standard_price? num, abc_class? text, stocked? bool, capex? bool, inspection_required? bool, hazardous? bool, default_lead_days? int, attributes? text, active? bool, serialised? bool

**item_category** · code* text, name? text, parent_code? text, kind? text, default_gl? int, stocked? bool, capex? bool, min_quotes? int, buyer_user? text, approval_category? text, required_attributes? text, required_supplier_docs? text, inspection_required? bool, default_tds_section? text, reverse_charge_default? text

**item_location** · item_code* text, location_code* text, reorder_policy? text, reorder_point? num, safety_stock? num, min_qty? num, max_qty? num, order_qty? num, service_level_pct? int, lead_days_override? int

**item_single_source** · item_code* text, supplier_code text, reason? text, marked_by? text, marked_at timestamptz

**item_substitute** · item_code* text, substitute_code* text, note? text

**joiner_kit** · id* bigint, kit text, item_code text, qty num, unit text, base_qty num, import_id bigint, created_at timestamptz

**location** · code* text, name? text, type? text, gstin? text, address? text, city? text, state_code? text, pincode? text, is_stock_location? bool, working_days? text, receiving_hours? text, holiday_scope? text, loc_short? text, head_user? text, region_code? text

**market_benchmark** · id* bigint, item_code text, price num, uom text, basis text, effective_date date, region_code? text, source_kind text, source_name text, source_ref? text, document_id? bigint, note? text, status text, entered_by? text, entered_at timestamptz, verified_by? text, verified_at? timestamptz, verify_note? text, rejected_reason? text, replaces? bigint

**match_result** · id* bigint, invoice_no text, invoice_line_no? int, result text, reason_codes? text, price_var? num, qty_var? num, detail? text, created_at timestamptz, resolved_by? text, resolved_at? timestamptz, resolution? text, tolerance_version? int

**material_request** · no* text, production_order_id? bigint, order_no text, product_code? text, location_code text, need_by date, status text, cancel_reason? text, created_by? text, created_at timestamptz, source_kind text, department_code? text

**material_request_buy** · id* bigint, mr_line_id bigint, request_no text, request_line_no int, qty num, created_at timestamptz

**material_request_line** · id* bigint, mr_no text, line_no int, requirement_id bigint, item_code text, location_code text, uom text, need_qty num, reserved_qty num, issued_qty num, need_by date, status text

**material_requirement** · id* bigint, source_kind text, source_doc_no text, line_key text, production_order_id? bigint, item_code text, qty num, unit text, base_qty num, need_by date, location_code text, status text, import_id bigint, created_at timestamptz, department_code? text, basis text

**model_cache** · request_hash* text, response jsonb, created_at timestamptz

**model_call** · id* bigint, at timestamptz, step text, provider? text, model? text, prompt_version? text, tokens_in int, tokens_out int, cost_usd num, ms int, attempts int, outcome text, error? text, request_hash? text, masked? jsonb, run_id? int, capability? text, user_id? int, workflow_id? int, entity? text, entity_id? text

**notification** · id* bigint, user_id? int, kind text, title text, body? text, link? text, created_at timestamptz, read_at? timestamptz, severity text, related_type? text, related_id? text, resolved bool, resolved_at? timestamptz

**number_counter** · document_type* text, scope_key* text, fy* text, last_seq int

**number_series** · id* bigint, document_type? text, prefix? text, scope? text, format? text, reset? text

**one_time_code** · id* bigint, purpose text, ref text, user_id? bigint, code_hash text, created_at timestamptz, expires_at timestamptz, attempts int, status text, used_at? timestamptz, address? text

**order_history** · id* bigint, po_no text, line_no int, item_code text, location_code text, ordered_on date, qty num, uom? text, unit_price? num

**org_setting** · key* text, value? text, description? text

**org_user** · code* text, employee_no? text, name? text, designation? text, email? text, phone? text, department_code? text, location_code? text, manager_code? text, approval_limit? num, notify_by? text, active? bool

**org_user_role** · id* bigint, user_code? text, role? text, scope_location? text, scope_department? text

**payment** · no* text, run_no text, type text, supplier_code text, po_no? text, amount num, tds_amount num, msme_interest num, mode text, reference? text, utr? text, paid_on? date, remittance_sent_at? timestamptz, created_at timestamptz

**payment_allocation** · payment_no* text, invoice_no* text, amount num, discount num

**payment_run** · no* text, run_date date, company_bank_account? text, proposed_by text, approved_by? text, approved_at? timestamptz, status text, total num, proposal_id? bigint, file_text? text, created_at timestamptz

**payment_terms** · code* text, name? text, days? int, baseline? text, advance_pct? int, discount_pct? num, discount_days? int

**period_demand_line** · id* bigint, item_code text, location_code text, period_from date, period_to date, qty num, uom? text, suggested_qty? num, suggestion? text, status text, review_task_id? int, created_by text, created_at timestamptz

**planning_note** · id* bigint, mr_no text, run_id? int, status text, summary? text, lines? jsonb, snapshot jsonb, snapshot_hash text, reasons ARRAY, created_by text, created_at timestamptz

**po** · no* text, po_type text, supplier_code text, supplier_gstin? text, buyer_gstin? text, ship_to_location text, buyer? text, request_no? text, rfq_no? text, contract_code? text, created_at timestamptz, approved_at? timestamptz, sent_at? timestamptz, payment_terms_code? text, delivery_terms? text, taxable num, cgst num, sgst num, igst num, total num, status text, cancel_reason? text, rule_code? text, rule_version? int, approval_round int, decision_reason? text, hold_reason? text, hold_proposal_id? bigint, sent_to? text, acknowledged_at? timestamptz, ack_note? text, version int, budget_accepted bool, closed_reason? text, closed_at? timestamptz, approval_tier? text, touchless bool, touchless_checks? jsonb, touchless_note? text, touchless_rule? text, approved_version? int, acknowledged_version? int

**po_approval_step** · id* bigint, po_no text, round int, step int, role text, approver text, delegate? text, status text, acted_by? text, acted_at? timestamptz, comment? text, task_id? bigint, version? int

**po_approved_version** · id* bigint, po_no text, version int, approved_at timestamptz, approved_by text, rule_code? text, rule_version? int, band_max? num, total num, snapshot jsonb

**po_change** · id* bigint, po_no text, po_line_no? int, kind text, old_value? text, new_value? text, reason? text, source text, email_id? bigint, status text, within_tolerance? bool, task_id? bigint, proposal_id? bigint, decided_by? text, decided_at? timestamptz, decision_note? text, version_made? int, created_at timestamptz, base_value? text, po_version? int

**po_charge** · id* bigint, po_no text, type text, amount num, gst_rate num, cgst num, sgst num, igst num

**po_commitment** · id* bigint, po_no text, po_line_no? int, promised_qty? num, promised_date? date, email_id? bigint, note? text, created_at timestamptz, po_version? int, source? text, acknowledgement_id? bigint

**po_line** · po_no* text, line_no* int, request_no text, request_line_no int, item_code? text, description? text, qty num, uom? text, base_qty? num, unit_price num, est_price? num, gst_rate num, taxable num, cgst num, sgst num, igst num, need_by? date, promised_date? date, received_qty num, invoiced_qty num, gl_code? text, source text, source_ref? text, status text, short_closed_qty num, price_record_id? bigint

**po_revision** · id* bigint, po_no text, version int, changed_by? text, changed_at timestamptz, reason text, changes jsonb, total_before num, total_after num, reapproval bool, baseline_version? int, material jsonb

**po_version** · id* bigint, po_no text, version int, made_at timestamptz, made_by text, reason? text, snapshot jsonb, document_id? int, sent_at? timestamptz, sent_to? text, acknowledged_at? timestamptz, acknowledged_by? text, ack_via? text, ack_address? text, ack_link_id? bigint, ack_code_id? bigint, ack_email_id? int, ack_sender_auth? jsonb, ack_change_ids? ARRAY, ack_note? text

**price_record** · id* bigint, supplier_code? text, item_code? text, contract_code? text, uom? text, price? num, min_qty? int, price_basis? text, freight_included? bool, valid_from? date, valid_to? date, lead_days? int, version int, status text, created_by? text, activated_by? text, activated_at? timestamptz, location_code? text, region_code? text

**price_threshold_rule** · id* bigint, category_code? text, version int, effective_from date, effective_to? date, gap_pct num, gap_inr_per_unit num, min_saving_inr num, evidence_max_age_days int, min_sources int, expiry_review_days int, note? text, created_by? text, created_at timestamptz

**product_demand** · id* bigint, kind text, doc_no text, product_code text, customer? text, qty num, need_by? date, period? text, status? text, import_id bigint, created_at timestamptz

**production_order** · id* bigint, order_no text, product_code text, qty num, need_by date, reason? text, location_code text, status text, import_id bigint, created_at timestamptz

**project** · code* text, name? text, type? text, cost_centre_code? text, budget_amount? num, start_date? date, end_date? date, status? text

**proposal** · id* int, action text, summary text, payload jsonb, proposed_by_type text, proposed_by_id text, proposed_for? text, risk text, required_permission text, document? text, value? num, case_id? int, status text, execution? text, result? jsonb, created_at timestamptz, expires_at? timestamptz, decided_by? int, decided_at? timestamptz, decision_note? text

**purchase_case** · id* int, ref text, state text, title? text, data jsonb, created_at timestamptz, updated_at timestamptz

**quote** · no* text, rfq_no text, supplier_code text, revision int, received_at timestamptz, valid_until? date, payment_terms_code? text, delivery_terms? text, packing_forwarding num, freight num, warranty_months? int, deviations? text, document_ref? text, entered_by? text, channel text, email_id? int, sender_address? text, contact_id? bigint, sender_auth? jsonb, received_on date

**quote_draft** · id* bigint, rfq_no text, supplier_code? text, email_id bigint, intent? text, status text, reading? jsonb, proposal? jsonb, problems jsonb, assumptions jsonb, confidence? num, model? text, prompt_version? text, quote_no? text, created_at timestamptz, decided_by? text, decided_at? timestamptz, decision_note? text

**quote_line** · quote_no* text, rfq_line_no* int, item_code? text, offered_brand? text, qty? num, moq? num, unit_price num, discount_pct num, gst_rate num, delivery_days? int, deviation? text

**recommendation** · id* int, capability text, kind text, business_key text, workflow_id? int, run_id? int, event_id? bigint, summary text, evidence jsonb, engine_figures jsonb, rule_versions jsonb, basis_hash text, status text, proposal_id? int, created_by text, directed_by? int, decided_by? int, decided_at? timestamptz, decision_note? text, stale_reason? text, superseded_by? int, task_id? int, created_at timestamptz, ai_note? text, ai_note_model? text, ai_note_at? timestamptz

**ref_gst_rate** · id* int, kind text, code_prefix text, description? text, cgst num, sgst num, igst num, effective_from date, effective_to? date, reverse_charge bool, needs_review bool, source? text

**ref_gst_state** · code* text, name text

**ref_holiday** · id* int, holiday_date date, name text, kind text, scope text

**ref_hsn_sac** · code* text, kind* text, level? text, description? text

**ref_load** · id* int, loaded_at timestamptz, files jsonb

**ref_rbi_bank_rate** · effective_from* date, rate_pct num

**ref_tds_section** · id* int, act text, section text, nature? text, payee_type? text, rate_pct? num, rate_no_pan_pct? num, threshold_single? num, threshold_annual? num, base_rule? text, effective_from date, effective_to? date, status? text

**ref_uom** · code* text, name text

**region** · code* text, name text

**request** · no* text, requester? text, on_behalf_of? text, department_code? text, ship_to_location text, type text, priority text, project_code? text, created_at timestamptz, need_by? date, channel text, source_ref? text, justification? text, preferred_supplier? text, status text, total_value num, rule_code? text, rule_version? int, decision_reason? text, submitted_at? timestamptz, decided_at? timestamptz, approval_round int, budget_check? jsonb

**request_check** · id* bigint, request_no text, kind text, other_request text, similarity num, detail? jsonb, flagged_at timestamptz, override_reason? text, overridden_by? text, overridden_at? timestamptz

**request_line** · request_no* text, line_no* int, item_code? text, free_text? text, attributes? jsonb, qty num, uom? text, base_qty? num, est_price? num, need_by? date, delivery_location? text, cost_centre_code? text, gl_code? text, category_code? text, project_code? text, reason? text, status text, stock_qty? num, est_source? text

**rfq** · no* text, buyer? text, request_no? text, category_code? text, created_at timestamptz, due_date date, type text, delivery_terms? text, payment_terms_proposed? text, status text, sent_at? timestamptz, proposal_id? bigint, note? text, min_quotes? int, min_quotes_rule? text, delivery_location? text, kind text, period_from? date, period_to? date, review_task_id? int, confirmed_by? int, confirmed_at? timestamptz, confirmed_via? text

**rfq_line** · rfq_no* text, line_no* int, request_no? text, request_line_no? int, item_code? text, description? text, qty num, uom? text, need_by? date, period_line_id? bigint

**rfq_supplier** · rfq_no* text, supplier_code* text, email? text, invited_at? timestamptz, send_error? text, reminders_sent int, responded_at? timestamptz, declined bool, decline_reason? text, last_reminded_at? timestamptz, invited_by? text, message_id? text

**role_permission** · role* text, action* text

**rule_evaluation** · id* bigint, at timestamptz, key text, version int, input jsonb, output? jsonb, trace? jsonb, actor text, case_ref? text

**rule_table** · id* int, key text, version int, status text, jdm jsonb, note? text, created_by text, created_at timestamptz, published_at? timestamptz

**sanction_entry** · id* bigint, name? text, country? text, list_name? text, listed_on? date, aliases? text

**serial_number** · item_code* text, serial* text, gr_no text, po_no text, po_line_no int, created_at timestamptz

**service_entry** · no* text, po_no text, po_line_no int, period_from? date, period_to? date, basis text, value num, qty_accepted num, amount_accepted num, accepted_by text, accepted_on date, via text, remarks? text, posted_by text, created_at timestamptz

**setup_change** · id* bigint, at timestamptz, by_user text, area text, key text, old_value? jsonb, new_value? jsonb, reason text, approved_by? text, prev_hash text, row_hash text

**sourcing_decision** · id* bigint, request_no text, line_no int, method text, supplier_code? text, contract_code? text, price? num, uom? text, lead_days? int, price_valid_to? date, rfq_no? text, reason text, decided_at timestamptz, route? text, route_rule? text, checks? jsonb, line_value? num, window_value? num, min_quotes? int, decided_on? date, needs_agreement bool, proposal_id? bigint, price_record_id? bigint, path_case? text, path_confirmed_by? int, path_recommendation_id? int

**stock_hold** · id* bigint, item_code text, location_code text, quality text, qty num, left_qty num, reason text, reference? text, source text, import_id? text, gr_no? text, gr_line_no? int, parent_id? bigint, held_by text, held_at timestamptz, closed_at? timestamptz

**stock_movement** · id* text, item_code text, location_code text, date date, type text, qty num, unit_cost? num, cost_centre_code? text, ref_doc? text, source text, quality text

**stock_opening** · item_code* text, location_code* text, qty? num, unit_cost? num, as_of? date

**supplier** · code* text, legal_name? text, trade_name? text, legal_form? text, pan? text, pan_status? text, gst_reg_type? text, gstin? text, state_code? text, e_invoice_applicable? bool, hsn_digits? text, msme_category? text, udyam_no? text, msme_agreement? bool, agreed_payment_days? int, payment_terms_code? text, tds_section_default? text, lower_tds_rate? num, lower_tds_valid_to? date, lower_tds_limit? num, reverse_charge? text, status? text, turnover_band? text, registered_by? text, invited_by? text, identity_checked_by? text, identity_checked_at? timestamptz, identity_method? text, identity_document_ids? ARRAY, identity_note? text

**supplier_address** · id* bigint, supplier_code? text, type? text, gstin? text, address? text, city? text, state_code? text, pincode? text

**supplier_bank_account** · id* bigint, supplier_code? text, bank_code? text, account_no? text, ifsc? text, verified? bool, verified_on? date, submitted_by? text, verification_method? text, evidence_document_id? bigint, verified_by? text, verified_at? timestamptz

**supplier_category** · supplier_code* text, category_code* text, approved_from? date

**supplier_category_scope** · id* bigint, supplier_code text, category_code text, location_code? text, region_code? text

**supplier_chase** · id* bigint, po_no text, kind text, how text, sent_at timestamptz, task_id? bigint, po_version? int

**supplier_contact** · id* bigint, supplier_code? text, name? text, role? text, email? text, phone? text, is_primary? bool

**supplier_document** · id* bigint, supplier_code? text, doc_type? text, number? text, issued_on? date, expires_on? date, verified? bool, added_by? text, verified_by? text, verified_at? timestamptz

**supplier_flag** · id* bigint, supplier_code text, kind text, other_ref? text, detail? text, raised_at timestamptz, cleared_by? text, cleared_at? timestamptz, clear_reason? text

**supplier_link** · id* bigint, po_no text, supplier_code text, purpose text, po_version int, issued_at timestamptz, expires_at timestamptz, used_at? timestamptz, used_for? text, sent_to? text

**supplier_message** · id* bigint, email_id bigint, linked_type text, linked_ref text, supplier_code? text, kind? text, status text, reading? jsonb, action? text, task_id? bigint, reply_draft? text, confidence? num, model? text, prompt_version? text, created_at timestamptz

**supplier_selection_rule** · id* bigint, version int, effective_from date, effective_to? date, method text, weight_price num, weight_delivery num, weight_quality num, weight_payment_terms num, tie_break text, input_credit bool, note? text, created_by? text, created_at timestamptz

**supplier_status_history** · id* bigint, supplier_code? text, at? timestamptz, from_status? text, to_status? text, reason? text, by_user? text

**supply_risk** · task_id* int, po_no text, mr_no text, location_code text, lines jsonb, buffer_days int, outcome? text, note? text, closed_by? text, closed_at? timestamptz

**task** · id* int, case_id? int, kind text, title text, assignee int, status text, sla_hours num, due_at timestamptz, escalation_chain jsonb, escalation_level int, payload? jsonb, created_at timestamptz, completed_at? timestamptz, completed_by? int

**tds_entry** · id* bigint, invoice_no? text, payment_no? text, section text, base num, rate num, amount num, booked_on date, quarter text

**tolerance** · id* bigint, key? text, value? text, category_code? text, note? text, version int

**tolerance_version** · version* int, effective_from date, effective_to? date, note? text, created_by? text, created_at timestamptz

**touchless_rule** · id* bigint, version int, effective_from date, effective_to? date, value_limit? num, price_pct num, note? text, created_by? text, created_at timestamptz

**unordered_receipt** · no* text, supplier_code text, item_code text, qty num, uom? text, location_code text, reason text, challan? text, serials jsonb, received_by text, posted_by text, received_on date, status text, proposal_id? bigint, decided_by? text, decided_at? timestamptz, decision_note? text, po_no? text, gr_no? text, returned_on? date, return_ref? text, created_at timestamptz

**uom_conversion** · item_code* text, from_uom* text, to_uom* text, factor? num

**user_limit** · id* int, user_id int, document text, category_code? text, location_code? text, max_value num

**user_profile** · user_id* int, display_name text, role text, created_at timestamptz

**user_role** · id* int, user_id int, role text, scope_location? text, scope_department? text

**vendor_statement** · id* bigint, supplier_code text, period_from date, period_to date, file_name? text, file_hash text, lines int, matched int, amount_differs int, only_theirs int, only_ours int, imported_by text, imported_at timestamptz

**vendor_statement_line** · id* bigint, statement_id bigint, row_no? int, doc_date? date, doc_type text, doc_no? text, their_amount? num, our_ref? text, our_amount? num, difference? num, outcome text, reason text

**workflow** · id* int, kind text, business_key text, capability text, status text, basis_hash? text, first_event_id? bigint, last_event_id? bigint, run_id? int, task_id? int, created_at timestamptz, updated_at timestamptz, closed_at? timestamptz, close_reason? text
