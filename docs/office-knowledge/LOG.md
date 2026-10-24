# Microsoft knowledge log

## 2026-10-22
- Started the Microsoft brain: 4 playbooks (#REF!, VLOOKUP #N/A, numbers stored as text, corrupt Excel file) and 2 formulas (sum by month, duplicates).
- Held-out test: all English, Hinglish and Hindi-script test phrases recognised.

## 2026-10-22 (night run)
- Learning feed: not reachable this run (fetch permission not granted), skipped.
- Accounts (AR/AP): 10 formulas (invoice due date, days overdue, aging bucket 0-30/31-60/61-90/90+, aging summary by customer, invoice balance after part payments, collections list, three-way match PO/GRN/invoice, duplicate vendor payments, early-payment discount 2/10 net 30, DSO) and 2 playbooks (aging report in wrong buckets, payment received but invoice still outstanding).
- Gave vlookup-na-no-match and sum-by-month extra symptoms/examples (incl. Hindi script) so they stay distinct from the new accounts entries.
- tests/test_officeknow.py: all pass. Whole suite: 6 failed + 35 errors, identical before and after this change: tests from main need tests/data/practice.xlsx, which .gitignore's `data/` rule keeps out of git.

## 2026-10-22 (night run 2)
- Learning feed: not reachable this run (fetch permission request timed out), skipped.
- Accounts (bank / card / intercompany reconciliation): 8 formulas (bank line in books? with date tolerance, outstanding cheques, deposits in transit, bank reconciliation statement with Reconciled/Difference result, bank charges/interest not booked, card/POS/UPI settlement vs sales net of MDR fees, intercompany balance check, transposition-error check) and 3 playbooks (bank reconciliation doesn't tally, card settlement short, intercompany balances don't match).
- Gave payment-not-matching-invoice and find-duplicates extra symptoms/examples so they stay distinct from the new entries.
- Auto fixes wanted: match_bank_to_ledger (one-to-one matching).
- tests/test_officeknow.py: all pass. Whole suite: 6 failed + 35 errors, identical before and after (tests/data/practice.xlsx missing from git).

## 2026-10-23
- Learning feed: not reachable this run (fetch permission request timed out), skipped.
- Test generator, seeds 610230-610379: 2162/2163 before. The one miss: 'paid by wise expenses' answered as money totals while the generator expected a count. Expenses are money, so the generator now expects the amounts added up when the people's word for the rows is money ('X wise expenses', 'item wise bills' with a 'Bill Amount' column); the agent now also adds up money columns headed 'Rs.', 'Amt (Rs)', 'Expense...', 'Rent' (it counted rows there). Pack: 'expense/kharcha' are words for the amount column.
- Generator grew by one field: rent collection (tenant, building/society/property, floor/Flr, rent, paid on, mode/Paid Via, received?). First run 99.1%: fixed with pack synonyms building~property~society, floor~flr~manzil, mode~via, tenant~kirayedar, rent~kiraya. After: 2155/2155 (100%) on the same seeds, 7077/7077 on 500 more (700000-700499). Seeds that once failed are now permanent in tests/test_fuzz_sheets.py.
- Accounts (month-end close): 16 formulas (SLM depreciation, WDV at a fixed rate, DB declining balance, first-year pro-rata, accumulated depreciation/NBV, prepaid expense balance, deferred revenue recognised, accrued interest, unbilled expense provision, trial balance tally check, wrong-side posting finder, voucher balanced check, account balance from journal, account code in chart?, account name from code, period cut-off check) and 3 playbooks (trial balance not tallying, depreciation schedule wrong, wrong/unrecognised account codes).
- Gave bank-reconciliation-difference and collections-list extra symptoms/examples so they stay distinct.
- tests/test_officeknow.py: all pass. Whole suite: 403 passed, 1 failed: tests/test_mind.py::test_how_many_sheets_gets_the_number fails on origin/main too (the reply now offers 'Show <sheet>' buttons, the test expects none) - not touched here.
