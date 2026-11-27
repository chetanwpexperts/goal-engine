# Run DBaba with Docker

Publish this port only on 127.0.0.1 (e.g. -p 127.0.0.1:8765:8765); never on all interfaces.

Run these commands from the repository root with Docker and the Compose plugin installed.

Choose a screen password of at least eight characters. In Bash, enter it without showing it on screen:

```sh
read -rs -p "Screen password: " DBABA_PASSWORD
echo
export DBABA_PASSWORD
docker compose -f deploy/onprem/docker/docker-compose.example.yml up -d --build
unset DBABA_PASSWORD
```

Open http://127.0.0.1:8765 on the Docker host.

**Important:** if this release does not include the local screen, it has no password protection yet. Use the SSH tunnel only, and keep the server accessible to trusted users. For a remote server, run this on your computer:

```sh
ssh -N -L 8765:127.0.0.1:8765 user@server
```

Then open http://127.0.0.1:8765 on your computer. Never publish the screen on all host interfaces. The container listens on its own network so Docker can forward the loopback port; use a trusted Docker network.

## Connect a database

Use an existing read-only database account. Before the first start, set the following environment variables as needed:

| Variable | Value |
| --- | --- |
| DBABA_DB_TYPE | mysql, mariadb or postgresql; leave empty to set up later |
| DBABA_DB_HOST | host.docker.internal for a database on the host, or the service name for another container |
| DBABA_DB_PORT | Defaults to 3306 or 5432 for PostgreSQL |
| DBABA_DB_NAME | Database name |
| DBABA_DB_USER | Read-only account |
| DBABA_DB_PASSWORD | Account password; enter privately |

The example includes the host gateway mapping for Linux. The host database must accept connections from the Docker network; restrict its listener, firewall and account permissions to trusted sources. Do not expose the database publicly.

For a database in another container, join both services to the same trusted Docker network and use the database service name as the host. No database port needs to be published.

## Settings and data

The named volume mounted at /data keeps dbaba.json and dbaba.db. The first start writes the configuration with mode 0600, using the same password hashing format as the Linux installer. Later starts keep the saved configuration and port, even if environment variables change. The fallback server does not enforce the saved screen password.

Docker administrators can inspect container environment variables. Do not share container inspection output or configuration files. The database password is stored in the private configuration file, as in the Linux installer.

The default screen port is 8765. For a different port on a fresh volume, set DBABA_PORT and change both sides of the Compose port mapping while retaining 127.0.0.1. To use a different host port only, change the middle number.

The container runs as UID/GID 10001. A named volume is recommended. For an existing bind-mounted directory, make it writable by that account before starting.

## Stop or update

```sh
docker compose -f deploy/onprem/docker/docker-compose.example.yml down
docker compose -f deploy/onprem/docker/docker-compose.example.yml up -d --build
```

Do not use down -v unless you intend to delete the saved data. Back up the volume before updating. The image uses local processing with external AI providers disabled.

## Test

From the repository root:

```sh
bash tests/onprem/test_docker.sh
```

The test builds the image, checks its size (less than 500 MB), starts a temporary container and volume, checks health, sends a chat message, verifies loopback-only publishing and checks that restarting preserves the configuration. Temporary resources are removed on exit.
