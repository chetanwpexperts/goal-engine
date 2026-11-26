# DBaba on-premises

## Install on your server

DBaba runs locally on your Linux server. The installer supports Ubuntu 22.04 and 24.04, Debian 12, AlmaLinux 9 and Rocky Linux 9, with systemd and Python 3.11 or newer.

Install Python first if needed:

| Distribution | Command |
| --- | --- |
| Ubuntu 22.04 | `sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv` |
| Ubuntu 24.04 / Debian 12 | `sudo apt-get update && sudo apt-get install -y python3 python3-venv` |
| AlmaLinux 9 / Rocky Linux 9 | `sudo dnf install -y python3.11 python3.11-pip` |

You also need curl, working systemd, and a local database client if you want the installer to create a database account.

### Install

Use the published installer command. Replace `<SERVER>` with the HTTPS release server address:

```bash
curl -fsSL <SERVER>/dbaba/install.sh | bash
```

For a system-wide installation, review the published script and use:

```bash
curl -fsSL <SERVER>/dbaba/install.sh | sudo bash
```

A regular account installs to `~/dbaba` and enables a user systemd service with lingering, so the service starts on boot. If lingering needs administrator approval, the installer prints the command to enable it. A root installation uses `/opt/dbaba` and a dedicated unprivileged system service account.

The installer asks for a screen port (8765 by default), a screen password, and your database choice. It downloads the release tarball, creates a Python virtual environment, installs the release's requirements, and starts the service. Use a trusted release server. The release archive is fetched over HTTPS; release publishing and artifact signing are separate deployment responsibilities.

From a trusted repository checkout, you can install directly for development:

```bash
DBABA_SRC=. bash deploy/onprem/install.sh
```

### Database access

The installer checks local TCP ports 3306 and 5432, common Unix sockets, and the available mysql, mariadb and psql clients. Choose MySQL, MariaDB, PostgreSQL, or set up the database later. For a local account, supply the database name and optionally its socket path (a socket directory for PostgreSQL).

The installer offers to create a new account named `dbaba_ro`. It shows the SQL with the generated credential redacted and asks for approval before executing it. Administrative database access via sudo (or the existing root session) is used for account creation and removal.

MySQL and MariaDB receive SELECT, PROCESS, SHOW VIEW and REPLICATION CLIENT on the server. These privileges give broad read and monitoring access; the saved database name is the database DBaba should read, not a restriction on these grants. The account is created for 127.0.0.1 for TCP, or localhost for Unix socket access.

PostgreSQL 14 or newer receives pg_read_all_data and pg_monitor, with read-only transactions as the role default and no superuser, database creation, role creation, replication or row-security bypass privileges. These roles cover the cluster. Existing PUBLIC grants, executable database functions and server access rules also affect what a PostgreSQL login can do. Have your administrator review those permissions for a strictly read-only deployment. The installer grants no data-changing privileges. The SQL preview includes `ALTER ROLE dbaba_ro SET default_transaction_read_only = on;`, which sets the transaction default rather than enforcing a privilege boundary.

On PostgreSQL 14 and older, the public schema normally grants CREATE to PUBLIC, so every role can create tables there unless the administrator has revoked that permission. Databases upgraded from those versions can retain the same permission. Ask your database administrator to check the public schema in each relevant database. If appropriate, the administrator can review and run:

```sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

This affects other roles as well. The installer leaves existing PUBLIC privileges unchanged and never executes this REVOKE.

The installer creates a new account rather than changing an existing account with the same name. If you prefer to arrange access yourself, choose to set up the database later. You can configure your existing read-only login when the local application supports database setup.

### Open DBaba from your laptop

The service listens on the server's loopback interface. Open an SSH tunnel, replacing the port if you chose another and replacing the SSH user and server address:

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

Open http://127.0.0.1:8765 in your laptop's browser while the tunnel is open.

The installer uses `python -m app.local serve --port` when the local entry point is included in the release. In its absence, it starts the existing application with Uvicorn on 127.0.0.1. **The fallback screen has no password protection yet.** Use the SSH tunnel only and a server accessible to trusted users; other users on that server can reach its loopback interface. Screen authentication becomes available with the local entry point.

The service runs in local processing mode, with its application database in SQLite and external model-provider settings cleared. Outbound connections remain available for database connections, signed knowledge updates and configured alert emails. The fallback is a development installation; the local application is responsible for its feature-level access controls and outbound-data policy.

### Settings and data

The installation directory contains:

- `dbaba.json` (mode 600): version, screen port, scrypt password hash and random salt in hex, database settings, API key hashes, update preferences and alert preferences.
- `data/`: private application data, including the local SQLite database.
- `release/`: active code and its `.venv/` Python environment. Links inside the release point to the stable data and configuration paths.
- `uninstall.sh`: a stable uninstall script outside the release.

The screen password is saved only as a hash. A generated database password is saved in the private configuration for the application to use. Keep the configuration and data directory private, including any backups. The initial configuration enables updates, contains an empty API key list and sets the alert email to null.

### Update

Run the same install command again using the same account and installation mode. The installer prepares `.release-new/`, builds its own Python environment, and checks that the application imports before stopping the current service. The existing release continues serving during preparation.

After preparation, the installer stops the service, waits up to 30 seconds for the saved port to have no listeners, moves the current code to `.release-old/`, activates the staged code as `release/`, and starts the service. Once health and loopback checks pass, it removes the old code. Existing flat-layout installations are migrated during this step.

If preparation fails, the current service is left running. If activation or health checking fails, the installer restores the previous code and service unit, restarts it, checks its health, and exits with a nonzero status. If recovery itself needs attention, the saved release and service recovery files are retained and the installer prints their location.

The installer leaves `dbaba.json` and `data/` at their existing paths throughout. Back up both before an update: code rollback does not reverse application-level changes to the database made during startup. Allow a maintenance window for the service switch. A sudden power loss or forced process termination may require manual recovery.

A new installation checks port availability before setup. To change the saved port, stop the service, check the new port is free and update the configuration before reinstalling.

### Service commands

For a user installation:

```bash
systemctl --user status dbaba.service
systemctl --user restart dbaba.service
journalctl --user -u dbaba.service -n 50
```

For a system installation:

```bash
sudo systemctl status dbaba.service
sudo systemctl restart dbaba.service
sudo journalctl -u dbaba.service -n 50
```

### Uninstall

Back up anything you want to retain. Run the matching uninstall command:

```bash
bash ~/dbaba/uninstall.sh
```

Or, for a system installation:

```bash
sudo bash /opt/dbaba/uninstall.sh
```

The uninstaller asks before removing the installation and its data. If a saved local dbaba_ro account is present, it shows the DROP statement and asks separately before removing that database account. Choose to keep the account if other applications use it; retain its password securely beforehand if needed. Remote or custom-port accounts can be reviewed separately by their administrator.

The service is stopped and disabled, the unit and installation directory are removed, and the dedicated system account is removed if the installer created it. User lingering is retained for other user services.

### Integration test

On a disposable Linux Docker host that supports systemd containers:

```bash
bash tests/onprem/test_install.sh
```

The test builds an Ubuntu 24.04 container with systemd, installs from the checkout without a database account, checks the health response and loopback listener, repeats the installation to verify preservation of settings and data, rejects a broken import, checks rollback after an unhealthy release, then uninstalls and checks cleanup. It uses a privileged test container with a private cgroup namespace; use a dedicated test host. The test publishes no service ports.

The Docker test covers the system installation path. User services, additional distributions, database grants and the future local entry point require their own integration checks.
