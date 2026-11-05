# Indian tax and compliance reference facts (P2P dataset, Nov 2024 to Oct 2026)

Compiled 2026-11-05 by web research. Each fact gives its source and effective date.
**Status tags:** CONFIRMED (official text read), CONFIRMED-SEC (only a secondary source was readable; the source is named), UNCONFIRMED (not verified; do not rely on it).

Official sources that blocked automated fetching (HTTP 403 or captcha): incometaxindia.gov.in (whole site), indiabudget.gov.in through WebFetch (plain download worked), docs.ewaybillgst.gov.in through WebFetch (plain download worked), rbidocs.rbi.org.in PDFs (captcha), indiacode.nic.in (504). Where one of these was the primary source, a gazette copy hosted elsewhere or a secondary source was used. Each such case is marked.

Key official documents used:

- **[ITA25]** Income-tax Act, 2025 (Act 30 of 2025), Gazette of India Extraordinary No. 35, 21 Aug 2025. https://egazette.gov.in/WriteReadData/2025/265620.pdf
- **[FA25]** Finance Act, 2025 (Act 7 of 2025), Gazette Extraordinary No. 7, 29 Mar 2025 (gazette ID 262125). Copy hosted on a government site: https://thc.nic.in/Central%20Governmental%20Acts/Finance%20Act,%202025.pdf
- **[MEMO25]** Memorandum explaining Finance Bill 2025. https://www.indiabudget.gov.in/budget2025-26/doc/memo.pdf
- **[MEMO24]** Memorandum explaining Finance (No.2) Bill 2024 (July 2024). https://www.indiabudget.gov.in/budget2024-25/doc/memo.pdf

---

## A. TDS on P2P payments (1961 Act up to 31-Mar-2026; Income-tax Act 2025 from 01-Apr-2026)

### A.1 Transition

| Fact | Effective | Source | Status |
|---|---|---|---|
| ITA 2025 received assent on 21-Aug-2025. Section 536(1): "The Income-tax Act, 1961 is hereby repealed." | Act in force from 01-Apr-2026 | [ITA25] s.536 | CONFIRMED (assent and repeal text). The 01-Apr-2026 date is from secondary reports, which agree with each other. |
| Non-salary TDS for residents is in s.393(1), one Table ("FOR PAYMENTS TO RESIDENT", columns A to D: Sl. No. / nature / payer / rate and threshold). Non-residents are in s.393(2). "No deduction" cases are in s.393(4). TCS is in s.394. | 01-Apr-2026 | [ITA25] s.393, s.394 | CONFIRMED |
| Savings clause s.536(2)(j): any circular, notification, etc. issued under the repealed Act continues, so far as it is not inconsistent, as if issued under the corresponding provision of the new Act. This keeps CBDT Circulars 23/2017 and 13/2021 alive. | 01-Apr-2026 | [ITA25] s.536(2)(j) | CONFIRMED |
| Finance Act 2026 (Act 4 of 2026, assent 30-Mar-2026) amends s.402(47) so that "work" expressly includes supply of manpower. Such payments then fall under s.393(1) Sl. 6(i) at 1%/2%, not under 6(iii). | 01-Apr-2026 | https://studycafe.in/budget-2026-manpower-supply-now-treated-as-contractual-work-for-tds-purposes-408799.html ; https://cleartax.in/s/tds-and-tcs-changes-from-april-2026 | CONFIRMED-SEC. The enacted wording of the clause was not read. |
| Timing, new Act. s.393(1)(c): deduct "at the time of credit of such income or sum to the account of the payee or at the time of its payment in cash or by way of a cheque or a draft or by any other mode, whichever is earlier". s.393(1)(a): deduct on the **entire amount** once the threshold is exceeded. The exception is 8(ii) (194Q successor), which applies only to the sum exceeding 50 lakh. | 01-Apr-2026 | [ITA25] s.393(1) | CONFIRMED |
| Timing, old Act. The same "credit or payment, whichever is earlier" rule applies in ss.194C, 194H, 194-I, 194J and 194Q. | to 31-Mar-2026 | MEMO24 p.~50 quotes this wording for 194H. The other sections were not read in the official text because incometaxindia.gov.in was blocked. | CONFIRMED for 194H. CONFIRMED-SEC for the rest. |

### A.2 Section-by-section (old to new mapping)

Definitions used by the new Act ([ITA25] s.402):

- **"Specified person"** (s.402(37)): (a) any person other than an individual or HUF; or (b) an individual or HUF whose turnover in the preceding tax year exceeded ₹1 crore (business) or ₹50 lakh (profession). This mirrors the old payer test in 194H, 194-I and 194J.
- **"Designated person"** (s.402(11)): the payer for Sl. 6(i). It covers Central and State Government, local authorities, statutory corporations, any company, co-operative society, housing/planning authority, registered society, trust, university, and so on. (The rest of the list was cut off in the extract.)
- **"Buyer"** (s.402(6) Table Sl. 1): for Sl. 8(ii), a person whose total sales, gross receipts or turnover from business exceeded ₹10 crore in the preceding tax year. Persons notified by the Central Government are excluded.

