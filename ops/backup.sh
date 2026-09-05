#!/bin/sh
set -eu

# Run every six hours from the host. SQLite's online backup API writes a
# consistent snapshot to the bind-mounted staging directory. Restic then
# encrypts and uploads it to the configured S3-compatible repository.
BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$BASE_DIR"

if [ ! -r "$BASE_DIR/.env" ]; then
    echo "Missing $BASE_DIR/.env" >&2
    exit 1
fi

# The deployment .env contains only non-secret configuration. Export it for
# restic without printing any values.
set -a
. "$BASE_DIR/.env"
set +a
: "${RESTIC_REPOSITORY:?Set RESTIC_REPOSITORY in .env}"

for required in \
    "$BASE_DIR/secrets/backup_password" \
    "$BASE_DIR/secrets/backup_s3_access_key" \
    "$BASE_DIR/secrets/backup_s3_secret_key"; do
    if [ ! -s "$required" ]; then
        echo "Missing backup secret: $required" >&2
        exit 1
    fi
done

stage="$BASE_DIR/backup-stage"
mkdir -p "$stage"
cleanup() {
    find "$stage" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
}
trap cleanup EXIT INT TERM

docker compose exec -T api python -m juma.backup \
    --online-backup /var/lib/juma/data /var/lib/juma/backup-stage

export RESTIC_PASSWORD_FILE="$BASE_DIR/secrets/backup_password"
AWS_ACCESS_KEY_ID=$(tr -d '\r\n' < "$BASE_DIR/secrets/backup_s3_access_key")
AWS_SECRET_ACCESS_KEY=$(tr -d '\r\n' < "$BASE_DIR/secrets/backup_s3_secret_key")
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

restic backup "$stage" --tag juma
restic forget --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
