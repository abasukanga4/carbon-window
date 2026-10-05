"""Expanding-window experiment: can calendar features beat simple baselines?

Calendar-only features are known in advance. This is an educational experiment,
not the model supplying the planner: that uses NESO's published forecast.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from carbon_window.analysis import latest
from carbon_window.pipeline import DB, ROOT


def features(times: pd.Series) -> pd.DataFrame:
    local = times.dt.tz_convert("Europe/London")
    hour = local.dt.hour + local.dt.minute / 60
    return pd.DataFrame({"hour_sin": np.sin(2*np.pi*hour/24),
                         "hour_cos": np.cos(2*np.pi*hour/24),
                         "weekday": local.dt.dayofweek, "month": local.dt.month}, index=times.index)


def folds(data: pd.DataFrame, count: int = 3, test_days: int = 7):
    """Fixed date spans; never shuffle future rows into the training period."""
    stop = data.start_utc.max().floor("D")  # use complete days only
    for i in range(count):
        begin = stop - pd.Timedelta(days=(count-i)*test_days)
        end = begin + pd.Timedelta(days=test_days)
        train = data[data.start_utc < begin]
        test = data[(data.start_utc >= begin) & (data.start_utc < end)]
        if train.empty or train.start_utc.max()-train.start_utc.min() < pd.Timedelta(days=28):
            raise ValueError("At least 50 days of completed history are needed for three weekly folds")
        if len(test) < test_days * 48 * 0.9:
            raise ValueError("Test week has less than 90% of its expected observations")
        yield train, test, begin, end


def evaluate(data: pd.DataFrame, run: dict) -> dict:
    # No future estimates, gap filling or provider forecasts enter model features.
    clean = data[(data.actual.notna()) & (data.end_utc <= pd.Timestamp(run["fetched_at"]))].copy()
    results, predictions = [], []
    for number, (train, test, begin, end) in enumerate(folds(clean), 1):
        X_train, X_test = features(train.start_utc), features(test.start_utc)
        local_train = train.start_utc.dt.tz_convert("Europe/London")
        local_test = test.start_utc.dt.tz_convert("Europe/London")
        buckets = train.actual.groupby(local_train.dt.hour*2 + local_train.dt.minute//30).mean()
        period_mean = (local_test.dt.hour*2+local_test.dt.minute//30).map(buckets).fillna(train.actual.mean())
        estimator = HistGradientBoostingRegressor(max_iter=100, max_leaf_nodes=15,
                                                  l2_regularization=10, random_state=42)
        estimator.fit(X_train, train.actual)
        forecasts = {"Training mean": np.repeat(train.actual.mean(), len(test)),
                     "Time-of-day mean": period_mean.to_numpy(),
                     "Calendar gradient boosting": np.maximum(0, estimator.predict(X_test))}
        for name, prediction in forecasts.items():
            results.append({"fold": number, "model": name, "train_rows": len(train),
                            "test_rows": len(test), "test_start": begin.isoformat(),
                            "test_end_exclusive": end.isoformat(),
                            "mae": float(mean_absolute_error(test.actual, prediction)),
                            "rmse": float(np.sqrt(mean_squared_error(test.actual, prediction))),
                            "bias": float(np.mean(prediction-test.actual))})
            predictions.extend({"fold": number, "model": name, "start_utc": t.isoformat(),
                                "actual": float(y), "prediction": float(p)}
                               for t,y,p in zip(test.start_utc, test.actual, prediction))
    table = pd.DataFrame(results)
    summary = table.groupby("model", sort=False)[["mae", "rmse", "bias"]].mean().reset_index()
    return {"run_id": run["run_id"], "data_fetched_at": run["fetched_at"],
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "units": "gCO2/kWh", "protocol": "3 expanding folds, 7 days each; fixed hyperparameters",
            "versions": {"python": platform.python_version(), "sklearn": sklearn.__version__},
            "summary": summary.to_dict("records"), "folds": results,
            "predictions": predictions,
            "limitations": ["One seasonal sample is not year-round validation.",
                            "Historical actuals are estimates fetched retrospectively and may be revised.",
                            "No weather, demand or generation forecasts are model inputs.",
                            "NESO's archived forecast lacks its issue timestamp here; it is not a like-for-like day-ahead benchmark.",
                            "The planner uses NESO forecasts; it does not use this experimental model."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/evaluation.json")
    args = parser.parse_args()
    data, run = latest(DB)
    result = evaluate(data, run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(pd.DataFrame(result["summary"]).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
