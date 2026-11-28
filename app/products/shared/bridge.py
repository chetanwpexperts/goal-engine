"""Shared optional product capabilities, registered by their owners."""
import re
from typing import Callable

_impl: dict[str, Callable] = {}

DB_TO_SHEET = re.compile(r"\b(query|queries|database|db|mysql|maria ?db|postgre\w*|sql|sqlite)\b.{0,40}\b(excel|xlsx|spreadsheets?)\b|"
                         r"\b(excel|xlsx|spreadsheets?)\b.{0,40}\b(database|mysql|maria ?db|postgre\w*|sqlite)\b", re.I)


def provide(name, fn):
    _impl[name] = fn


def call(name, *a, default=None, **kw):
    fn = _impl.get(name)
    return fn(*a, **kw) if fn else default