| Old (1961) | New (2025) ref | Nature | Rate | Threshold old (to 31-Mar-2025) | Threshold old (01-Apr-2025 to 31-Mar-2026) | Threshold new (from 01-Apr-2026) | Status |
|---|---|---|---|---|---|---|---|
| 194Q | s.393(1) Table Sl. **8(ii)**, Note 1 | Purchase of goods by a buyer with turnover > ₹10 cr, from a resident seller | 0.1% of the sum **exceeding ₹50 lakh** in the year | ₹50 lakh aggregate per seller per year | same | Same: "Any sum exceeding fifty lakh rupees for purchase of any goods". Note 1(a): does not apply where tax is deductible or collectible under any other provision. Note 1(b): deduct on the sum exceeding ₹50 lakh. | CONFIRMED (new: [ITA25]; old rate and threshold: MEMO24 p.60, MEMO25) |
| 194C | s.393(1) Sl. **6(i)** | Work contract (incl. supply of labour) paid by a "designated person" | (a) **1%** if the contractor is an individual or HUF; (b) **2%** otherwise | ₹30,000 single sum / ₹1,00,000 aggregate in the FY | unchanged | (a) ₹30,000 for any such sum; (b) ₹1,00,000 aggregate | New: CONFIRMED. Old rates: CONFIRMED (MEMO24 p.51). Old thresholds: CONFIRMED-SEC (ClearTax rate chart). |
| 194C note | Sl. 6(i) Note; s.402(47)(e) | Manufacturing to the customer's specification using the customer's material | TDS on invoice value **excluding material** if material is shown separately, otherwise on the whole invoice | same rule in old 194C | | | CONFIRMED (new) |
| 194C(6) | s.393(4) Table Sl. 8 | Transport contractor owning ≤10 goods carriages who gives a declaration and PAN | No TDS | | | same | CONFIRMED (new) |
| 194C "work" | s.402(47)(B) | "Work" excludes sums covered by 194J (new: Sl. 6(iii)) | | **From 01-Oct-2024** (MEMO24 p.51-52) | | same | CONFIRMED |
| 194J(a) | s.393(1) Sl. **6(iii)** rate (a) | Fees for technical services (not professional), film royalty, and payee operating only a call centre | **2%** | ₹30,000 per category per FY | ₹50,000 | ₹50,000 (for (a) fees for professional services, (b) FTS, (d) royalty, (e) s.26(2)(h) sums) | CONFIRMED (new: [ITA25]; old change: FA25 s.65, MEMO25) |
| 194J(b) | s.393(1) Sl. **6(iii)** rate (b) | Professional fees, other royalty, non-compete sums | **10%** | ₹30,000 | ₹50,000 | ₹50,000 | CONFIRMED |
| 194J director | Sl. 6(iii)(c) | Director's remuneration not taxed as salary | 10% | Nil | Nil | Nil | CONFIRMED (new). Old: CONFIRMED-SEC. |
| 194-I(a) | s.393(1) Sl. **2(ii)(a)**, payer = specified person | Rent of plant, machinery or equipment | **2%** | ₹2,40,000 per FY | **₹50,000 per month or part of a month** | ₹50,000 per month or part of a month | CONFIRMED (new: [ITA25]; old change: FA25 s.64, MEMO25) |
| 194-I(b) | s.393(1) Sl. **2(ii)(b)** | Rent of land, building (incl. factory building), land appurtenant, furniture or fittings | **10%** | ₹2,40,000 per FY | ₹50,000 per month or part | ₹50,000 per month or part | CONFIRMED (old rates 2%/10%: CONFIRMED-SEC, ClearTax) |
| 194-IB | Sl. 2(i), payer other than a specified person | Rent paid by small individuals or HUFs | 2% | | ₹50,000 per month | ₹50,000 per month; deducted once, for the last month of the year or tenancy | CONFIRMED (new) |
| 194H | s.393(1) Sl. **1(ii)**, payer = specified person | Commission (not insurance) or brokerage | **2%**. Was 5% until 30-Sep-2024; 2% **from 01-Oct-2024** (MEMO24). | ₹15,000 per FY | ₹20,000 | ₹20,000 | CONFIRMED (FA25 s.63; MEMO24 p.~50; [ITA25]) |
| 194M | Sl. 6(ii) | Individual or HUF (not otherwise liable) paying contractors, professionals or commission | 2% | ₹50 lakh | ₹50 lakh | ₹50 lakh | CONFIRMED (new) |
| 206AA | **s.397(2)(b)(i)** | PAN not furnished | Old: higher of the section rate, the rates in force, or **20%**; **5%** for 194Q and 194-O. New: higher of (A) the rate in the Act, (B) the rates in force, or (C) **5%** for Sl. 8(ii)/8(v) and **20% in any other case**. | | | | New: CONFIRMED. Old: CONFIRMED-SEC (incometaxindia tutorial and ClearTax snippet; official text blocked). |
| 206AA, inoperative PAN | ITA25 s.262(6)(b) makes PAN inoperative if Aadhaar is not intimated | A PAN made inoperative (rule 114AAA; CBDT Circular 3/2023 dated 28-Mar-2023) is treated as not furnished, so the higher rate applies from 01-Jul-2023. Relief: Circulars 6/2024 and 9/2025 (no higher-rate liability if the payee links PAN and Aadhaar within the window; 9/2025 covers payments from 01-Apr-2024 to 31-Jul-2025). | | | | | CONFIRMED-SEC (taxguru/taxmann summaries). The new-Act equivalent of the inoperative-PAN consequence rule was not traced (UNCONFIRMED). |
| 206AB | none (no equivalent in ITA 2025) | Higher TDS for "specified person" non-filers: higher of twice the section rate, twice the rate in force, or 5% | applied up to 31-Mar-2025 | **Omitted w.e.f. 01-Apr-2025** (FA25 s.71: "Section 206AB of the Income-tax Act shall be omitted.") | n/a | CONFIRMED (omission). The rate formula is CONFIRMED-SEC. |

