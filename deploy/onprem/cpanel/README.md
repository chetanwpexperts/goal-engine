# DBaba on cPanel (lite)

Use a private, dedicated HTTPS subdomain at its root, not a URL subfolder.
This edition uses Passenger and does not start Uvicorn, a separate listener,
cron jobs or a scheduler. Apache must enforce authentication for every URL,
including static files and API routes. Do not expose an unprotected copy.

## Installation

1. In cPanel create a dedicated subdomain and enable its SSL certificate.
   Keep its document root separate from the private application directory.
2. Upload and extract the complete DBaba release into ~/dbaba-app, outside
   public_html and outside every domain's document root. Do not upload a
   local .env, credentials, database or data directory from another machine.
3. In Setup Python App create an app with Python 3.11 or newer, application
   root dbaba-app, application URL the dedicated subdomain at /, startup
   file passenger_wsgi.py, entry point application. Stop the app while
   preparing it. If the host cannot supply a private app root, do not proceed.
4. Copy deploy/onprem/cpanel/passenger_wsgi.py to ~/dbaba-app/passenger_wsgi.py.
   In the terminal activate the virtual environment using the exact command
   shown in Setup Python App, then run:

       cd ~/dbaba-app
       python -m pip install -r deploy/onprem/cpanel/requirements-cpanel.txt
       chmod 700 ~/dbaba-app
       mkdir -p data
       chmod 700 data

   The separate requirements file pins a2wsgi 1.10.10 (Apache-2.0 license).
   The main requirements file is unchanged. If dependency wheels are not
   available or the host refuses installation, contact the host.
5. Create the password file outside all document roots. In the terminal run
   (choose a separate Apache password when prompted):

       umask 077
       htpasswd -cB "$HOME/.dbaba-htpasswd" dbaba
       chmod 600 "$HOME/.dbaba-htpasswd"

   If Apache cannot read the file, ask the host to arrange restricted access;
   do not make it publicly readable. Alternatively ask the host to create
   the same private password file using its supported authentication tools.
6. Append the supplied .htaccess directives to the subdomain document
   root's existing .htaccess, preserving Passenger's generated directives.
   Replace /home/REPLACE_ACCOUNT/.dbaba-htpasswd with the actual absolute
   private password-file path. Do not put this file in public_html.
   The host must support Apache 2.4 authentication and HTTPS expressions,
   and must forward HTTPS and REMOTE_USER to Passenger. If it does not,
   ask the host for help before enabling the app.
7. Before starting Passenger, create the S4 screen password and allow only
   your dedicated subdomain. In the activated virtual environment, from
   ~/dbaba-app, run the following (replace the example hostname):

       python - <<'PY'
       from getpass import getpass
       from pathlib import Path
       from app.local.config import Config
       import os
       path = Path("dbaba.json")
       if path.is_symlink():
           raise SystemExit("Settings must not be a symlink")
       cfg = Config(path)
       os.chmod(path, 0o600)
       host = "dbaba.example.com"
       def configure(d):
           if not cfg.has_password():
               pw = getpass("DBaba screen password: ")
               if not 8 <= len(pw) <= 4096 or pw != getpass("Repeat password: "):
                   raise SystemExit("Passwords must match and contain 8–4096 characters")
               d["screen_password"] = Config.password_record(pw)
           d["allowed_hosts"] = [host]
       cfg.update(configure)
       PY

   This creates the installer-format settings if missing, preserves an
   existing screen password and stores only its scrypt record. Do not put
   the password in environment variables or source code. Keep dbaba.json
   private (mode 0600). If Passenger starts before this step, it creates
   missing settings but returns 503 until a screen password is configured.
8. Restart the Python App. Open the HTTPS URL, pass Apache Basic Auth,
   then log in with the DBaba screen password. Complete the three-step
   database setup using a read-only database account. No setup token is
   supplied at startup. The configured port is S4 metadata, not a listener.

## Manual verification

- Request the HTTP URL without credentials: it must deny access (or the
  hosting SSL configuration must redirect to HTTPS before authentication).
- Open HTTPS in a fresh private browser: expect a Basic Auth challenge.
  Try a wrong Apache password: expect 401. Enter the correct password: expect the DBaba login screen.
- Repeat without credentials for /api/health and /local/local.js; neither
  may be accessible. With correct credentials /api/health must return
  an object containing {"ok":true}. With Apache authentication but no
  DBaba session, /api/state must return 401. A wrong screen password must
  return 401; a correct one must set a Secure, HttpOnly dbaba_session cookie.
  With that cookie /api/state must return 200. Complete setup and check a
  simple MySQL query. An unlisted Host must return 403.
- Verify the private password file, application source and data are not
  downloadable from any domain. Check the Passenger error log if needed.
- Confirm no cron entry or separate DBaba server was created.
- Stop the Python App if any security test fails.

This is a request-driven edition. Scheduled jobs, persistent background
work and WebSockets are not supported. Passenger does not run ASGI lifespan
events through this adapter; S4 initializes its working database
and local request model lazily through S4, without starting its periodic scheduler. Full application behavior must be verified on the target host.
S4's _env function forces local mode and clears AI keys. The working database is kept in the private data directory. Passenger workers hold sessions and login throttling in memory; use a single worker where the host supports it. Worker recycling can require logging in again. Manual scans and setup-triggered background work may be interrupted by recycling. Keep the application private and
do not configure external integrations. To remove it, stop and delete the
Python App, remove its dedicated authentication configuration and, after
backing up your data, delete the private app (including dbaba.json) and password file.
