# Microsoft knowledge log

## 2026-10-22
- Started the Microsoft brain: 4 playbooks (#REF!, VLOOKUP #N/A, numbers stored as text, corrupt Excel file) and 2 formulas (sum by month, duplicates).
- Held-out test: all English, Hinglish and Hindi-script test phrases recognised.

## 2026-10-22 (night run)
- Learning feed: not reachable this run (fetch permission not granted), skipped.
- Accounts (AR/AP): 10 formulas (invoice due date, days overdue, aging bucket 0-30/31-60/61-90/90+, aging summary by customer, invoice balance after part payments, collections list, three-way match PO/GRN/invoice, duplicate vendor payments, early-payment discount 2/10 net 30, DSO) and 2 playbooks (aging report in wrong buckets, payment received but invoice still outstanding).
- Gave vlookup-na-no-match and sum-by-month extra symptoms/examples (incl. Hindi script) so they stay distinct from the new accounts entries.
- tests/test_officeknow.py: all pass. Whole suite: 6 failed + 35 errors, identical before and after this change: tests from main need tests/data/practice.xlsx, which .gitignore's `data/` rule keeps out of git.
