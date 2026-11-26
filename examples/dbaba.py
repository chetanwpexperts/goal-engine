import os
import sys

import requests

base = os.environ.get("DBABA_URL", "http://127.0.0.1:8000").rstrip("/")
key = os.environ["DBABA_API_KEY"]
query = sys.argv[1] if len(sys.argv) > 1 else "SELECT * FROM orders WHERE customer_id = 42;"

with requests.Session() as session:
    session.headers["Authorization"] = f"Bearer {key}"
    response = session.post(f"{base}/api/new-chat", timeout=120)
    response.raise_for_status()
    response = session.post(f"{base}/api/message", json={"text": query}, timeout=120)
    response.raise_for_status()
    print(response.json()["reply"])
