#!/usr/bin/env bash
# Remove the lab databases and their data. Safe to run twice; always run after a lab job.
cd "$(dirname "$0")"
bash ./guard.sh || exit 1
LAB_PW="${LAB_PW:-unused}" docker compose -p dbaba-lab down -v --remove-orphans     # only this project's containers
echo "LAB-DOWN"