### A.3 Tax base excluding GST

- **CBDT Circular No. 23/2017**, F.No. 275/59/2012-IT(B), dated **19-Jul-2017**, para 4: "wherever in terms of the agreement or contract between the payer and the payee, the component of 'GST on services' comprised in the amount payable to a resident is indicated separately, tax shall be deducted at source ... on the amount paid or payable without including such 'GST on services' component." GST here means IGST, CGST, SGST and UTGST.
  - Effective 01-Jul-2017. It continues under ITA 2025 via s.536(2)(j).
  - Source: scanned circular, OCR-read: https://itatonline.org/info/wp-content/uploads/2017/07/CBDT-Circular-23-TDS-GST.pdf. The official copy on incometaxindia.gov.in was blocked.
  - CONFIRMED. The text was read from a facsimile of the signed circular.
  - The circular covers **services** paid to **residents** only.
- **Goods under 194Q: CBDT Circular 13/2021 (30-Jun-2021).** GST is excluded when TDS is made on credit and GST is shown separately. On an **advance payment**, TDS applies to the whole amount including GST. Source: https://taxguru.in/income-tax/summary-cbdt-circular-no-13-2021-section-194q.html. CONFIRMED-SEC.

---

## B. TCS u/s 206C(1H) on sale of goods

| Fact | Effective | Source | Status |
|---|---|---|---|
| 206C(1H): a seller with turnover > ₹10 cr collects 0.1% of sale consideration **exceeding ₹50 lakh** received from a buyer in the year. | in force up to 31-Mar-2025 | MEMO24 p.60 ("collect tax at the rate of 0.1% of such consideration exceeding fifty lakh rupees") | CONFIRMED (seller turnover test is CONFIRMED-SEC) |
| Interaction before April 2025: 206C(1H) did not apply if the buyer was liable to deduct TDS under any other provision (in practice 194Q) on the goods and **had deducted** it. 194Q(5)(b) excluded transactions on which TCS was collectible under 206C, "other than a transaction to which sub-section (1H) of section 206C applies". So **194Q took priority** and TCS applied only where the buyer did not deduct. | to 31-Mar-2025 | MEMO25 para XIII.4; FA25 s.69 | CONFIRMED |
| **Omitted from 01-Apr-2025.** FA25 s.72(c) inserts a proviso into 206C(1H): "nothing contained in the provisions of this sub-section shall apply from the 1st day of April, 2025". The 194Q(5)(b) carve-out wording was deleted at the same time (FA25 s.69). | 01-Apr-2025 | [FA25] ss.69, 72; MEMO25 XIII.5 | CONFIRMED |
| ITA 2025 s.394 TCS Table has no general sale-of-goods entry. It lists liquor, tendu, timber, scrap, minerals, motor vehicles above ₹10 lakh, overseas tour packages and similar items. | 01-Apr-2026 | [ITA25] s.394 | CONFIRMED |
| Dataset effect: TCS on general goods only for receipts up to 31-Mar-2025, and only where the buyer did not deduct 194Q. | | derived | |

---

## C. GST e-invoicing (IRN)

