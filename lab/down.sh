#!/usr/bin/env bash
# Remove the lab databases and their data. Safe to run twice; always run after a lab job.
cd "$(dirname "$0")"
LAB_PW="${LAB_PW:-unused}" docker compose down -v --remove-orphans
echo "LAB-DOWN"
