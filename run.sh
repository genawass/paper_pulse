#!/bin/bash
# Daily cron entry point. Structured after arxiv-sanity-lite's update script:
# the fetch signals "nothing new" through its exit status, and everything
# expensive downstream is skipped on that signal.
#
#   crontab -e
#   17 7 * * *  /home/genadiy/dev/paper_pulse/run.sh >> /home/genadiy/dev/paper_pulse/paperpulse.log 2>&1
#
# Runs at 07:17 daily. Off-the-hour on purpose — every cron on earth fires at
# :00, and arXiv notices.

set -u
cd "$(dirname "$0")" || exit 1

PY=${PYTHON:-python3}
DAYS=${DAYS:-7}
TOP=${TOP:-60}

echo "=== $(date -u '+%Y-%m-%d %H:%M:%SZ') paperpulse ==="

# Exit codes: 0 = new papers, 2 = nothing new, anything else = failure.
# The failure branch matters: an arXiv outage must not read as a quiet day.
$PY -m paperpulse.cli ingest --days "$DAYS"
status=$?

if [ $status -eq 2 ]; then
    echo "no new papers — skipping enrich/pages/stars/report"
    exit 0
elif [ $status -ne 0 ]; then
    echo "ingest FAILED (exit $status) — see above"
    exit "$status"
fi

echo "new papers detected — running the rest of the pipeline"
$PY -m paperpulse.cli enrich --top 400 --include-daily "$DAYS"
$PY -m paperpulse.cli pages  --top "$TOP"
$PY -m paperpulse.cli stars  --top 50
$PY -m paperpulse.cli report --top "$TOP"

echo "done"