| Fact | Effective | Source | Status |
|---|---|---|---|
| Threshold: aggregate turnover **> ₹5 crore in any preceding FY from 2017-18 onward**. Set by Notification **10/2023-Central Tax** dated 10-May-2023, amending 13/2020-CT ("ten crore" replaced by "five crore"). | **01-Aug-2023** (in force throughout Nov-2024 to Oct-2026) | EY alert: https://www.ey.com/en_in/technical/alerts-hub/2023/05/cbic-lowers-turnover-threshold-for-e-invoicing-to-inr5-crores-with-effect-from-1-august-2023 | CONFIRMED-SEC (CBIC page not read). A search for a later reduction found none. |
| Earlier phases: ₹500 cr from 01-Oct-2020; ₹10 cr from 01-Oct-2022 (17/2022-CT); ₹5 cr from 01-Aug-2023. | | RSM newsflash (secondary) | CONFIRMED-SEC |
| Invoice must carry a QR code with the IRN embedded where issued under rule 48(4) (rule 46(r)). Taxpayers above the threshold who do not e-invoice must print a declaration (rule 46(s)). | | CGST Rule 46: https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter6/rule46_v1.00.html | CONFIRMED |
| **30-day reporting limit.** From **01-Apr-2025**, taxpayers with **AATO ≥ ₹10 crore** cannot report invoices, credit notes or debit notes to the IRP more than **30 days** after the document date; the IRP rejects them. Example: an invoice dated 01-Apr-2025 cannot be reported after 30-Apr-2025. AATO below ₹10 cr: no restriction "as of now". | 01-Apr-2025 | GSTN advisory dated 05-Nov-2024 (official copy on Maharashtra GST site): https://www.mahagst.gov.in/public/uploads/gstnadvisory/1760531580_293%20Advisory%20Time%20Limit%20for%20Reporting%20e%20Invoice%20on%20the%20IRP%20Portal%20%20Lowering%20of%20Threshold%20to%20AATO%2010%20Crores%20and%20Above.pdf | CONFIRMED |
| Before that, the 30-day limit applied to AATO ≥ ₹100 crore. | from 01-Nov-2023 (advisory 13-Sep-2023, einvoice.gst.gov.in news read-602) | referenced in the 05-Nov-2024 advisory. The 01-Nov-2023 date is from secondary sources. | CONFIRMED (the ≥ ₹100 cr rule) / CONFIRMED-SEC (the date) |
| **IRN composition:** SHA-256 hash of **Supplier GSTIN + Financial Year (YYYY-YY) + Document Type (INV/CRN/DBN) + Document Number**. NIC example input string: `01AAAAA9999A19N2019-20INVABC01234`. The result is 64 hex characters. | | NIC: https://einv-apisandbox.nic.in/irn.html | CONFIRMED. See conflict note 1. |
| Doc number in IRP schema: 1 to 16 characters, pattern `^([a-zA-Z1-9]{1}[a-zA-Z0-9/-]{0,15})$`. Must not start with 0, `/` or `-`. irn.html says lowercase letters are rejected. | | https://einv-apisandbox.nic.in/version1.03/generate-irn.html | CONFIRMED |
| **Signed QR code payload** (JWT signed RS256 / "SHA256RSA", issuer `"iss":"NIC"`; `data` holds a JSON string). Fields: `SellerGstin`, `BuyerGstin`, `DocNo`, `DocTyp`, `DocDt` (dd/mm/yyyy), `TotInvVal`, `ItemCnt`, `MainHsnCode` (HSN of the line with the highest taxable value), `Irn`, `IrnDt` (yyyy-mm-dd hh:mm:ss). JWT header carries `alg`, `kid`, `typ`, `x5t`. | doc dated 14-Jul-2025 | NIC "Qrcode_procedure.pdf": https://einvoice1.gst.gov.in/Documents/Qrcode_procedure.pdf | CONFIRMED. The older sandbox sample in generate-irn.html (2020) lacks `IrnDt`. |
| **Public key for verification:** e-Invoice portal, Downloads > Public Keys. It offers "Public Key (.pem) and Certificate (.cer)" for **e-Invoice1 Production**, **e-Invoice2 Production** and **Sandbox**, "for verification of signed content". | | https://einvoice1.gst.gov.in/Others/Publickeys | CONFIRMED |
| Other verification routes: Verify Signed Invoice (https://einvoice1.gst.gov.in/Others/VSignedInvoice) and the QR Code Verifier App (https://einvoice1.gst.gov.in/Others/QRCodeVerifyApp). The sandbox key (announced 23-Feb-2023) is behind a login at einv-apisandbox.nic.in. | | einvoice1 home and announcements pages | CONFIRMED |
| HSN field in the IRP schema: 4, 6 or 8 digits, not all zeros (`^(?!0+$)([0-9]{4}\|[0-9]{6}\|[0-9]{8})$`), "at least 4 digits". | | generate-irn.html | CONFIRMED |

---

## D. E-way bill

| Fact | Effective | Source | Status |
|---|---|---|---|
| Rule 138(1) CGST Rules: every registered person causing movement of goods of **consignment value exceeding ₹50,000** must generate an EWB before movement. This covers (i) movement in relation to a supply, (ii) movement for reasons other than supply, and (iii) inward supply from an unregistered person. | since 01-Apr-2018 (inter-State); in force throughout | https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter16/rule138_v1.00.html | CONFIRMED |
| Consignment value (rule 138 Explanation 2) = section 15 value on the invoice, bill of supply or delivery challan, **including CGST/SGST/UTGST/IGST and cess**. It excludes exempt supplies invoiced together with taxable ones. Per the EWB FAQ, freight charged separately by the transporter is also excluded. | | rule 138; EWB FAQ https://docs.ewaybillgst.gov.in/html/faq_new.html | CONFIRMED |
| EWB regardless of value: inter-State movement to a job worker; inter-State handicraft goods moved by exempt persons. | | rule 138(1) provisos | CONFIRMED |
| Validity: 1 day per **200 km** or part, for normal cargo. Over-dimensional cargo and multimodal shipments with a ship leg get 1 day per 20 km. Validity starts at the first Part-B entry. | 200 km from 01-Jan-2021 | rule 138(10); EWB FAQ | CONFIRMED (the 01-Jan-2021 date is UNCONFIRMED) |
| Part-B (vehicle) not needed for intra-State movement from the consignor's place to the transporter's place when the distance is < 50 km. | | EWB FAQ | CONFIRMED |
| Exemptions in rule 138(14) include: non-motorised conveyance; goods in the Annexure; exempt goods; alcoholic liquor, petroleum crude, HSD, petrol, natural gas and ATF; no-supply items under Schedule III; and areas each State notifies under 138(14)(d). | | rule 138(14) | CONFIRMED |
| **Intra-State threshold set by each State.** The EWB FAQ says "For threshold limit of value of goods intrastate movement ... please refer to the relevant statute/provisions passed by the respective States/Union Territories". | | EWB FAQ | CONFIRMED |
| State examples (secondary list, page undated): ₹1,00,000 in Delhi, Maharashtra (some goods exempt), Tamil Nadu, Punjab, Bihar, Jharkhand and MP. Rajasthan: ₹1 lakh, or ₹2 lakh within a city. Chhattisgarh: ₹50,000 for 15 notified goods only. Goa: 22 specified goods. Kerala: none, except gold from 20-Jan-2025. J&K: none intra-UT. Most others, incl. Karnataka, UP, Telangana, Haryana, Gujarat and Odisha: ₹50,000. | varies | https://cleartax.in/s/state-wise-threshold-limits-e-way-bills | CONFIRMED-SEC. Verify each State before use. |
| West Bengal: Notification 02/2026-C.T./GST (22-May-2026) and Trade Circular 01/2026 (25-May-2026). Intra-State EWB required above ₹50,000 except job-work movements, which stay exempt. | 01-Jun-2026 | https://comtax.wb.gov.in/GST/GST_Trade_Circulars/WBGST_TC_01_2026_20260525.pdf | CONFIRMED. See conflict note 4. |

---

## E. HSN digits on tax invoices

| Fact | Effective | Source | Status |
|---|---|---|---|
| Notification **78/2020-Central Tax**, 15-Oct-2020 (amends 12/2017-CT under the first proviso to rule 46). Aggregate turnover in the preceding FY up to ₹5 crore: **4 digits**. More than ₹5 crore: **6 digits**. | **01-Apr-2021** | https://www.gstcouncil.gov.in/sites/default/files/2024-05/notfctn-78-central-tax-english-2020.pdf | CONFIRMED |
| Proviso: a registered person with turnover ≤ ₹5 cr "may not mention" HSN on tax invoices for supplies to **unregistered persons** (B2C). So **4-digit HSN is mandatory on B2B invoices** for ≤ ₹5 cr and optional on B2C. For > ₹5 cr, 6 digits are required on all invoices, B2B and B2C. | 01-Apr-2021 | same | CONFIRMED |
| Rule 46(g): the invoice must carry the HSN code for goods or services. SAC is the services equivalent. | | rule 46 | CONFIRMED |
| E-way bill: reporting of at least 6-digit HSN for AATO > ₹5 cr (4-digit for others) enforced from 01-Feb-2024 by NIC advisory. | 01-Feb-2024 | taxo.online summary of the NIC advisory | UNCONFIRMED (secondary only, not read) |

---

## F. GST invoice number rule

| Fact | Effective | Source | Status |
|---|---|---|---|
| Rule 46(b) CGST Rules: "a consecutive serial number **not exceeding sixteen characters**, in one or multiple series, containing alphabets or numerals or special characters- **hyphen or dash and slash** symbolised as "-" and "/" respectively, and any combination thereof, **unique for a financial year**". | since 01-Jul-2017 (current wording) | https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter6/rule46_v1.00.html | CONFIRMED |
| IRP schema adds: the first character must not be 0, `/` or `-`, and lowercase letters are rejected (see C). Practical rule for e-invoicing taxpayers: `[A-Z1-9][A-Z0-9/-]{0,15}`. | | NIC irn.html and generate-irn.html | CONFIRMED |
| Whether the series must restart each FY: the rule only requires uniqueness within an FY. A GSTN advisory (04-Apr-2019, per secondary sources) asks taxpayers to start a new series each FY. | | secondary (irisgst, taxguru) | CONFIRMED-SEC |

---

## G. MSMED Act 2006: payment terms and interest

Source for the Act text: https://dcmsme.gov.in/old/MSMED2006.pdf (Ministry of MSME copy of the Act as enacted).

| Fact | Effective | Status |
|---|---|---|
| s.2(b) "appointed day" = the day after **15 days** from the day of acceptance or deemed acceptance. | 02-Oct-2006 onward | CONFIRMED |
| Day of acceptance = day of actual delivery or rendering. If the buyer objects in writing within 15 days of delivery, it is the day the supplier removes the objection. | | CONFIRMED |
| **Deemed acceptance** (Explanation (ii)): if there is no written objection within 15 days of delivery, the day of actual delivery or rendering is the day of deemed acceptance. | | CONFIRMED |
| **s.15**: the buyer pays on or before the date agreed in writing or, absent agreement, **before the appointed day** (15 days). Proviso: the agreed period may in no case exceed **45 days** from acceptance or deemed acceptance. | | CONFIRMED |
| **s.16**: on failure, the buyer pays, "notwithstanding anything contained in any agreement ... or in any law", **compound interest with monthly rests** from the appointed day, or from the day after the agreed date, at **three times the bank rate notified by the Reserve Bank**. | | CONFIRMED |
| Applies only where the supplier is a micro or small enterprise (s.2(n) "supplier"). | | CONFIRMED-SEC (definition not re-read) |
| MSMED (Amendment) Act 2026 (passed Aug-2026; Act 16 of 2026 per secondary sources). The PIB summary lists changes to classification, voluntary registration, TReDS for CPSEs, MSEFC timelines and recovery. Ss.15 and 16 are not among the listed changes. | PIB 11-Aug-2026; commencement 12/15-Oct-2026 per secondary | CONFIRMED (PIB: https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/aug/doc2026811951101.pdf). The commencement date is UNCONFIRMED. |

### G.1 RBI Bank Rate (= MSF rate), Nov 2024 to Oct 2026

| Effective from | Bank Rate | Repo | 3x Bank Rate (s.16) | Source (rbi.org.in) | Status |
|---|---|---|---|---|---|
| in force at 01-Nov-2024 (unchanged at the 09-Oct-2024 and 06-Dec-2024 meetings) | **6.75%** | 6.50% | 20.25% | Minutes, Dec 4-6 2024: https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=59347 | CONFIRMED |
| **07-Feb-2025** | **6.50%** | 6.25% | 19.50% | Minutes, Feb 5-7 2025: https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=59818 | CONFIRMED |
| **09-Apr-2025** | **6.25%** | 6.00% | 18.75% | Minutes, Apr 7-9 2025: https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=60279 | CONFIRMED |
| **06-Jun-2025** | **5.75%** | 5.50% | 17.25% | Resolution, Jun 6 2025: https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=60604 | CONFIRMED |
| (Aug-2025 and Oct-2025: held) | 5.75% | 5.50% | | inferred: the Dec-2025 resolution cuts from 5.50% repo | CONFIRMED-SEC (inference) |
| **05-Dec-2025** | **5.50%** | 5.25% | 16.50% | Minutes, Dec 3-5 2025: https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61856 | CONFIRMED |
| (06-Feb-2026, 08-Apr-2026, 05-Jun-2026, 05-Aug-2026: held) | 5.50% | 5.25% | | Resolutions prid 62514 (Apr), 62863 (Jun), 63287 (Aug). Feb-2026 from secondary sources; the RBI PDF was behind a captcha. | CONFIRMED (Apr, Jun, Aug) / CONFIRMED-SEC (Feb) |
| **07-Oct-2026** | **5.75%** | 5.50% (hike) | 17.25% | Resolution, Oct 5-7 2026: https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63742 | CONFIRMED |

Rate changes are announced "with immediate effect" on the decision date. That wording was seen explicitly for Feb, Apr and Jun 2025.

**UNCONFIRMED:** how s.16 treats a bank-rate change during a delay period (rate on the appointed day vs. the rate in force each month). The Act is silent and practice varies.

---

## H. GST reverse charge (services) relevant to a company buyer

Primary: Notification **13/2017-Central Tax (Rate)**, 28-Jun-2017, as amended up to 07/2025-CT(R). Consolidated copy printed from idtc.icai.org: https://d23z1tp9il9etb.cloudfront.net/download/gstlaw/NOTIFICATION%20NO.%2013_2017-CENTRAL%20TAX%20(RATE)1737461987.pdf. Rates: Notification 11/2017-CT(R) and 8/2017-IT(R), as amended by **15/2025-CT(R) / 15/2025-IT(R)** dated 17-Sep-2025, effective **22-Sep-2025** (gazette copy: https://bcasonline.org/wp-content/uploads/2025/09/No.15-2025-Central-Tax-Rate.pdf).

| Service (13/2017 Sl.) | Supplier | Recipient liable | Rate / notes | Effective | Status |
|---|---|---|---|---|---|
| **GTA**, road transport of goods (Sl. 1) | GTA | Factory, society, co-op, **any GST-registered person**, body corporate, partnership firm/LLP/AOP, casual taxable person. The person paying freight is treated as the recipient. | RCM applies **unless** the GTA has opted for forward charge and issues an invoice with the Annexure III declaration (proviso from 18-Jul-2022). Rate options: **5% without ITC** (RCM default, or forward charge) or forward charge with ITC at **12% up to 21-Sep-2025** and **18% from 22-Sep-2025** (8/2017-IT(R) Sl. 9(iii)(b) changed "12" to "18"). | 18-Jul-2022 / 22-Sep-2025 | CONFIRMED. The 15/2025 gazette text does not name GTA at Sl. 9(iii); the GTA mapping is backed by the notification's own illustration of a GTA charging 18%. |
| **Legal services** by an individual advocate, senior advocate or firm of advocates (Sl. 2); arbitral tribunal (Sl. 3) | Advocate / firm / tribunal | Any business entity in the taxable territory (the litigant is the recipient) | 18% RCM | since 01-Jul-2017 | CONFIRMED. The exemption for small business entities under 12/2017 is UNCONFIRMED. |
| **Security services** (supply of security personnel) (Sl. 14) | Any person **other than a body corporate** | A registered person, excluding government TDS-only registrants and composition taxpayers | 18% RCM | from 01-Jan-2019 | CONFIRMED (the effective date comes from the footnote trail: 29/2018-CT(R)) |
| **Renting of motor vehicle** carrying passengers, fuel cost included (Sl. 15) | Non-body-corporate | **Body corporate** | RCM applies when the supplier does not charge CGST at "6 per cent" (12% total). Forward rates: 5% without ITC or 12% with ITC, which became **18% from 22-Sep-2025** (8/2017-IT(R) Sl. 10(i)). Sl. 15 kept the "6 per cent" wording until **Notification 08/2026-CT(R)** (27-Oct-2026) changed it to "9", effective **01-Nov-2026**. | 31-Dec-2019; 22-Sep-2025; 01-Nov-2026 | Sl. 15 text: CONFIRMED. 08/2026: CONFIRMED-SEC (taxheal/taxguru). |
| **Renting of immovable property other than residential dwelling** (Sl. 5AB) | **Unregistered** person | Registered person; composition taxpayers excluded from 16-Jan-2025 (7/2025-CT(R)) | 18% RCM | **10-Oct-2024** (9/2024-CT(R)) | CONFIRMED |
| Residential dwelling rented to a registered person (Sl. 5AA) | Any person | Registered person | | 18-Jul-2022 | CONFIRMED |
| Director's services (Sl. 6) | Director | Company / body corporate | 18% RCM | 2017 | CONFIRMED |
| Sponsorship (Sl. 4) | Any person other than a body corporate (from 16-Jan-2025) | Body corporate or partnership firm | 18% RCM | | CONFIRMED |
| Government / local authority services (Sl. 5), excluding renting, postal, railway and transport | Govt / LA | Business entity | | | CONFIRMED |
| Govt renting of immovable property (Sl. 5A) | Govt / LA (excl. Railways) | Registered person | | 25-Jan-2018 | CONFIRMED |
| Others: insurance agent → insurer (Sl. 7); recovery agent → bank/NBFC (Sl. 8); author/music/copyright (Sl. 9, 9A); business correspondent agent (Sl. 13); securities lending (Sl. 16) | | | | | CONFIRMED |

Note: 13/2017's amendment list ends at 07/2025-CT(R) (16-Jan-2025). Any 2025-26 amendment other than 08/2026 was not found (UNCONFIRMED that none exists).

---

## I. GSTIN and PAN structure

### GSTIN (15 characters)

- **CGST Rule 10(1):** two characters for the State code; ten characters for the PAN (or TAN); two characters for the entity code; one checksum character. Source: https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter3/rule10_v1.00.html. CONFIRMED.
- Customary breakdown of the 2-character entity code:
  - Position 13 = entity number for the same PAN in the same State (1-9, then A-Z).
  - Position 14 = "Z" by default.
  - Source: python-stdnum docs: https://sources.debian.org/src/python-stdnum/2.2-1/stdnum/in_/gstin.py. CONFIRMED-SEC; no official GSTN text found.
- **Check character (position 15), Luhn mod 36.** Use the alphabet `0-9A-Z` (values 0-35):
  1. For each of the first 14 characters, left to right, multiply its value by weights 1, 2, 1, 2, ... (the 1st character has weight 1 and the 14th has weight 2).
  2. Replace each product p with `p // 36 + p % 36`, and sum the results.
  3. Check value = `(36 - sum % 36) % 36`. Map it back through the alphabet.
- Worked example: `27AAPFU0939F1Z` gives sum 221, 221 mod 36 = 5, check 31 = **V**, so `27AAPFU0939F1ZV`.
  - This matches the python-stdnum test vector (stdnum `luhn.validate(number, alphabet)` over 0-9A-Z).
  - The NIC sample buyer `11AAACT3904F1ZZ` also validates.
  - NIC sandbox test GSTINs such as `37ARZPT4384Q1MT` do **not** validate, so the sandbox does not enforce the checksum on test data.
- **Status of the "official method":** No GSTN or CBIC publication of the algorithm was found. CBIC Rule 10 only says "one checksum character". The Luhn mod 36 method is the de facto method (stdnum, ClearTax, many validators). Treat it as CONFIRMED-SEC and UNCONFIRMED as an official specification.

### PAN (10 characters, `[A-Z]{5}[0-9]{4}[A-Z]`)

Source: Protean (NSDL e-Gov, ITD's PAN service agency): https://www.proteantech.in/articles/pan-card-number-10-characters-meaning. The official incometaxindia.gov.in tutorial was blocked. CONFIRMED-SEC (semi-official).

- Characters 1-3: alphabetic series AAA to ZZZ.
- Character 4: holder status.
  - **P** Individual
  - **C** Company
  - **H** HUF
  - **F** Firm / LLP
  - **A** AOP
  - **T** Trust
  - **B** BOI
  - **L** Local authority
  - **J** Artificial juridical person
  - **G** Government
- Character 5: first letter of the surname (individuals) or of the entity name (others).
- Characters 6-9: sequential digits 0001-9999.
- Character 10: alphabetic check character. The formula is not published (UNCONFIRMED).
- GSTIN positions 3-12 = PAN, so GSTIN position 6 = PAN holder type.

---

## Conflicts and caveats noted

1. **IRN field order.** NIC irn.html gives the order GSTIN + FY + DocType + DocNo, with an example string. generate-irn.html lists "Gstin, DocDtls.No, DocDtls.Typ, financial year". Use the irn.html order, which has the example. The IRP generates the IRN in any case.
2. **Signed QR fields.** The 2020 sandbox sample has 9 data fields with no `IrnDt`. The 2025 NIC procedure document lists 10 fields including `IrnDt`. Use 10.
3. **206AA.** The old text was not read officially. The new-Act wording ("5% for 8(ii)/8(v), 20% in any other case") matches secondary descriptions of the old 206AA.
4. **West Bengal.** ClearTax says WB moved from ₹1 lakh to ₹50,000 on 01-Dec-2023. The WB Trade Circular 01/2026 restates ₹50,000 from 01-Jun-2026, with a job-work exemption. The 2026 notification may have re-notified the threshold to add the job-work carve-out. Treat ₹50,000 as the threshold throughout the dataset period.
5. **Renting of motor vehicles.** From 22-Sep-2025 to 31-Oct-2026, the RCM entry still referred to "6 per cent" CGST while the forward-charge with-ITC rate became 9% CGST (18%). The mismatch was fixed by 08/2026-CT(R) from 01-Nov-2026. Commentary disagrees on how the mismatch should be read.
6. **e-invoice threshold.** No reduction below ₹5 cr was found up to Nov 2026. One undated secondary article mentions a proposed ₹1.5 cr threshold; it is not notified (UNCONFIRMED).
7. **Bank rate Feb-2026 and Aug/Oct-2025 holds.** These were not read from RBI originals. The bank rate is unaffected because both the before and after values were confirmed.
8. **tds_sections.csv `effective_from` values that predate the dataset were not verified (UNCONFIRMED).** They mark the start of each rule version and do not affect Nov-2024 to Oct-2026 values:
   - 194J 2%/10% split from 2020-04-01
   - 194-I ₹2.4 lakh threshold from 2019-04-01
   - 206C(1H) from 2020-10-01
   - 206AB from 2021-07-01
   - 194Q from 2021-07-01 (secondary: Circ 13/2021)

   In-period dates (2024-10-01, 2025-04-01, 2026-04-01) are confirmed. The 1961 206AA row is marked UNCONFIRMED in the CSV because the official text could not be read.
