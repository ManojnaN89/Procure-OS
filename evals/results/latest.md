# Evals report

Run on 2026-11-23 03:24; AI evals used a scripted model.

| ID | What it checks | Result | Seconds | Key figures |
|---|---|---|---|---|
| E33 | The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job | **FAIL** | 16.4 |  |
| E34 | The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes | **FAIL** | 13.1 |  |
| E66 | A later masters import keeps second-person controls: status, bank, policies, versions, history, nothing deleted | **PASS** | 22.9 | first import: audit rows / mode 1 / ['first']; first import: tables whose rows differ from the file none; order bound to a contract price (po, price_record id) PO-CKN-2627-00001, 1; rows the company holds differently from the file, untouched by the scenario {'supplier_document': 1} |
| E67 | The app runs without owner rights: its own login, insert-only audit by grant, nothing it could switch off | **FAIL** | 13.6 |  |
| E68 | Held stock (quarantine, blocked) is never set aside, issued or counted; decisions hand usable stock on at once | **FAIL** | 12.7 |  |
| E69 | Late supply never covers an earlier need: at risk, not bought twice, one case for the buyer (Q17) | **FAIL** | 11.7 |  |
| E70 | Safety stock is never set aside or issued without the manager's approved override (C4) | **FAIL** | 12.5 |  |
| E71 | The store check runs every morning and after short-close, cancel, promise and rejection, never re-raising a rejection | **FAIL** | 11.9 |  |
| E72 | Replenishment counts only the stock part of open orders (DF-25): production orders never block a restock | **FAIL** | 12.1 |  |
| E73 | The planning agent explains shortages from tool results only; anything ungrounded falls back to the facts | **FAIL** | 12.9 |  |
| E74 | Two store checks and a count at once never set stock aside twice or buy twice (AT-09) | **FAIL** | 12.6 |  |
| E78 | Company states: setup, cutover, live, suspended; who may sign in; user creation in two databases is safe | **FAIL** | 19.9 |  |
| E79 | Setup is archived at cutover; live starts from a clean database with the configuration carried over | **FAIL** | 29.6 | setup test documents {'request': 'REQ-CKN-2627-00001', 'po': 'PO-CKN-2627-00001', 'receipt': 'GRN-CKN-2627-00001', 'invoice': 'PI-2627-000001'} |
| E80 | Readiness checks find every gap, including separation coverage, before a company may cut over or go live | **FAIL** | 51.0 | apex: requesters warned (platform / oracle) {} / {}; brightwave: requesters warned (platform / oracle) {} / {}; plants {'a change waiting': "new blockers ['pending_changes']", 'a band gap (REQ-02 from 20,000.00)': "new blockers ['approval_bands']", 'the buyer verifies only their own proposals': "new blockers ['separation']", 'mutation: the maker counted as their own checker': 'caught', 'planthead deactivated': 'new blockers []', 'purchasemanager deactivated': "new blockers ['duties']", 'director deactivated': 'new blockers []'}; mutation: one person's two Admin roles counted twice caught |
| E81 | Cutover import: open orders, receipts, invoices and advances loaded with checks, control totals, nothing twice | **FAIL** | 31.7 |  |
| E82 | Imports follow the company state; opening stock and bank accounts are locked after go-live | **FAIL** | 32.0 |  |
| E83 | A company goes from setup to live and pays its cutover invoices in the first live payment run | **FAIL** | 55.1 |  |
| E85 | The budget counts each request line once: at its estimate until ordered, then at the order's price | **FAIL** | 13.6 |  |
| E86 | Stock spends its budget when accepted at receipt, at the order price; services and charges when paid | **FAIL** | 14.9 |  |
| E87 | A locked budget month is frozen: nothing posts into it, it is locked in order, and late postings move on | **FAIL** | 13.6 |  |
| E88 | Year end locks the year, records the open commitments it carries, and the new year counts them | **FAIL** | 11.9 |  |
| E89 | Budget supplements, returns and transfers are proposals another finance person approves | **FAIL** | 12.4 |  |
| E90 | Forward-buy recommendations equal an independent oracle: trend, dip, cover, months, limits and money | **FAIL** | 14.3 |  |
| E91 | A forward buy is accepted by the buyer and approved by the person the rule names, as a new linked request | **FAIL** | 15.2 |  |
| E92 | Contract, capex, disabled and stale-priced items are never recommended; Brightwave's coffee saves too little | **FAIL** | 12.2 |  |
| E93 | The forward-buy explanation is the template with the record's own numbers, or an AI text that invents none | **FAIL** | 15.2 |  |
| E105 | An order's approval is of the version the approver saw; the approved version is recorded | **FAIL** | 11.8 |  |
| E106 | A material change after approval is approved again; anything else carries the approval (Q19) | **FAIL** | 12.9 |  |
| E107 | Changes add up from the approved version: dates, quantities, earlier dates | **FAIL** | 11.4 |  |
| E108 | Supplier price rises add up from the first approved price; above 2% the price approver decides | **FAIL** | 11.6 |  |
| E109 | A revised order is never touchless: a person approves the change | **FAIL** | 12.9 |  |
| E110 | Every decision from an email link needs a one-time code (D-07) | **FAIL** | 12.1 |  |
| E111 | Approving an order above Rs 5,00,000 needs a one-time code for that version | **FAIL** | 11.9 |  |

