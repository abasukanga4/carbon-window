import numpy as np
import pandas as pd
from carbon_window.evaluate import features, folds, evaluate


def history():
    times=pd.date_range("2026-07-01",periods=70*48,freq="30min",tz="UTC")
    return pd.DataFrame({"start_utc":times,"end_utc":times+pd.Timedelta(minutes=30),
                         "actual":100+20*np.sin(np.arange(len(times))*2*np.pi/48)})


def test_training_never_crosses_test_boundary():
    data=history()
    for train,test,begin,end in folds(data):
        assert train.start_utc.max()<test.start_utc.min()
        assert len(test)==7*48
        assert train.end_utc.max()<=begin and test.end_utc.max()<=end


def test_changing_future_targets_cannot_change_first_fold_predictions():
    data=history(); run={"run_id":"test","fetched_at":"2026-10-01T00:00:00Z"}
    first=evaluate(data,run)
    boundary=list(folds(data))[0][2]
    data.loc[data.start_utc>=boundary,"actual"]+=300
    changed=evaluate(data,run)
    before=[r["prediction"] for r in first["predictions"] if r["fold"]==1]
    after=[r["prediction"] for r in changed["predictions"] if r["fold"]==1]
    np.testing.assert_allclose(before,after)


def test_features_contain_only_information_known_ahead():
    data=history()
    assert set(features(data.start_utc).columns)=={"hour_sin","hour_cos","weekday","month"}
