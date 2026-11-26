import os
import re
import sys

import requests

base = os.environ.get("DBABA_URL", "http://127.0.0.1:8000").rstrip("/")
key = os.environ["DBABA_API_KEY"]
query = sys.argv[1] if len(sys.argv) > 1 else "SELECT * FROM orders WHERE customer_id = 42;"

database = (sys.argv[2] if len(sys.argv) > 2 else "") or os.environ.get("DBABA_DB") or "MySQL"
is_sql = re.match(r"^\s*(?:select|with|insert|update|delete|replace|merge|create|alter|drop|truncate|explain|show|describe|desc|grant|revoke|call|set|begin|commit|rollback)(?:\s|$)", query, re.I) and not query.rstrip().endswith("?")
text = f"{database}: {query}" if is_sql else query

with requests.Session() as session:
    session.headers["Authorization"] = f"Bearer {key}"
    response = session.post(f"{base}/api/new-chat", timeout=120)
    response.raise_for_status()
    response = session.post(f"{base}/api/message", json={"text": text}, timeout=120)
    response.raise_for_status()
    print(response.json()["reply"])