**1 of 33 passed.**

## Findings

- **high** (E33): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e33_flow_demo.py", line 57, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e33_flow_demo.py", line 76, in _run
    made = demo.reset_company(demo.company("apex"), dataset, credentials=credentials, slug=slug)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 274, in go_live
    raise DemoError(f"not ready: {', '.join(found.blockers)}")
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E34): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e34_flow_journey.py", line 185, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e34_flow_journey.py", line 226, in _run
    made = demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 274, in go_live
    raise DemoError(f"not ready: {', '.join(found.blockers)}")
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E67): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e67_app_without_owner_rights.py", line 244, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e67_app_without_owner_rights.py", line 267, in _run
    demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 274, in go_live
    raise DemoError(f"not ready: {', '.join(found.blockers)}")
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E68): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e68_held_stock.py", line 52, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e68_held_stock.py", line 67, in _run
    apex = s2.Co("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E69): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e69_late_supply.py", line 53, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e69_late_supply.py", line 68, in _run
    apex = s2.Co("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E70): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e70_safety_stock.py", line 50, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e70_safety_stock.py", line 65, in _run
    apex = s2.Co("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E71): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e71_store_check_job.py", line 49, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e71_store_check_job.py", line 64, in _run
    apex = s2.Co("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E72): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e72_restock_stock_part.py", line 41, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e72_restock_stock_part.py", line 61, in _run
    co = s2.Co("apex", PREFIX, client)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E73): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e73_planning_explains.py", line 102, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e73_planning_explains.py", line 117, in _run
    apex = s2.Co("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E74): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e74_concurrent_checks.py", line 39, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e74_concurrent_checks.py", line 53, in _run
    co = s2.Co("apex", PREFIX, client)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E78): company cutover: exit 1, state setup: not ready: approvers

- **high** (E79): company cutover --dry-run: exit 1: not ready: approvers

- **high** (E79): company cutover: exit 1, state setup: not ready: approvers

- **high** (E79): one tenant_archive row expected: []
- **high** (E80): apex: setup blockers ['approvers']
- **high** (E80): apex: blockers of all groups ['approvers', 'cutover_import']
- **high** (E80): apex: plain readiness of the setup group: exit 1: ok    masters  Master data loaded
ok    admins  Two administrators
ok    approval_bands  Approval bands cover every value
FAIL  approvers  Every approval role names someone who may approve
        FWD-01: purchase_manager (Purchase Manager) is Sanjay Rao, who may not approve it
ok    approvers  Ever
- **high** (E80): brightwave: setup blockers ['approvers']
- **high** (E80): brightwave: blockers of all groups ['approvers', 'cutover_import']
- **high** (E80): brightwave: plain readiness of the setup group: exit 1: ok    masters  Master data loaded
ok    admins  Two administrators
ok    approval_bands  Approval bands cover every value
FAIL  approvers  Every approval role names someone who may approve
        FWD-01: cfo (Chief Financial Officer) is Farah Khan, who may not approve it
ok    approvers  Every requ
- **high** (E80): the main demo company has setup blockers before any plant: ['approvers']
- **high** (E81): apex: company cutover exit 1: not ready: approvers

- **high** (E81): brightwave: company cutover exit 1: not ready: approvers

- **high** (E82): company cutover exit 1: not ready: approvers

- **high** (E83): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e83_setup_to_live.py", line 64, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e83_setup_to_live.py", line 80, in _run
    co = Company.make("e83d", profile, state=None)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block0d.py", line 275, in make
    demo.reset_company(demo.company(profile), dataset, credentials=None, slug=slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 274, in go_live
    raise DemoError(f"not ready: {', '.join(found.blockers)}")
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E85): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e85_budget_document_in_force.py", line 49, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e85_budget_document_in_force.py", line 62, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E86): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e86_stock_budget_at_receipt.py", line 45, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e86_stock_budget_at_receipt.py", line 58, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E87): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e87_period_locks.py", line 45, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e87_period_locks.py", line 82, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E88): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e88_year_end.py", line 47, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e88_year_end.py", line 60, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E89): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e89_budget_adjustments.py", line 43, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e89_budget_adjustments.py", line 56, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E90): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e90_forward_buy_numbers.py", line 54, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e90_forward_buy_numbers.py", line 67, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E91): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e91_forward_buy_decision.py", line 50, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e91_forward_buy_decision.py", line 79, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E92): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e92_forward_buy_exclusions.py", line 46, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e92_forward_buy_exclusions.py", line 59, in _run
    bw = b3.Co3("brightwave", PREFIX, client)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E93): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e93_forward_buy_explanation.py", line 85, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e93_forward_buy_explanation.py", line 98, in _run
    apex = b3.Co3("apex", PREFIX, client)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\backend\p2p\platform\demo.py", line 265, in reset_company
    go_live(slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E105): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e105_approval_of_version_seen.py", line 40, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e105_approval_of_version_seen.py", line 56, in _run
    bw = k.Co("brightwave", PREFIX, client, box)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block4.py", line 416, in __init__
    super().__init__(profile, prefix, client)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E106): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e106_material_change_reapproved.py", line 45, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e106_material_change_reapproved.py", line 61, in _run
    bw = k.Co("brightwave", PREFIX, client, box)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block4.py", line 416, in __init__
    super().__init__(profile, prefix, client)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E107): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e107_changes_add_up.py", line 39, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e107_changes_add_up.py", line 55, in _run
    bw = k.Co("brightwave", PREFIX, client, box)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block4.py", line 416, in __init__
    super().__init__(profile, prefix, client)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E108): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e108_cumulative_price_rise.py", line 44, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e108_cumulative_price_rise.py", line 60, in _run
    bw = k.Co("brightwave", PREFIX, client, box)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block4.py", line 416, in __init__
    super().__init__(profile, prefix, client)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E109): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e109_revised_not_touchless.py", line 38, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e109_revised_not_touchless.py", line 54, in _run
    bw = k.Co("brightwave", PREFIX, client, box)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block4.py", line 416, in __init__
    super().__init__(profile, prefix, client)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E110): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e110_every_link_decision_needs_code.py", line 52, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e110_every_link_decision_needs_code.py", line 68, in _run
    bw = k.Co("brightwave", PREFIX, client, box)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block4.py", line 416, in __init__
    super().__init__(profile, prefix, client)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
