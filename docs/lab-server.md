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

## Fences
- A separate user, `dbaba-lab`, runs the runner. It is not root and never gets the site's or the database's passwords.
- Every lab port is bound to 127.0.0.1: nothing is reachable from outside the server.
- Memory caps: SQL Server 2 GB, Oracle 2 GB, MongoDB 1 GB (5 GB at most, only during the run). CPU caps too.
- Only made-up test data goes in. A new random password is made for every run.
- The workflow never runs for pull requests, so a pull request from someone else's fork can't run code on the server.
- Note: being in the `docker` group gives a user power close to root on that machine. That is why the user is separate and
  the runner only ever runs this repository's own workflow. Rootless Docker is the stricter option if you want it later.

## One-time setup (Ubuntu / Debian, about 30-60 minutes)

1. Check the server has room (want: 8 GB+ RAM total, 15 GB+ free disk):

       free -h; nproc; df -h /; cat /etc/os-release | head -2; docker --version

2. Docker, if `docker --version` failed:

       curl -fsSL https://get.docker.com | sudo sh

3. The lab user, and Python for the drivers:

       sudo useradd -m -s /bin/bash dbaba-lab
       sudo usermod -aG docker dbaba-lab
       sudo apt-get install -y python3-venv openssl

4. Download the images once (as the lab user):

       sudo -iu dbaba-lab docker pull mcr.microsoft.com/mssql/server:2022-latest
       sudo -iu dbaba-lab docker pull gvenzl/oracle-free:23-slim
       sudo -iu dbaba-lab docker pull mongo:7.0

5. The GitHub runner: on GitHub open the repository -> Settings -> Actions -> Runners -> New self-hosted runner -> Linux, x64.
   GitHub shows the download commands and a one-time token. Run them as the lab user:

       sudo -iu dbaba-lab
       mkdir actions-runner && cd actions-runner
       # paste GitHub's "Download" commands here, then:
       ./config.sh --url https://github.com/chetanwpexperts/goal-engine --token <TOKEN FROM GITHUB> --labels dbaba-lab --name lab-server --unattended
       exit
       cd /home/dbaba-lab/actions-runner && sudo ./svc.sh install dbaba-lab && sudo ./svc.sh start

   Never paste the token anywhere else (not in chat, not in the repo).

6. GitHub safety setting: Settings -> Actions -> General -> "Fork pull request workflows from outside collaborators" ->
   "Require approval for all outside collaborators".

7. First test: GitHub -> Actions -> lab -> Run workflow. A green run prints `sqlserver: ok`, `oracle: ok`, `mongo: ok`, then
   `LAB-DOWN`. Afterwards `docker ps` on the server shows nothing from the lab.

## Turning it off
Stop it: `cd /home/dbaba-lab/actions-runner && sudo ./svc.sh stop`. Remove it: Settings -> Actions -> Runners -> remove, then
`sudo userdel -r dbaba-lab`. The images: `docker image rm mcr.microsoft.com/mssql/server:2022-latest gvenzl/oracle-free:23-slim mongo:7.0`.
