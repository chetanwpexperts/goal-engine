"""Local S4 API examples. Usage: python examples/local_contract.py GROUP."""
import json, os, re, sys
from urllib import request
from http.cookiejar import CookieJar

base = os.environ.get("DBABA_URL", "http://127.0.0.1:8765").rstrip("/")
if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", base) or not 1 <= int(base.rsplit(":",1)[1]) <= 65535:
    raise SystemExit("Use a local loopback URL.")
class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None
opener = request.build_opener(request.ProxyHandler({}), NoRedirect(), request.HTTPCookieProcessor(CookieJar()))
key = os.getenv("DBABA_API_KEY", "")
token = os.getenv("DBABA_SETUP_TOKEN", "")
def call(path, method="GET", body=None, setup=False, html=False):
    headers = {"Content-Type":"application/json"}
    if setup: headers["X-DBaba-Setup"] = token
    elif key: headers["Authorization"] = "Bearer " + key
    with opener.open(request.Request(base+path, headers=headers, method=method, data=None if body is None else json.dumps(body).encode()), timeout=30) as response:
        return response.read() if html else json.load(response)
def identifier(name):
    value = os.environ[name]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value): raise ValueError("Invalid ID")
    return value
def main(group):
    if group == "health": print(call("/api/health"))
    elif group == "state": print(call("/api/state"))
    elif group == "chat":
        call("/api/new-chat","POST")
        print(call("/api/message","POST",{"text":"How do indexes work?"})["reply"])
    elif group == "alerts": print(call("/api/alerts/"+identifier("DBABA_ALERT_ID")+"/ack","POST"))
    elif group == "report":
        print(call("/api/report"))
        print(call("/api/report/scan","POST"))
        with os.fdopen(os.open("report.html",os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),"wb") as f: f.write(call("/api/report.html",html=True))
    elif group == "jobs":
        print(call("/api/jobs"))
        print(call("/api/jobs/"+identifier("DBABA_JOB_ID"),"POST",{"enabled":True,"at":"09:30"}))
    elif group == "setup":
        body=json.load(sys.stdin)
        print(call("/api/setup/detect","POST",setup=True))
        test=call("/api/setup/test","POST",{k:v for k,v in body.items() if k!="screen_password"},setup=True)
        if not test["ok"]: raise ValueError("Database test failed")
        saved=call("/api/setup/save","POST",body,setup=True)
        if not saved["ok"]: raise ValueError("Setup failed")
        with os.fdopen(os.open("dbaba-key",os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),"w") as f: f.write(saved["api_key"])
        print("Setup saved; key stored locally.")
    elif group == "session":
        body=json.load(sys.stdin)
        call("/api/login","POST",{"password":body["password"]})
        print(call("/api/state"))
        call("/api/logout","POST")
    elif group == "reset":
        if os.getenv("DBABA_CONFIRM_RESET")!="yes": raise ValueError("Set DBABA_CONFIRM_RESET=yes to reset setup")
        print(call("/api/setup/reset","POST"))
    else: raise ValueError("Unknown group")
if __name__ == "__main__":
    try: main(sys.argv[1])
    except Exception: sys.exit("Local request failed; check configuration and credentials.")