p2p.platform.demo.DemoError: not ready: approvers

- **high** (E111): the eval itself crashed:
Traceback (most recent call last):
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\run.py", line 82, in main
    outcome: Outcome = mod.run(ctx)
                       ^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e111_po_approval_code.py", line 44, in run
    return _run(ctx)
           ^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\e111_po_approval_code.py", line 60, in _run
    bw = k.Co("brightwave", PREFIX, client, box)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block4.py", line 416, in __init__
    super().__init__(profile, prefix, client)
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 942, in __init__
    self.reset()
  File "C:\CBC\PR to PO\.claude\worktrees\p3-b2\evals\block1.py", line 956, in reset
    demo.reset_company(demo.company(self.name), self.dataset, credentials=folder / "credentials", slug=self.slug)
p2p.platform.demo.DemoError: not ready: approvers


## Details

### E33 The main demo company completes the whole flow, from low stock to a paid invoice, with separate people for each job

Checks: Phase 1: request, approval, price or quotation, order with GST, receipt, invoice match, payment run, closing; requester, approver, receiver, invoice enterer and payer differ for the same case; Rs 10,000 approves itself, up to Rs 1,00,000 the plant head, above that the director; orders above Rs 1,00,000 the purchase manager; payment runs the director


### E34 The whole application for a real user: the demo script from low stock to a paid invoice in a browser, every role guide followed as a new user, and every screen swept for errors, overflow and raw codes

