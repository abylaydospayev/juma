"""Online SQLite snapshots used by the hosted backup job."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path


def online_backup(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for db in source.glob("*.sqlite"):
            online_backup(db, destination / db.name)
        audit = source / "audit.jsonl"
        if audit.exists():
            shutil.copy2(audit, destination / audit.name)
        return destination
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online-backup", nargs=2, metavar=("SOURCE", "DESTINATION"), required=True)
    args = parser.parse_args()
    print(online_backup(Path(args.online_backup[0]), Path(args.online_backup[1])))
