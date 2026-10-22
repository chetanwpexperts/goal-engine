# Microsoft knowledge backlog

Work top to bottom, one item per run (10-20 playbooks or 20-40 formulas). Tick `[x]` when done and note it in LOG.md.

## Department tasks people do in Excel (Chetan's list, 22 Oct) — for each: how people ask it (Eng/Hinglish/Hindi), the formula, the common mistakes
- [ ] Accounts: invoice aging (0-30/31-60/61-90/90+), AR collections list, AP three-way matching (PO vs receipt vs invoice), duplicate vendor payments, early-payment discount tracking
- [ ] Accounts: bank reconciliation (ledger vs bank statement, timing differences, bank fees), credit card / POS settlement matching, intercompany matching
- [ ] Accounts: month-end close: accruals/deferrals, depreciation schedule (straight-line, WDV), trial balance check (debits = credits), transposed numbers, wrong account codes
- [ ] Finance: budget vs actual variance, expense reports, cash flow forecast, GST/tax calculation
- [ ] Sales & marketing: pipeline by stage, lead scoring, campaign ROI, commission calculation, dirty lead data (duplicates, missing phones, USA/U.S.A.)
- [ ] HR & operations: attendance and leave, shift roster clashes, project Gantt, inventory reorder alerts, accidental formula overwrites (sheet protection)
- [ ] Data analysis: pivot summaries, KPI dashboards, trend lines, unrefreshed pivots, VLOOKUP breaking on inserted columns (XLOOKUP), cluttered charts
- [ ] Working across sheets: consolidate (SUM across sheets, VSTACK), cross-sheet lookups (XLOOKUP/INDEX-MATCH), Power Query merges, group editing

## Excel (most asked first)
- [ ] Formula errors: #REF! (done: 1), #N/A (done: 1), #VALUE!, #DIV/0!, #NAME?, #NUM!, #SPILL!, #CALC!, circular reference warning, formula shows as text, formula not updating (manual calculation)
- [ ] Lookups and matching: VLOOKUP/XLOOKUP/INDEX-MATCH problems, two sheets not matching, matching on multiple columns, partial match, case and space issues, comparing two lists
- [ ] Data cleaning: extra spaces, numbers as text (done: 1), mixed date formats, DD/MM vs MM/DD, leading zeros lost (phone, PIN, IDs), split full name, merge columns, blank rows, merged cells breaking sort/filter, duplicates
- [ ] Dates and numbers: dates showing as numbers, wrong year/century, time totals over 24h, rounding, scientific notation (Aadhaar/long IDs as 1.23E+11), Indian number format (lakhs/crores)
- [ ] Big and slow files: file slow, lakhs of rows, too many formulas/volatile functions (INDIRECT, OFFSET, TODAY), whole-column references, conditional formatting bloat, file size huge, 1,048,576 row limit
- [ ] Recovery: corrupt file (done: 1), unsaved file lost, AutoRecover, previous versions, file opens read-only, "file is locked for editing"
- [ ] Pivot tables and charts: pivot not refreshing, blank/(blank) rows, grouping dates fails, chart not updating
- [ ] Import/export: CSV opens in one column, Hindi/UTF-8 text garbled in CSV, PDF to Excel, Tally/ERP/bank statement exports
- [ ] Macros/VBA: macros disabled, "Run-time error 1004", security warnings, macro not running after update
- [ ] Sharing and sync: OneDrive/SharePoint sync conflicts, co-authoring errors, "upload blocked", links to other workbooks breaking
- [ ] Printing and page setup: prints on many pages, cut columns, repeat header rows
- [ ] Everyday formulas (formulas.json): totals by month/category (done: 1), running total, top N, count unique, percentage change, age from DOB, working days, GST calculation, EMI/PMT, ranking, IF with multiple conditions, text join, extract text, duplicates (done: 1)

## Word
- [ ] Document won't open / corrupt, recover unsaved document, formatting jumps, page numbers, table of contents, track changes, Hindi fonts (Mangal/Kokila/Unicode vs Kruti Dev), convert to PDF, compare documents

## PowerPoint
- [ ] File won't open, too large, video/audio won't play, fonts change on another PC, convert to PDF/video

## Outlook and email
- [ ] Outlook not opening, send/receive errors (0x800CCC0E, 0x8004010F, ...), PST/OST too large or corrupt, password prompts, rules, search not working, Gmail/Office 365 setup

## Windows and Office installation
- [ ] Activation errors, "product deactivated", Office repair, Windows update stuck, slow PC, printer offline, disk full, BitLocker recovery key, keyboard Hindi input

## OneDrive and Teams
- [ ] Sync stuck, files missing, storage full; Teams mic/camera, login loops, meeting issues

## Google Sheets (same people use both)
- [ ] Differences from Excel, IMPORTRANGE, QUERY, sharing permissions, Sheets to Excel conversion issues

## Auto fixes wanted (code to write in the file tools; add here when a fix had to be `steps`)