Checks: Phase 1 acceptance: the flow works end to end for real people at real screens; a new user completes their first task from the guide; no screen shows an error, a blank panel, a raw code or sideways scrolling


### E66 A later masters import keeps second-person controls: status, bank, policies, versions, history, nothing deleted

Checks: Task 0.6, DF-16, D-22, rule 8 (block 0c spec 3.1-3.5): the first import loads everything; any later import matches rows by key, never deletes, never changes a supplier's status or an existing supplier's bank details, adds new suppliers as drafts with unverified accounts, leaves out possible duplicates, never applies policies, approval rules or opening stock, adds price versions and contracts as drafts, keeps approvers; every added or changed row audited; a dry run writes nothing; a repeat changes nothing; tenant isolation. Decisions of 22 Nov: the admin API makes no Admin login and gives no role or limit without a second administrator

- first import: audit rows / mode: 1 / ['first']
- first import: tables whose rows differ from the file: none
- order bound to a contract price (po, price_record id): PO-CKN-2627-00001, 1
- rows the company holds differently from the file, untouched by the scenario: {'supplier_document': 1}
- dry run: exit code / first line: 1 / update by key (dry run, nothing written): would add 6, would change 4, unchanged 690, not in the file 13 (kept), in 0.22 s; 4 left out, 14 warnings
- import: exit code / first line: 1 / update by key: added 6, changed 4, unchanged 690, not in the file 13 (kept), in 0.38 s; 4 left out, 14 warnings
- oracle delta: added / changed / removed: {'supplier': 1, 'supplier_address': 1, 'supplier_bank_account': 1, 'supplier_contact': 1, 'contract': 1, 'price_record': 1} / {'department': 1, 'item': 1, 'supplier': 1, 'supplier_contact': 1} / none
- warnings expected / printed / missing: 13 / 14 / 0
- controls after the import (spec 3.5): kept
- audit: imports / row_add / row_change: 1 / 6 / 4
- the same copy again: first line: update by key: added 0, changed 0, unchanged 700, not in the file 13 (kept), in 0.42 s; 4 left out, 14 warnings
- admin API (decisions of 22 Nov): {'Admin login': 'refused (403)', 'role': 'proposed; maker approves 403, other admin 200; 0 -> 1', 'limit': 'proposed; maker approves 403, other admin 200; None -> 50000.00', 'own role': '403', 'own limit': '403'}
- mutation checks (rule broken on purpose: caught?): {'the import applies supplier.status from the file': 'caught in the database', "a new supplier's bank account keeps verified=true": 'caught in the database', 'the later import deletes and re-inserts': 'caught in the database'}
- note: rows the company held differently from the file before the import: {'supplier_document': [('SUP-002', 'cancelled_cheque', 'CHQ-0002')]}

### E67 The app runs without owner rights: its own login, insert-only audit by grant, nothing it could switch off

Checks: Task 1.7, D-25, DF-37 (block 0c spec 4.1-4.5, 5): every runtime path (API, worker, CLI, engines) signs in as the app login, which is no superuser, makes no database or role, bypasses no row security and owns nothing; it may select, insert, update and delete business tables, only select and insert audit_event and setup_change, only select alembic_version and the control tenant tables; every forbidden statement fails with 42501 and changes nothing; stored tenant URLs hold no password; health says restricted; the owner still migrates, and tables it makes later reach the app by default privileges


### E68 Held stock (quarantine, blocked) is never set aside, issued or counted; decisions hand usable stock on at once

Checks: Task 4.1 (block 2 spec 3, 9.3 Q1-Q3): three qualities; held stock never reserved, issued or counted; an inspection receipt waits in quarantine and is not invoiceable; rejected at inspection goes to blocked; a manual hold of set-aside stock gives back the latest need first, then the store check buys; a decision to usable sets stock aside at once; holds API fields and errors; audit stock.hold / stock.hold_decide; ledger equals the quality view; tenant isolation


### E69 Late supply never covers an earlier need: at risk, not bought twice, one case for the buyer (Q17)

Checks: Task 6.1, Q17 (block 2 spec 4, 9.3 T1-T4, BT1-BT2): supply counts only if due by the latest arrival (need-by less the buffer in working days on the location calendar; a passed date is today); free stock goes first to short + at risk; only the short part is bought; one late_supply case per PO and material request (owner the buyer, escalation to the manager, due late_supply_case_days, buffer recorded), resolved by the system, closing rules; Exceptions row; tenant isolation


