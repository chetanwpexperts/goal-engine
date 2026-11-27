"""Create the local configuration and optionally manage a read-only account."""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

def save(path, config):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
        temp = handle.name
    os.replace(temp, path)

def new_config(port, password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=16384, r=8, p=1, dklen=64)
    return {"version": 1, "port": port,
            "screen_password": {"algorithm": "scrypt", "salt": salt.hex(),
                                "hash": digest.hex(), "n": 16384, "r": 8, "p": 1},
            "database": None, "api_keys": [], "updates": {"enabled": True},
            "alerts": {"email": None}}

def approved(question):
    return input(question + " [y/N] ").strip().lower() in ("y", "yes")

def execute(kind, sql):
    pg = kind == "postgresql"
    candidates = ("psql",) if pg else (("mariadb", "mysql") if kind == "mariadb" else ("mysql", "mariadb"))
    client = next((shutil.which(name) for name in candidates if shutil.which(name)), None)
    if not client:
        raise SystemExit("Please install the local database client or ask your database administrator.")
    user = input("Database administrator account [" + ("postgres" if pg else "root") + "]: ").strip()
    user = user or ("postgres" if pg else "root")
    password = getpass.getpass("Database administrator password: ")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG") and k.upper() != "MYSQL_PWD"}
    if pg:
        env["PGPASSWORD"] = password
        cmd = [client, "-X", "-w", "-U", user, "-h", "127.0.0.1", "-p", "5432", "-d", "postgres",
               "-v", "ON_ERROR_STOP=1", "--single-transaction", "-f", "-"]
    else:
        env["MYSQL_PWD"] = password
        cmd = [client, "--no-defaults", "--protocol=TCP", "--host=127.0.0.1",
               "--port=3306", "--user=" + user, "--batch", "--silent", "--connect-timeout=5"]
    result = subprocess.run(cmd, input=sql + "\n", text=True, capture_output=True, env=env, timeout=60)
    if result.returncode:
        raise SystemExit("The database could not apply the approved SQL. Please ask its administrator to review it.")

def create_account(path, config):
    kind = {"1": "mysql", "2": "mariadb", "3": "postgresql"}.get(
        input("Read-only database account: 0 Skip, 1 MySQL, 2 MariaDB, 3 PostgreSQL: ").strip())
    if not kind:
        return
    pg = kind == "postgresql"
    name = input("Database name: ").strip()
    if not 1 <= len(name) <= 128:
        raise SystemExit("Please use a database name of 1 to 128 characters.")
    password = secrets.token_urlsafe(32)
    if pg:
        salt = secrets.token_bytes(16)
        salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
        stored = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
        server = hmac.digest(salted, b"Server Key", "sha256")
        b64 = lambda value: base64.b64encode(value).decode("ascii")
        verifier = "SCRAM-SHA-256$4096:" + b64(salt) + "$" + b64(stored) + ":" + b64(server)
        create = ("CREATE ROLE dbaba_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                  "NOREPLICATION NOBYPASSRLS PASSWORD '<generated password verifier>';")
        actual = create.replace("<generated password verifier>", verifier)
        grants = ("GRANT pg_read_all_data, pg_monitor TO dbaba_ro;\n"
                  "ALTER ROLE dbaba_ro SET default_transaction_read_only = on;")
        print("These roles cover the cluster. Review PUBLIC schema permissions, especially on PostgreSQL 14 and older.")
    else:
        account = "'dbaba_ro'@'127.0.0.1'"
        create = f"CREATE USER {account} IDENTIFIED BY '<generated password>';"
        actual = create.replace("<generated password>", password)
        grants = f"GRANT SELECT, PROCESS, SHOW VIEW, REPLICATION CLIENT ON *.* TO {account};"
        print("These read and monitoring privileges cover all databases on this server.")
    print("SQL to run (the generated credential is kept private):\n" + create + "\n" + grants)
    if not approved("Create this read-only database account?"):
        return
    execute(kind, actual + ("\n" + grants if pg else ""))
    config["database"] = {"type": kind, "host": "127.0.0.1", "port": 5432 if pg else 3306,
                          "name": name, "user": "dbaba_ro", "password": password}
    save(path, config)
    if not pg:
        execute(kind, grants)
    print("Read-only database account saved.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("action", choices=("create", "drop"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()
    if args.action == "create":
        if args.path.exists():
            return
        password = os.environ.get("DBABA_PASSWORD", "") if args.non_interactive else getpass.getpass("Choose a screen password (at least 8 characters): ")
        if len(password) < 8 or (not args.non_interactive and password != getpass.getpass("Enter it again: ")):
            raise SystemExit("Please enter matching passwords of at least 8 characters.")
        config = new_config(args.port, password)
        save(args.path, config)
        if not args.non_interactive:
            create_account(args.path, config)
    elif args.path.exists():
        db = json.loads(args.path.read_text(encoding="utf-8")).get("database") or {}
        if db.get("user") != "dbaba_ro":
            return
        kind = db.get("type")
        if (db.get("host") != "127.0.0.1" or kind not in ("mysql", "mariadb", "postgresql")
                or db.get("port") != (5432 if kind == "postgresql" else 3306) or db.get("socket")):
            print("Please ask your database administrator to review this account separately.")
            return
        sql = "DROP ROLE dbaba_ro;" if kind == "postgresql" else "DROP USER 'dbaba_ro'@'127.0.0.1';"
        print("SQL to run:\n" + sql)
        if approved("Drop this database account?"):
            execute(kind, sql)
        else:
            print("The database account is kept.")

if __name__ == "__main__":
    main()
