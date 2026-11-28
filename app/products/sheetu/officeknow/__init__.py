"""Goal Engine's Microsoft brain: Excel, Word, PowerPoint, Outlook, OneDrive, Teams and Windows problems. No AI.

Plain JSON files, grown every night (docs/office-knowledge/README.md):

  issues.json    playbooks: how people describe it -> causes -> the fix (done by the agent on the file when possible)
  formulas.json  "har mahine ka total" -> a tested formula for each Excel version / Google Sheets

Rule for every fix: if the agent can do it on the uploaded file, it's `auto` (the agent does it, the person
downloads the fixed copy). Steps are only for what nobody but the person can do, and stay short.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ("issues", "formulas")
APPS = {"excel", "word", "powerpoint", "outlook", "onedrive", "teams", "windows", "access", "google_sheets", "csv", "any"}
CATEGORIES = {"formula_error", "data_cleaning", "lookup_matching", "dates_numbers", "big_slow_file", "corrupt_recovery",
              "formatting", "pivot_charts", "macros_vba", "import_export", "sharing_sync", "printing", "install_activation",
              "email", "security_password", "documents", "presentations", "windows_system"}
# What the file tools can do by themselves on an uploaded file (each gets real code + a test before it's used)
AUTO_FIXES = {"trim_spaces", "text_to_number", "fix_dates", "remove_duplicates", "fix_broken_refs", "replace_formula",
              "unmerge_cells", "remove_blank_rows", "split_column", "compare_sheets", "recover_file", "convert_file",
              "remove_formatting_bloat", "fix_encoding"}
TEMPLATE_KINDS = {"excel365", "excel2016", "google_sheets"}
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

REQUIRED = {
    "issues": {"id", "apps", "category", "title_en", "title_hi", "symptoms", "causes", "fixes", "verify", "test_phrases", "source"},
    "formulas": {"id", "title_en", "title_hi", "examples", "needs", "templates", "explain_en", "explain_hi", "test_phrases", "source"},
}


@lru_cache(maxsize=None)
def load(name: str) -> list[dict]:
    path = HERE / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def validate() -> list[str]:
    problems = []
    for name in FILES:
        seen = set()
        for i, e in enumerate(load(name)):
            where = f"{name}[{i}] {e.get('id', '?')}"
            missing = REQUIRED[name] - set(e)
            if missing:
                problems.append(f"{where}: missing {sorted(missing)}")
                continue
            if e["id"] in seen:
                problems.append(f"{where}: duplicate id")
            seen.add(e["id"])
            if not str(e["source"]).startswith(("https://support.microsoft.com/", "https://learn.microsoft.com/",
                                                "https://support.google.com/")):
                problems.append(f"{where}: source must be official Microsoft (or Google for Sheets) documentation")
            for link in e.get("seen_at", []):
                if not str(link).startswith("https://"):
                    problems.append(f"{where}: seen_at links must be https")
            if "license" in e and str(e["license"]).upper().startswith(("GPL", "AGPL", "LGPL")):
                problems.append(f"{where}: nothing may be copied from GPL-family projects")
            learned = {x.strip().lower() for x in e.get("symptoms", []) + e.get("examples", [])}
            tps = e["test_phrases"]
            if len(tps) < 3 or not any(DEVANAGARI.search(t) for t in tps):
                problems.append(f"{where}: needs 3+ test_phrases (English, Hinglish and one in Hindi script)")
            if any(t.strip().lower() in learned for t in tps):
                problems.append(f"{where}: test_phrases must be different from the learned sentences")
            if name == "issues":
                for app in e["apps"]:
                    if app not in APPS:
                        problems.append(f"{where}: unknown app {app}")
                if e["category"] not in CATEGORIES:
                    problems.append(f"{where}: category must be one of {sorted(CATEGORIES)}")
                if len(e["symptoms"]) < 4:
                    problems.append(f"{where}: give at least 4 ways people describe it")
                cause_ids = {c.get("id") for c in e["causes"]}
                for f in e["fixes"]:
                    if f.get("cause") not in cause_ids:
                        problems.append(f"{where}: fix points to unknown cause {f.get('cause')}")
                    if f.get("action") not in ("auto", "steps", "advice"):
                        problems.append(f"{where}: fix.action must be auto/steps/advice")
                    if f.get("action") == "auto" and f.get("auto") not in AUTO_FIXES:
                        problems.append(f"{where}: auto fix must be one of {sorted(AUTO_FIXES)}")
                    for k in ("steps_en", "steps_hi"):
                        if not f.get(k):
                            problems.append(f"{where}: fix needs {k}")
                        elif len(f[k]) > 420:
                            problems.append(f"{where}: {k} too long: keep it to 3 short steps")
            if name == "formulas":
                for kind, tpl in e["templates"].items():
                    if kind not in TEMPLATE_KINDS:
                        problems.append(f"{where}: unknown template kind {kind}")
                    if not str(tpl).startswith("="):
                        problems.append(f"{where}: template {kind} must be a formula starting with =")
                    for ph in re.findall(r"\{(\w+)\}", tpl):
                        if ph not in e["needs"]:
                            problems.append(f"{where}: placeholder {{{ph}}} not listed in needs")
    return problems


def stats() -> dict:
    return {"entries": sum(len(load(n)) for n in FILES)}