### E70 Safety stock is never set aside or issued without the manager's approved override (C4)

Checks: Task 6.2, C4 (block 2 spec 5, 9.3 S1-S2): free = usable - set aside - safety stock; a manual issue above that is 409 with needs_override, available and safety_stock; an override is a proposal approved only by the asker's manager chain holding request.approve (never the asker); approved, it issues or sets aside and cuts the open request; safety stock changes wait for a second administrator; replenishment after the issue; audit; tenant isolation


### E71 The store check runs every morning and after short-close, cancel, promise and rejection, never re-raising a rejection

Checks: Task 6.5 (block 2 spec 6, 9.3 E1-E2, decision OP-14 of 22 Nov): job store_check daily 05:30 IST when auto_store_check, drafts unless store_check_submit, always queues the next run, audited, idempotent; events (close-balance, cancel, acknowledgement, rejection) re-run evaluation and a scoped store check; a rejected shortfall is not re-raised automatically but is by the button; tenant isolation


### E72 Replenishment counts only the stock part of open orders (DF-25): production orders never block a restock

Checks: Task 6.7, DF-25 (block 2 spec 7, 9.3 D1): an open PO line counts as on order for stock only by its request line's stock part (base qty less linked material request quantities); a line with no request line counts fully; a restock order still blocks a second restock; a second run makes nothing; tenant isolation


### E73 The planning agent explains shortages from tool results only; anything ungrounded falls back to the facts

Checks: Task 11b.1, D49, rule 2 (block 2 spec 8): six read-only tools; an explanation is accepted only if its schema is valid, every short or at-risk line is present, each line's causes equal the engine's, and every number, date and document number appears in a tool result (no small-number exemption), else facts only; off by default (no model call), switched on by two administrators, kill switch; one explanation per snapshot; traced; permission and tenant


### E74 Two store checks and a count at once never set stock aside twice or buy twice (AT-09)

Checks: AT-09 (part), block 2 spec 6 and 11: concurrent demand and reservations: no double reservation or buying; set aside never above usable - safety per place; one purchase request per material request; the result equals the serial one


### E78 Company states: setup, cutover, live, suspended; who may sign in; user creation in two databases is safe

Checks: Task 7.1, D-04 (states Setup / Cutover / Live / Suspended and what each allows), D-22 (go-live by two administrators), DF-36 (user creation writes both databases safely; no Admin login made by one administrator)


### E79 Setup is archived at cutover; live starts from a clean database with the configuration carried over

Checks: Task 7.2, D-05 (testing in setup, archived at cutover; cutover rebuilds a clean live database), OP-6 (new database, the test database kept as an archive), OP-7 (what is carried), OP-22 (queued jobs cancelled)

- setup test documents: {'request': 'REQ-CKN-2627-00001', 'po': 'PO-CKN-2627-00001', 'receipt': 'GRN-CKN-2627-00001', 'invoice': 'PI-2627-000001'}

### E80 Readiness checks find every gap, including separation coverage, before a company may cut over or go live

Checks: Task 7.3 (readiness checks including separation coverage: every rule that needs a second or different person has enough eligible people), Plan line 1086 (go-live readiness requires two administrators), FR32 (five different people for the five duties)

- apex: requesters warned (platform / oracle): {} / {}
- brightwave: requesters warned (platform / oracle): {} / {}
- plants: {'a change waiting': "new blockers ['pending_changes']", 'a band gap (REQ-02 from 20,000.00)': "new blockers ['approval_bands']", 'the buyer verifies only their own proposals': "new blockers ['separation']", 'mutation: the maker counted as their own checker': 'caught', 'planthead deactivated': 'new blockers []', 'purchasemanager deactivated': "new blockers ['duties']", 'director deactivated': 'new blockers []'}
- mutation: one person's two Admin roles counted twice: caught
- checks: 58 of 65 passed
- note: spec gap: section 6 expects warnings for ['Meera Iyer'] on apex; the rules of 4.3 applied to the dataset give [] (resolve_chain refuses a request whose every approver is the requester)
- note: spec gap: section 6 expects warnings for ['Farah Khan', 'Vivek Menon'] on brightwave; the rules of 4.3 applied to the dataset give [] (resolve_chain refuses a request whose every approver is the requester)

