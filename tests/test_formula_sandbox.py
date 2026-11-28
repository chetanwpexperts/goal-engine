"""Formulas from an uploaded workbook are worked out by Sheetu itself. A crafted formula must never reach Python
objects (attributes, indexing, calling a result); ordinary formulas keep working."""

import pytest

from app.products.sheetu.office.sheetdata import _Evaluator


def _value(formula: str):
    raw = {"S": ([[2, 3, None]], [[None, None, formula]])}
    return _Evaluator(raw).cell("S", 0, 2)


@pytest.mark.parametrize("formula", [
    "=().__class__",
    '=SUM(A1:B1).__class__.__bases__',
    '=IF(1,2,3).real',
    '=(1)(2)',
    '=IF.__globals__["os"]',
    '=[1][0]',
])
def test_crafted_formulas_give_nothing(formula):
    assert _value(formula) is None


@pytest.mark.parametrize("formula, want", [("=A1+B1", 5), ("=SUM(A1:B1)*2", 10), ("=IF(A1>1,1.5,0)", 1.5), ("=ROUND(B1/A1,1)", 1.5)])
def test_ordinary_formulas_still_work(formula, want):
    assert _value(formula) == want
