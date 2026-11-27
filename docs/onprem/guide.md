# DBaba: your first steps

DBaba runs on your own computer or server. It reads database status and helps you understand problems. This guide is for the local edition described in the [local contract](../dbaba-local-contract.md). Use a trusted release that includes `app/local/`; older fallback releases do not have screen password protection.

## 1. Install DBaba

Choose one method. Start with a trusted, complete checkout or extracted release of this repository. Run the commands from its top-level folder.

### Linux

1. Use Ubuntu 22.04/24.04, Debian 12, AlmaLinux 9 or Rocky Linux 9 with systemd. Install Python 3.11 or newer, virtual environment support and curl. See the [Linux installer instructions](../../deploy/onprem/README.md) for commands for your system.
2. Open a terminal and run:

   ```bash
   DBABA_SRC=. bash deploy/onprem/install.sh
   ```

3. Accept port 8765 (or choose a free port). Choose a screen password of at least eight characters.
4. Choose to set up the database later if you want the restricted database account described below.

A normal installation lives in `~/dbaba` and uses a user systemd service. Boot-time startup may require administrator approval for lingering. For a system-wide installation, use `sudo env DBABA_SRC=. bash deploy/onprem/install.sh`; it lives in `/opt/dbaba` and runs under a dedicated service account.

### Docker

1. Install Docker and its Compose plugin.
2. In Bash, enter a private screen password of at least eight characters:

   ```bash
   read -rs -p "Screen password: " DBABA_PASSWORD
   echo
   export DBABA_PASSWORD
   docker compose -f deploy/onprem/docker/docker-compose.example.yml up -d --build
   unset DBABA_PASSWORD
   ```

3. Keep the example's port mapping on `127.0.0.1:8765:8765`. Never publish the screen on all host interfaces. Use a trusted Docker network.

The named volume keeps your settings and data. For a database on the Docker host, use `host.docker.internal` as its address; for another container on the same trusted network, use its service name. Your database administrator must allow connections from that network without exposing the database publicly. See the [Docker instructions](../../deploy/onprem/docker/README.md).

### Windows

1. Install Python 3.11 or newer. For boot-time startup, install Python for all users.
2. Open PowerShell in the release folder. Run:

   ```powershell
   .\deploy\onprem\windows\install.ps1
   ```

3. Choose the screen port and password when asked. Choose database setup later if creating your own read-only account.

Running as administrator installs to `C:\ProgramData\DBaba` and starts DBaba at boot. Running as a normal user installs to `%LOCALAPPDATA%\DBaba` and starts it when you sign in. The installer uses Windows Task Scheduler and does not add an inbound firewall rule.

## 2. Open the screen safely