### E81 Cutover import: open orders, receipts, invoices and advances loaded with checks, control totals, nothing twice

Checks: Task 7.4 (cutover import of open POs, receipts, invoices and advances, with checks), OP-9 (part-paid invoices refused), OP-10 (receipts post no stock), OP-11 (invoices re-matched by the platform's rules), OP-12 (year-to-date purchases for tax at source), OP-13 (carrier request per PO)


### E82 Imports follow the company state; opening stock and bank accounts are locked after go-live

Checks: Task 7.6, D-02 (imports depend on company state; opening stock and verified accounts locked after go-live), Base WP-7 (imports refused after go-live), block 0c update mode underneath


### E83 A company goes from setup to live and pays its cutover invoices in the first live payment run

Checks: Task 7.7 (evals: Setup to Live), Base WP-7 (a company goes Setup to Live; imports refused after go-live), D-04, D-05, D-22 (go-live by two administrators), OP-12 (year-to-date purchases for tax at source), plan acceptance gate 9 (the pilot client rehearses cutover)


### E85 The budget counts each request line once: at its estimate until ordered, then at the order's price

Checks: Task 3.7, FR27 (block 3 spec 3.3 BG0/BG1, 4.1, 4.5, 4.6): used = held by requests (open or sourcing lines of submitted or approved requests) + held by orders (open order balance before GST, less actuals) + spent; the request checked excluded; final prices; variance left = budget - the three parts, per location too; a cancelled order puts the estimate back; GET /api/budgets/check fields, 404, 403; tenant isolation


### E86 Stock spends its budget when accepted at receipt, at the order price; services and charges when paid

Checks: D-10 (block 3 spec 3.3 BG1, 4.1, 4.2): a stocked line's actual = accepted x order price in the acceptance month (source receipt), nothing at gate entry, never the rejected part; held by orders falls by the actual; a balance closed holds nothing; payment adds only charges for stocked lines and the taxable value of a service in the invoice's month; audit budget.actual once per receipt or invoice; tenant isolation


### E87 A locked budget month is frozen: nothing posts into it, it is locked in order, and late postings move on

Checks: D-09 (block 3 spec 3.3 PL1, 4.3, 4.4, 4.6, 4.7): lock only ended months, in order, once, with a reason, by budget.manage; a late actual goes to the first open month keeping event_period (audit budget.actual_moved); adjustments and imported budget rows for a locked month are refused; periods API and CLI; tenant isolation


### E88 Year end locks the year, records the open commitments it carries, and the new year counts them

Checks: D-09, OP-5 (block 3 spec 3.3 YE1, 4.3, 4.6, 4.7): close allowed from 1 April by budget.manage; locks every month of the FY not yet locked; carried per GL, cost centre, location (held by requests, held by orders) and total; 409 twice, 422 early or for a later FY; GET years/{fy} = the close; open commitments count in the new FY, unspent budget lapses; CLI close-year; audit budget.year_close; tenant isolation


### E89 Budget supplements, returns and transfers are proposals another finance person approves

Checks: D-09, OP-8 (block 3 spec 4.3, 4.6): POST /api/budgets/adjustments by budget.manage -> 201 pending proposal; the proposer never approves; on approval `budget` rows of kind adjustment with reason and proposal id (a transfer two rows summing 0); open periods only; 422 for bad amounts, holders, GL, reason; the check and the variance read the new budget; audit budget.adjust; tenant isolation


### E90 Forward-buy recommendations equal an independent oracle: trend, dip, cover, months, limits and money

Checks: Task 13.5 (block 3 spec 3.4 FB1-FB5, FB8, 5.1-5.4, 5.6): price now = newest counting benchmark; trend = least squares of 12 monthly averages; dip above min_gap_pct; cover from usable stock and open supply; months 1..max_cover with net per unit above 0; storage and budget limits; whole packs from the latest month; recommend only when net > min_net_saving_inr; one live record per item and location (superseded, lapsed); every evaluation in rule_evaluation; preview writes nothing; tenant isolation


### E91 A forward buy is accepted by the buyer and approved by the person the rule names, as a new linked request

Checks: Task 13.5 (block 3 spec 3.4 FB6, 5.4, 5.6): accept (po.create) with a reason and whole packs up to the recommendation; a forward_buy.approve proposal and task for the forward_buy rule's person, never the buyer; revalidation 409 with new_id; approval writes a planning request (stock part all, anchor's need-by, department, estimate) through the normal chain and budget check; the anchor never grows; reject with a reason, not offered again until a newer benchmark; audit; tenant isolation


