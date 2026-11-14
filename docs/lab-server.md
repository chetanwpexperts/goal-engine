# The night lab on our own server

DBaba only turns something into a rule after reproducing it on a real database. MariaDB and PostgreSQL run in the nightly
sandbox, but SQL Server, Oracle and MongoDB images can't be downloaded there (the sandbox's network refuses Docker Hub and
Microsoft's registry; checked 14 Nov 2026). So they run on our server, at night, through a GitHub runner:

1. The nightly run writes rules and tests and pushes its branch.
2. The push (or the 02:00 IST schedule) starts `.github/workflows/lab.yml` on the server's runner.
3. The job starts the three databases (`lab/docker-compose.yml`), runs the lab, reports the result as a GitHub check, and
   removes the databases with their data (`lab/down.sh`, always, even after a failure). A 2-hour limit stops a stuck run.
4. The nightly run reads the check and its logs back from GitHub.

Between runs the lab uses no memory or CPU; only the downloaded images stay on disk (about 6-12 GB).

## Fences (the lab never touches anything else on the server)
The server also runs other people's work. The lab is fenced off from all of it:
- **Its own Docker.** The lab user runs *rootless* Docker: a separate Docker that belongs to that user only. It cannot see,
  stop or change any other container on the machine (the server's main Docker and everything in it stay invisible to it).
  The lab user is **not** in the `docker` group and has no sudo. `lab/guard.sh` refuses to start or remove anything unless
  it is running as that user, on that user's rootless Docker.
- **Only its own containers.** Start and stop use the project name `dbaba-lab`; `down` removes only that project's
  containers, network and volumes.
- **Its own files.** Everything lives in `/home/dbaba-lab` (the runner, the checkout, the images). It never reads or writes
  other directories (`/cdata`, other users' homes, the site).
- **No outside ports.** Lab ports (14330, 15210, 27170) are bound to 127.0.0.1 and are checked to be free before setup.
- **Caps.** SQL Server 2 GB / 1 CPU, Oracle 2 GB / 1 CPU, MongoDB 1 GB / 0.5 CPU: at most 5 GB and 2.5 of the 4 CPUs, only
  during the 02:00 IST run, with a 2-hour limit.
- **Only this repository's job.** The runner is registered to this repository only, and the workflow never runs for pull
  requests, so no one else's code can run on the server.
- Only made-up test data goes in; a new random password is made for every run.

## One-time setup (Ubuntu / Debian)

1. Check the room (done 14 Nov: 15 GB RAM, 4 CPUs, 145 GB free, Docker 29.8) and that the lab ports are free; this must
   print nothing:

       sudo ss -ltnp | grep -E ':(14330|15210|27170)\b'

2. Packages for rootless Docker and the drivers:

       sudo apt-get install -y uidmap dbus-user-session docker-ce-rootless-extras python3-venv openssl

3. The lab user: no sudo, **not** in the docker group. If an earlier version of this guide added it, take it out again:

       sudo useradd -m -s /bin/bash dbaba-lab
       sudo gpasswd -d dbaba-lab docker 2>/dev/null || true

4. Let the lab user's own Docker apply memory and CPU caps, and keep it running without a login:

       sudo mkdir -p /etc/systemd/system/user@.service.d
       printf '[Service]\nDelegate=cpu cpuset io memory pids\n' | sudo tee /etc/systemd/system/user@.service.d/delegate.conf
       sudo systemctl daemon-reload
       sudo loginctl enable-linger dbaba-lab

5. Rootless Docker for the lab user (its own daemon, its own images):

       LAB_UID=$(id -u dbaba-lab)
       sudo -iu dbaba-lab env XDG_RUNTIME_DIR=/run/user/$LAB_UID dockerd-rootless-setuptool.sh install
       sudo -iu dbaba-lab env XDG_RUNTIME_DIR=/run/user/$LAB_UID systemctl --user enable --now docker
       sudo -iu dbaba-lab env DOCKER_HOST=unix:///run/user/$LAB_UID/docker.sock docker info --format '{{json .SecurityOptions}}'

   The last line must contain `rootless`.

6. Download the images once, into the lab user's Docker:

       for img in mcr.microsoft.com/mssql/server:2022-latest gvenzl/oracle-free:23-slim mongo:7.0; do
         sudo -iu dbaba-lab env DOCKER_HOST=unix:///run/user/$LAB_UID/docker.sock docker pull $img
       done

7. The GitHub runner: on GitHub open the repository -> Settings -> Actions -> Runners -> New self-hosted runner -> Linux, x64.
   GitHub shows the download commands and a one-time token. Run them as the lab user:

       sudo -iu dbaba-lab
       mkdir actions-runner && cd actions-runner
       # paste GitHub's "Download" commands here, then:
       ./config.sh --url https://github.com/chetanwpexperts/goal-engine --token <TOKEN FROM GITHUB> --labels dbaba-lab --name lab-server --unattended
       echo "DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock" >> .env
       exit
       cd /home/dbaba-lab/actions-runner && sudo ./svc.sh install dbaba-lab && sudo ./svc.sh start

   Never paste the token anywhere else (not in chat, not in the repo).

8. GitHub safety setting: Settings -> Actions -> General -> "Fork pull request workflows from outside collaborators" ->
   "Require approval for all outside collaborators".

9. First test: GitHub -> Actions -> lab -> Run workflow. A green run prints `sqlserver: ok`, `oracle: ok`, `mongo: ok`, then
   `LAB-DOWN`. The server's main `docker ps` never shows anything from the lab (it lives in the lab user's own Docker).

## Turning it off
Stop it: `cd /home/dbaba-lab/actions-runner && sudo ./svc.sh stop`. Remove it completely: Settings -> Actions -> Runners ->
remove, then `sudo ./svc.sh uninstall`, `sudo loginctl disable-linger dbaba-lab` and `sudo userdel -r dbaba-lab` (its rootless
Docker, images and files go with its home directory; nothing else on the server is touched).