On the machine running DBaba, open [http://127.0.0.1:8765](http://127.0.0.1:8765). Use your chosen port if different. Log in with the screen password, not your database password.

If DBaba is on a remote server, open a terminal on your own computer and run:

```bash
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 user@server
```

Replace `user@server` with your SSH login. Verify the server's host-key fingerprint with your administrator before accepting it. Keep the terminal open and visit the same local browser address. If you changed the server's screen port, replace the second 8765; if your computer's port is busy, change the first and use that port in your browser.

Never open the DBaba port to the internet, bind the host screen to all interfaces, or add a public reverse proxy. A password is not a substitute for private access. If the installer warns that the release has no screen password protection, upgrade to a release with the local screen before following this guide.

## 3. Create a read-only database account

Ask your database administrator to run the SQL below, not DBaba. Use a new account and replace the example database name and password with your own values. Keep the password private. Do not give DBaba an administrator account.

### MySQL or MariaDB

For DBaba connecting by TCP from the database machine:

```sql
CREATE USER 'dbaba_ro'@'127.0.0.1' IDENTIFIED BY 'REPLACE_WITH_STRONG_PASSWORD';
GRANT SELECT ON `your_database`.* TO 'dbaba_ro'@'127.0.0.1';
GRANT PROCESS ON *.* TO 'dbaba_ro'@'127.0.0.1';
GRANT SELECT ON performance_schema.* TO 'dbaba_ro'@'127.0.0.1';
```

SELECT allows reading the chosen database. PROCESS allows reading server-wide process/status information, including other sessions' query text. With performance_schema enabled and collecting statistics, the full report (S5) uses this extra SELECT grant to show slow queries and unused indexes. Without access, it says it cannot see them rather than reporting that everything is fine.

**Do not grant SELECT on `mysql.user`.** It holds password hashes. The full report will show "Accounts not checked", which is expected.

For Docker or a separate server, ask the administrator to replace the account host with the actual trusted connection source; do not use a wildcard host. Do not grant INSERT, UPDATE, DELETE or administrative privileges.

### PostgreSQL

Run the following as an administrator connected to the database DBaba should inspect. This example uses the public schema; repeat the schema-specific grants for other required schemas.

```sql
CREATE ROLE dbaba_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
GRANT CONNECT ON DATABASE your_database TO dbaba_ro;
GRANT USAGE ON SCHEMA public TO dbaba_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dbaba_ro;
GRANT pg_monitor TO dbaba_ro;
ALTER ROLE dbaba_ro SET default_transaction_read_only = on;
```

CONNECT and USAGE let the account reach the database and schema; SELECT lets it read existing tables and views. pg_monitor provides monitoring access. For slow queries in the full report (S5), PostgreSQL also needs the `pg_stat_statements` extension: ask your administrator to include it in `shared_preload_libraries`, restart PostgreSQL and run `CREATE EXTENSION pg_stat_statements;` in the monitored database. The report itself explains this requirement. Ask your administrator to grant SELECT on new tables when needed. The read-only transaction default is an extra precaution, not a permission boundary.

Existing PUBLIC permissions, role memberships and executable functions can still allow changes. PostgreSQL 14 and older (including upgraded databases) may allow everyone to create objects in the public schema. Ask your administrator to review these permissions before connecting. Where appropriate, they can revoke CREATE from PUBLIC with the following SQL, which affects other users too:

```sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

These manual grants are intentionally narrower than the installers' optional automatic account creation (which can grant broader read and monitoring access). Some findings may be unavailable with restricted permissions. Do not add write privileges to remove a warning.

## 4. Complete the three-step setup

If you chose database setup later, log in to the screen and follow these steps. If the installer already saved a database, the screen may open directly in Chat instead.

1. **Which database:** choose MySQL, MariaDB or PostgreSQL. Use **Find it for me** to look for a database on the machine running DBaba.
2. **Connect:** enter the database host, port (usually 3306 for MySQL/MariaDB or 5432 for PostgreSQL), database name, read-only user and its password. Click **Test connection**. If it fails, check the message and ask your administrator for help. If it warns that the account can change data, stop and have its permissions corrected. Click **Save and start the first scan** when ready.
3. **First scan:** wait while DBaba reads the database status and catalog. The Report opens when the first scan is ready.

The browser setup assumes the installer has already set a screen password. A fresh backend without one needs setup using the local setup token and a new password (see the [local API guide](api.md)); the browser login alone cannot initialize that password. Keep setup tokens and API keys private.

If the same database moves to a new server or an SSH tunnel gets a new local port, an authenticated `POST /api/database` (for example from a script or using the `dbaba` command's API key) can update its address without repeating setup; DBaba tests the connection before saving and keeps the old settings if the test fails.

## 5. Read your report and alerts

Open **Report**. Before the first scan finishes, there is no score. Afterward, you can see a score out of 100, the scan time and findings. The report has eight sections, with the worst findings first:

- **Now (`now`):** connections compared with the limit, queries running over a minute, who is waiting for whom, and deadlocks.
- **Slow queries (`slow`):** the slowest queries from `performance_schema` or `pg_stat_statements`, checked against the real tables, or an explanation of why they cannot be seen.
- **Indexes (`indexes`):** duplicate indexes, indexes covered by another index, unused indexes after at least seven days of statistics, foreign keys without an index, and large tables read without an index.
- **Tables (`tables`):** missing primary keys, wasted space, dead rows, stale statistics, and the biggest tables.
- **Settings (`settings`):** memory set aside for data compared with the machine's memory, connections near the limit, and the slow-query log.
- **Backups (`backups`):** whether point-in-time recovery is possible; this does not mean DBaba has made or tested a backup.
- **Security (`security`):** whether DBaba's account can change data, remote root access, empty passwords, anonymous users, open network access, MD5 authentication, and versions no longer supported.
- **Server (`server`):** the database server version.

After the first report, each report is compared with the previous one: see the previous score and which warnings or critical findings are new or gone. A new critical finding also raises an alert.

Read critical findings first, then warnings and information. Each finding explains what was found and why it matters. Use **Ask about this** for an explanation. Suggested SQL is not executed by this release; ask your administrator to review it before applying any change. Use **Scan now** to request another scan or **Download as HTML** to save the report privately.

Open **Automation** to see alerts and jobs. **Mark as seen** acknowledges an alert; it does not fix the problem. An information alert is raised when a detected problem clears. The health job runs every five minutes; the daily scan defaults to 02:40 local server time. You can switch jobs on or off and change the daily scan time. DBaba's own automated backups are not part of S5.

## 6. Use the dbaba command (optional)

If you prefer a terminal, install the CLI from the trusted repository in a Python virtual environment using `python -m pip install .`. Keep the server running. Set `DBABA_URL` to your loopback screen address if using a different port. Authenticated commands require an API key, supplied privately through `DBABA_API_KEY` or the private key file described in the [API guide](api.md).

To create an API key, log in to the screen, open **Automation**, find **API keys**, enter a name and click **Create API key**. The key is shown only once: copy it then and store it privately. To disable a key, click **Revoke** in the same list and confirm when asked; the key is refused from the next request. Only a logged-in screen can create or revoke keys; an API key cannot do either.

```bash
dbaba status
dbaba report
dbaba report --scan
dbaba report --html report.html
dbaba alerts
dbaba jobs
dbaba ask "What is an index?"
dbaba check query.sql
```

The scan runs in the background; check the report again later. Keep exported reports private. Use `dbaba --help` for available commands and `dbaba serve --help` for starting a separate local engine. Do not start another engine on a port already used by your installation.

## 7. Use the desktop app (optional)

The desktop app can open your local or remote DBaba screen in its own window. See the [desktop instructions](../../desktop/README.md) for platform requirements and building the app.

1. Start your installed DBaba server.
2. Add a connection with a name and the screen port.
3. For a remote server, supply its SSH host, user and private key path. Use ssh-agent for a passphrase-protected key. Verify an unknown fingerprint with your administrator; stop if the trusted fingerprint changes.
4. Open the connection and log in with your screen password. An optional API key is stored in the operating system's keychain and enables alert polling. Close the connection when finished.

No SSH password is collected. Warning and critical desktop notifications depend on the operating system and an authenticated connection.

## 8. Update DBaba

Back up your private configuration and data before updating. Obtain a trusted newer release.

- Linux: run the same installer command from the new release with the same account and installation mode. It stages the new code and attempts rollback if activation or health checks fail.
- Docker: from the new release folder, run the Compose commands below using the same Compose project/volume as before. Supply the screen-password environment variable privately if needed by Compose.
- Windows: run the same PowerShell installer from the new release, using the same administrator or normal-user mode. It keeps the existing configuration and data.

```bash
docker compose -f deploy/onprem/docker/docker-compose.example.yml down
docker compose -f deploy/onprem/docker/docker-compose.example.yml up -d --build
```

Do not add `-v` during an update. After updating, open the screen and check the connection and report. Code rollback is not a substitute for a data backup.

## 9. Uninstall DBaba

Back up anything you want to keep. Removing an installation can delete its settings, chat history, reports and alerts.

Linux, normal user:

```bash
bash ~/dbaba/uninstall.sh
```

Linux, system-wide:

```bash
sudo bash /opt/dbaba/uninstall.sh
```

Windows, normal user (PowerShell):

```powershell
& "$env:LOCALAPPDATA\DBaba\uninstall.ps1"
```

Windows, administrator (PowerShell):

```powershell
& "$env:ProgramData\DBaba\uninstall.ps1"
```

Use the same installation mode. Read the confirmation prompts, including any separate prompt about removing a database account. Keep the account if another application needs it.

Docker:

```bash
docker compose -f deploy/onprem/docker/docker-compose.example.yml down
```

This stops and removes the containers but keeps the saved volume. Only if you deliberately want to delete the saved data, use the same command with `-v` after backing up.

## 10. What DBaba never does

- The local engine does not use AI/LLM providers or require paid AI APIs. It clears provider keys and forces local processing.
- It does not send your database contents, chat history, reports or alerts to a cloud service. The local files stay with your installation; you choose whether to open them through a private connection or export a report. Installing or updating software and connecting to your chosen database are separate network activities.
- It does not change your database without approval. In the current S4 local screen, fixes are suggestions only: there is no automatic execution, even after approval. The installers' optional database account creation/removal is a separate operation with confirmation.

Keep `dbaba.json`, its backups, database passwords, API keys and the data folder private. The screen password is stored as a hash, but the database password is stored in the private configuration so DBaba can connect.

For technical details, see the [local contract](../dbaba-local-contract.md) and [API guide](api.md).