### E92 Contract, capex, disabled and stale-priced items are never recommended; Brightwave's coffee saves too little

Checks: Task 13.5 (block 3 spec 3.4 FB7/FB8, 5.1-5.3, 7): eligibility (stocked goods, not capex, rule enabled, no contract price in force), no_fresh_benchmark without a delivered fresh benchmark, not_below_trend, saving_too_small, shelf life; the preview writes nothing; a forward-buy request counts as stock on order for replenishment; tenant isolation


### E93 The forward-buy explanation is the template with the record's own numbers, or an AI text that invents none

Checks: Task 13.5, rule 2 (block 3 spec 5.5): the template sentence with numbers only from the record; the AI step (one call, summary <= 600 characters, at most 3 risks) passes an evidence check of every number and date or the template stays; explanation_source template or ai; audit; tenant isolation


### E105 An order's approval is of the version the approver saw; the approved version is recorded

Checks: Task 0.2, DF-07 (block 4 spec 4.1 rules 1-3, 4.3, V1): each step carries the version of its round; approve and reject with a stale version are 409 and write nothing; approving records po.approved_version and one po_approved_version snapshot (total, band, rule, approver, lines) in the same step; audit po.approve with version and with_code; omitted version approves the current one; amounts against the oracle and the truth file; tenant check


### E106 A material change after approval is approved again; anything else carries the approval (Q19)

Checks: Task 0.2, Q19, AT-08 (block 4 spec 4.1 rules 4-7 and 9, V2, V2b): a change is measured from the last approved version; material means approval again (submitted, ack cleared, new round with the new version, reasons and baseline on the revision); otherwise the approval carries (approved_version unchanged, ack cleared, new respond link); sending a version changed materially after approval is refused; tenant check


### E107 Changes add up from the approved version: dates, quantities, earlier dates

Checks: Task 0.2, DF-07, OP-2, OP-5, OP-22 (block 4 spec 4.1 rule 4, 4.3, V3): every revision is compared with the last approved version; two +2 day slips make the second material; a quantity back below the approved one is not; an earlier date beyond tolerance is; +2 then -2 is not; reasons on the revision and in the history; tenant check


### E108 Supplier price rises add up from the first approved price; above 2% the price approver decides

Checks: DF-08, OP-3, OP-16 (block 4 spec 4.2, 4.3, V4, V4b): a supplier's price is judged exactly from the first approved price; the buyer alone accepts up to supplier_price_pct in all; above it the buyer's acceptance makes the po.supplier_price proposal valued (new - base) x quantity for the price_variance person; C1 kept; base_value and rise_pct shown; amounts against the oracle and the truth file; tenant check


### E109 A revised order is never touchless: a person approves the change

Checks: Rev s5, OP-7 (block 4 spec 4.1 rules 3 and 8, V5): a touchless order's approval by the rule records its first approved version ('rule:PO-01', the contract price); after a material revision the touchless tests run again, touchless.revision fails with its text, the order is not approved by the rule but waits for the next band's person (the purchase manager), who approves the new version; tenant check


### E110 Every decision from an email link needs a one-time code (D-07)

Checks: D-07, OP-9, OP-10 (block 4 spec 5.1, O1): approve, reject and return from /approve/{token} each need a valid code whatever the amount; the code is mailed only to the login the link names and never shown on the page or in another mail; wrong codes count down and lock after five; a code works once, for one step of one round for one person; expired codes are refused; the email and page carry the code note and the app link; audit otp.* and 'emailed link'; Apex and Brightwave; a link re-signed for another company decides nothing; tenant check


### E111 Approving an order above Rs 5,00,000 needs a one-time code for that version

Checks: Task 1.2, D-07, OP-11, OP-14, OP-15 (block 4 spec 5.2, O2, O2b): needs_code = total with GST above approval_otp_above; approve without, with a wrong or another version's code is 409 and writes nothing; the code goes only to the person whose turn it is, ref <po>:<version>:<round>:<step>, resend gap 30 s; the right code approves (with_code true, otp.use); reject needs none; at or below the limit no code (409 'needs no code'); totals against the oracle and the truth file; tenant check

