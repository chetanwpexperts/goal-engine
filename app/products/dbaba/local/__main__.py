"""python -m app.local serve --port N --config PATH [--host 127.0.0.1]
   python -m app.local reset --config PATH        (setup again: the next start prints a new setup token)

The settings file defaults to $DBABA_CONFIG, else ./dbaba.json; it is created (mode 0600) when missing. DBaba's own
working database defaults to data/dbaba.db next to it. No AI provider is ever used: their keys are cleared here."""

import argparse
import ipaddress
import os
import secrets
import sys
from pathlib import Path


def _env(config_path: Path) -> None:
    data = config_path.parent / "data"
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{data / 'dbaba.db'}")
    # forced, not defaulted: an environment that says ENGINE_MODE=ai (or small daily caps, or the website's switches)
    # must never change how DBaba answers on someone's own machine
    for k, v in {"SITE_MODE": "open", "ENGINE_MODE": "ml", "EXPOSE_INTERNALS": "0", "DBABA_LOCAL": "1",
                 "AGENT_SCHEDULER": "off", "VISITOR_DAILY_CAP": "100000", "AI_DAILY_CAP": "100000", "COOKIE_SECURE": "false",
                 "DB_AREA_LOCK": "off"}.items():
        os.environ[k] = v
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "AI_PROVIDER_ORDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ[k] = ""                        # no third-party AI, whatever the environment says


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int)
    s.add_argument("--config", default=os.environ.get("DBABA_CONFIG") or "dbaba.json")
    s.add_argument("--host", default="127.0.0.1")
    r = sub.add_parser("reset")
    r.add_argument("--config", default=os.environ.get("DBABA_CONFIG") or "dbaba.json")
    a = ap.parse_args(argv)
    config = Path(a.config).expanduser().resolve()
    _env(config)
    from app.products.dbaba.local.config import Config, ConfigError

    if a.cmd == "reset":
        try:
            Config(config).update(lambda d: d.__setitem__("setup_done", False))
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
        print("Setup will start again on the next start.")
        return 0
    try:
        cfg = Config(config, a.port or 8765)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    port = a.port or int(cfg.data.get("port") or 8765)
    if not 1024 <= port <= 65535:
        print("Choose a port from 1024 to 65535.", file=sys.stderr)
        return 2
    try:
        loop = a.host == "localhost" or ipaddress.ip_address(a.host).is_loopback
    except ValueError:
        print("--host must be an IP address (127.0.0.1, or 0.0.0.0 inside a container).", file=sys.stderr)
        return 2
    if not loop:
        print(f"DBaba listens on {a.host}:{port}. Publish this port only on 127.0.0.1 (or reach it through an SSH tunnel).",
              file=sys.stderr, flush=True)
    token = None if cfg.setup_done else secrets.token_urlsafe(24)
    if token:
        print(f"DBABA-SETUP-TOKEN {token}", flush=True)

    from app.products.dbaba.local.server import make_app

    app = make_app(str(config), port, token, announce=lambda line: print(line, flush=True))
    app.state.jobs.start()
    import uvicorn

    uvicorn.run(app, host=a.host, port=port, log_level="warning", access_log=False, proxy_headers=False,
                server_header=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
