"""The products bridge (app/products/shared/bridge.py): a capability one product lends another must work even when
nothing started the app or imported the owner first (a script, a generator, a fresh process). CI caught DBaba's
reasoning silently getting the empty default for Sheetu's lexicon in tests.fuzz.db_errors (2775/3000);
the word reading now lives in app/products/shared/meaning.py, so DBaba doesn't need Sheetu at all."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fresh(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_a_borrowed_capability_loads_its_owner_in_a_fresh_process():
    code = ("from app.products.shared import bridge; import sys; "
            "assert 'app.products.dbaba' not in sys.modules; "
            "print(bridge.call('dbaba.concept_match', 'what is a left join', default='MISSING') != 'MISSING')")
    assert _fresh(code) == "True"


def test_database_reasoning_reads_complaints_without_app_startup():
    code = ("from app.dbknow import reasoning as R; "
            "res = R.diagnose('our read replica is lagging behind and shows old data'); "
            "import sys; print('replica_lag' in [x['cause'] for x in res['open'][:4]], 'app.products.sheetu' in sys.modules)")
    assert _fresh(code) == "True False"          # reads words through shared/meaning.py, without loading Sheetu


def test_unknown_owner_still_returns_the_default():
    from app.products.shared import bridge

    assert bridge.call("nobody.anything", default=7) == 7
