#!/usr/bin/env sh
# Recorded / simulated demo for macOS and Linux. No cluster needed; nothing is ever applied.
cd "$(dirname "$0")" && python3 triage.py --scenario all-four --set env.APP_MODE=staging
