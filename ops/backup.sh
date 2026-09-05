#!/bin/sh
set -eu

# Run every six hours from the host.  SQLite's online backup API is exposed by
# ``juma backup``; restic then encrypts and uploads the staged snapshot.
docker compose exec -T api python -m juma.backup --online-backup /var/lib/juma/data /tmp/juma-backup
restic backup /srv/juma/backup-stage --tag juma
restic forget --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
