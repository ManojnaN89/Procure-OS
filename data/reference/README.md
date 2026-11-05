# Reference data (real, public)

Every file records its source. Anything marked VERIFY was read from a secondary source or written from knowledge.

| File | Rows | Source | Status |
|---|---|---|---|
| gst_goods_rates_2025-09-22.csv | 1,195 | Official Gazette copy of Notification 9/2025-Central Tax (Rate), 17 Sep 2025 (7 schedules: 2.5%, 9%, 20%, 1.5%, 0.125%, 0.75%, 14% CGST = 5%, 18%, 40%, 3%, 0.25%, 1.5%, 28% total) | Parsed; every serial number present. Rates BEFORE 22 Sep 2025 (12%, 28% slabs) are not in this file |
| gst_services_rates.csv | 22 | Notifications 15/2025 and 11/2017 | Changed entries read from text; others marked VERIFY |
| gst_rate_history_catalogue.csv | 207 | Rate before and after 22 Sep 2025 for every HSN/SAC in the catalogue: PIB release of 3 Sep 2025 (changed items), ICAI consolidated copy of old Notification 1/2017, Notification 10/2025 (nil items), Notification 15/2025 (services) | 171 CONFIRMED, 36 SECONDARY (mostly services). gst_lookup.py cannot see the nil items; use this file |
| gst_state_codes.csv | 38 | Written from knowledge | VERIFY against GST portal |
| gst_uqc_units.csv | 44 | Written from knowledge | VERIFY against GST portal |
| holidays_central_2024_2025.csv | 37 | DoPT OMs 03.07.2023 and 09.07.2024 (official copies) plus separate OMs; one row from staffnews.in | Mostly official |
| holidays_2026_central_gazetted.csv | 17 | DoPT OM 03.07.2025 Annexure-I via staffnews.in; Bakrid corrected to 28 May 2026 by DoPT order of 22.05.2026 | VERIFY against official PDF |
| holidays_state_MH_GJ_KA_2024_2026.csv | 224 | State GAD / DPAR notifications; 8 rows from news sources | See notes in the agent report; Gujarat copies hosted on simpliance.in |
| tax_rules_facts.md | 9 sections | TDS (old and new Act), e-invoice, e-way bill, HSN digits, invoice numbers, MSMED, RBI Bank Rate, reverse charge, GSTIN and PAN | Mostly official text; each fact carries its source and status; conflicts listed at the end |
| tds_sections.csv | 25 | Income-tax Act 2025 s.393 (read from the Act) and the 1961 Act split at 1 Apr 2025 | 24 CONFIRMED, 1 UNCONFIRMED (206AA, 1961). Tax adviser still to confirm before the first client |
| pincode_directory.csv | 19,300 | India Post "All India Pincode Directory till last month" on data.gov.in (157,126 office rows reduced to one row per pincode; file obtained from the 1:1 GitHub copy dropdevrahul/pincodes-india, byte size identical to data.gov.in's listed 23,760,720, because data.gov.in file URL returned 403). Licence: Government Open Data License - India (GODL-India), https://www.data.gov.in/Godl, attribution required | Official data via mirror; all 36 state names mapped to GST state codes. 46 pincodes occur under two states in the source (first row kept). First office row and district kept as given (upper case) |
| hsn_sac_codes.csv | 23,048 | GST portal master HSN_SAC.xlsx, https://tutorial.gst.gov.in/downloads/HSN_SAC.xlsx (official; sheet modified 24 Apr 2026): goods at 2, 4, 6 and 8 digits plus 681 SAC rows. Licence: no licence text on the file (government publication) | Official. 443 six-digit rows derived from their sole 8-digit child; 201 six-digit parents with several children are absent from the portal file; 4 blank headings dropped (0503, 1402, 1519, 2851); generic "Other" 6/8-digit rows carry the parent text. Catalogue code 400910 is not a valid HS code |

Still to collect: GST rates before 22 Sep 2025 for catalogue items; RBI bank rate history; bank IFSC prefixes; pincode directory.
