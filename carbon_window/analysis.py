"""SQL reporting and gap-aware scheduling; all stored timestamps are UTC."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from carbon_window.pipeline import DB, utc


def query(sql: str, params=(), db: Path = DB) -> pd.DataFrame:
    with closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def latest(db: Path = DB) -> tuple[pd.DataFrame, dict]:
    meta = query("SELECT * FROM runs ORDER BY fetched_at DESC, rowid DESC LIMIT 1", db=db)
    if meta.empty:
        raise ValueError("No successful refresh is stored")
    run = meta.iloc[0].to_dict()
    run["manifest"] = json.loads(run.pop("manifest_json"))
    data = query("SELECT * FROM observations WHERE run_id = ? ORDER BY start_utc", (run["run_id"],), db)
    for col in ("start_utc", "end_utc"):
        data[col] = pd.to_datetime(data[col], utc=True)
    return data, run


def daily(db: Path = DB) -> pd.DataFrame:
    return query("""
       SELECT substr(start_utc,1,10) AS day_utc, COUNT(*) AS intervals,
          COUNT(actual) AS actual_intervals, AVG(actual) AS average_actual,
          MIN(actual) AS minimum_actual, MAX(actual) AS maximum_actual,
          AVG(CASE WHEN actual IS NOT NULL THEN ABS(forecast-actual) END) AS archived_forecast_mae
       FROM latest_observations GROUP BY day_utc ORDER BY day_utc
    """, db=db)


def candidate_windows(data: pd.DataFrame, earliest: datetime, deadline: datetime,
                      minutes: int = 120) -> pd.DataFrame:
    """Enumerate complete contiguous future windows, with no gap interpolation."""
    if minutes <= 0 or minutes % 30:
        raise ValueError("Duration must be a positive multiple of 30 minutes")
    earliest, deadline = pd.Timestamp(utc(earliest)), pd.Timestamp(utc(deadline))
    subset = data[(data.start_utc >= earliest) & (data.end_utc <= deadline)].sort_values("start_utc")
    length = minutes // 30
    windows = []
    for i in range(len(subset) - length + 1):
        chunk = subset.iloc[i:i+length]
        if chunk.forecast.isna().any():
            continue
        if not (chunk.end_utc - chunk.start_utc).eq(pd.Timedelta(minutes=30)).all():
            continue
        if length > 1 and not chunk.start_utc.diff().iloc[1:].eq(pd.Timedelta(minutes=30)).all():
            continue
        windows.append({"start": chunk.start_utc.iloc[0], "end": chunk.end_utc.iloc[-1],
                        "intensity": float(chunk.forecast.mean())})
    return pd.DataFrame(windows, columns=["start", "end", "intensity"])


def plan(data: pd.DataFrame, earliest: datetime, deadline: datetime, minutes: int,
         energy_kwh: float) -> dict | None:
    if not np.isfinite(energy_kwh) or energy_kwh <= 0:
        raise ValueError("Energy must be a positive finite value")
    windows = candidate_windows(data, earliest, deadline, minutes)
    if windows.empty:
        return None
    best = windows.sort_values(["intensity", "start"]).iloc[0]
    first = windows.iloc[0]
    # Uniform power across the chosen duration: average intensity × total kWh.
    best_g, first_g = best.intensity * energy_kwh, first.intensity * energy_kwh
    return {"best": best.to_dict(), "first": first.to_dict(), "best_g": best_g,
            "first_g": first_g, "difference_g": first_g - best_g,
            "difference_pct": (first_g-best_g)/first_g*100 if first_g > 0 else 0.0,
            "candidates": len(windows)}
