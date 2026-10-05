"""Validated, versioned snapshots of NESO's national carbon-intensity API."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/carbon.sqlite"
API = "https://api.carbonintensity.org.uk"
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, requested_start TEXT NOT NULL,
 requested_end TEXT NOT NULL, source TEXT NOT NULL, manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
 run_id TEXT NOT NULL REFERENCES runs(run_id), start_utc TEXT NOT NULL,
 end_utc TEXT NOT NULL, forecast REAL CHECK(forecast >= 0),
 actual REAL CHECK(actual >= 0), intensity_index TEXT,
 PRIMARY KEY (run_id, start_utc)
);
CREATE INDEX IF NOT EXISTS observations_time ON observations(start_utc);
CREATE VIEW IF NOT EXISTS latest_observations AS
 SELECT o.* FROM observations o WHERE run_id =
 (SELECT run_id FROM runs ORDER BY fetched_at DESC, rowid DESC LIMIT 1);
"""


def utc(value: str | datetime) -> datetime:
    result = (
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )
    if result.tzinfo is None:
        raise ValueError("A timezone is required")
    return result.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def floor_slot(value: datetime) -> datetime:
    value = utc(value)
    return value.replace(minute=30 * (value.minute // 30), second=0, microsecond=0)


def validate(records: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Keep a half-open interval; identical API boundary duplicates are harmless."""
    seen = {}
    for record in records:
        begin, finish = utc(record["from"]), utc(record["to"])
        if begin < start or begin >= end:
            continue
        if begin != floor_slot(begin) or finish - begin != timedelta(minutes=30):
            raise ValueError("Unexpected observation interval: expected UTC half-hours")
        values = {}
        for key in ("forecast", "actual"):
            value = record["intensity"].get(key)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"Non-numeric {key}")
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"Invalid {key}: {value}")
            values[key] = value
        row = {
            "from": stamp(begin),
            "to": stamp(finish),
            "intensity": {**values, "index": record["intensity"].get("index")},
        }
        if begin in seen and seen[begin] != row:
            raise ValueError(f"Conflicting duplicate at {begin}")
        seen[begin] = row
    if not seen:
        raise ValueError("The API returned no observations for the requested period")
    return [seen[k] for k in sorted(seen)]


def quality(records: list[dict], start: datetime, end: datetime, fetched: datetime) -> dict:
    expected = int((end - start).total_seconds() / 1800)
    historical = [r for r in records if utc(r["to"]) <= fetched - timedelta(hours=2)]
    return {
        "expected_intervals": expected,
        "observed_intervals": len(records),
        "missing_intervals": expected - len(records),
        "historical_intervals": len(historical),
        "historical_actual_missing": sum(r["intensity"]["actual"] is None for r in historical),
        "forecast_missing": sum(r["intensity"]["forecast"] is None for r in records),
        "historical_grace_hours": 2,
    }


def save_snapshot(snapshot: dict, db: Path = DB) -> str:
    start, end, fetched = (utc(snapshot[k]) for k in ("start", "end", "fetched_at"))
    if start != floor_slot(start) or end != floor_slot(end) or end <= start:
        raise ValueError("Snapshot bounds must be increasing UTC half-hours")
    rows = validate(snapshot["data"], start, end)
    manifest = {
        "quality": quality(rows, start, end, fetched),
        "requests": snapshot.get("requests", []),
        "schema_version": 1,
    }
    run_id = snapshot.get("run_id") or str(uuid.uuid4())
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        # One transaction: failures leave the previous run as the current one.
        with conn:
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    stamp(fetched),
                    stamp(start),
                    stamp(end),
                    snapshot["source"],
                    json.dumps(manifest, sort_keys=True),
                ),
            )
            conn.executemany(
                "INSERT INTO observations VALUES (?,?,?,?,?,?)",
                [
                    (
                        run_id,
                        r["from"],
                        r["to"],
                        r["intensity"]["forecast"],
                        r["intensity"]["actual"],
                        r["intensity"].get("index"),
                    )
                    for r in rows
                ],
            )
    return run_id


def collect(days: int = 90, db: Path = DB) -> dict:
    if not 30 <= days <= 366:
        raise ValueError("History must be between 30 and 366 days")
    fetched = datetime.now(timezone.utc)
    now = floor_slot(fetched)
    start, end = now - timedelta(days=days), now + timedelta(hours=48)
    client = requests.Session()
    client.headers.update({"Accept": "application/json", "User-Agent": "CarbonWindow/1.0"})
    client.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
                respect_retry_after_header=True,
            )
        ),
    )
    records, requests_log, cursor = [], [], start
    raw_dir = ROOT / "data/raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        while cursor < end:
            stop = min(cursor + timedelta(days=14), end)
            url = f"{API}/intensity/{cursor:%Y-%m-%dT%H:%MZ}/{stop:%Y-%m-%dT%H:%MZ}"
            response = client.get(url, timeout=(10, 60))
            response.raise_for_status()
            body = response.json()
            if not isinstance(body.get("data"), list):
                raise ValueError("Unexpected API response; previous data retained")
            digest = hashlib.sha256(response.content).hexdigest()
            (raw_dir / f"{digest}.json").write_bytes(response.content)
            # API ranges may include the preceding interval. Filter each chunk
            # before combining so an overlapping refresh cannot create conflicts.
            records.extend(validate(body["data"], cursor, stop))
            requests_log.append(
                {
                    "url": url,
                    "sha256": digest,
                    "rows": len(body["data"]),
                    "received_at": stamp(datetime.now(timezone.utc)),
                }
            )
            print(f"Fetched {cursor:%d %b} → {stop:%d %b}: {len(body['data'])} rows", flush=True)
            cursor = stop
            time.sleep(0.2)
    finally:
        client.close()
    snapshot = {
        "run_id": str(uuid.uuid4()),
        "fetched_at": stamp(fetched),
        "start": stamp(start),
        "end": stamp(end),
        "source": "NESO Carbon Intensity API",
        "requests": requests_log,
        "data": validate(records, start, end),
    }
    save_snapshot(snapshot, db)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--export-demo", action="store_true")
    args = parser.parse_args()
    snapshot = collect(args.days)
    if args.export_demo:
        (ROOT / "data/demo.json").write_text(json.dumps(snapshot, separators=(",", ":")) + "\n")
    print(f"Saved {len(snapshot['data']):,} observations. Run: {snapshot['run_id']}")


if __name__ == "__main__":
    main()
