import pandas as pd
import pytest
from carbon_window.analysis import candidate_windows, plan


def frame(values, start="2026-10-05T00:00Z"):
    times=pd.date_range(start,periods=len(values),freq="30min")
    return pd.DataFrame({"start_utc":times,"end_utc":times+pd.Timedelta(minutes=30),"forecast":values})


def test_contiguous_best_window_and_energy_units():
    data=frame([100,100,20,40,90])
    answer=plan(data,data.start_utc.iloc[0],data.end_utc.iloc[-1],60,2)
    assert answer["best"]["intensity"]==30
    assert answer["best_g"]==60 and answer["first_g"]==200 and answer["difference_g"]==140


def test_does_not_bridge_a_missing_half_hour():
    data=frame([10,20,30]).drop(index=1)
    assert candidate_windows(data,data.start_utc.iloc[0],data.end_utc.iloc[-1],60).empty


def test_null_forecast_is_not_zero_carbon():
    data=frame([10,None,40])
    assert plan(data,data.start_utc.iloc[0],data.end_utc.iloc[-1],60,1) is None


def test_already_started_intervals_are_excluded():
    data=frame([1,2,10,20])
    result=plan(data,data.start_utc.iloc[0]+pd.Timedelta(minutes=10),data.end_utc.iloc[-1],60,1)
    assert result["first"]["start"]==data.start_utc.iloc[1]


def test_ties_choose_earliest_and_zero_baseline_does_not_divide():
    data=frame([0,0,0])
    result=plan(data,data.start_utc.iloc[0],data.end_utc.iloc[-1],60,1)
    assert result["best"]["start"]==data.start_utc.iloc[0] and result["difference_pct"]==0


def test_clock_change_still_means_one_real_hour():
    data=frame([30,20,10,40],"2026-10-25T00:30Z")
    windows=candidate_windows(data,data.start_utc.iloc[0],data.end_utc.iloc[-1],60)
    assert len(windows)==3
    assert ((windows.end-windows.start)==pd.Timedelta(hours=1)).all()
    assert data.start_utc.dt.tz_convert("Europe/London").iloc[0].utcoffset()!=data.start_utc.dt.tz_convert("Europe/London").iloc[-1].utcoffset()


@pytest.mark.parametrize("minutes,energy",[(0,1),(45,1),(60,-1),(60,float("nan"))])
def test_reject_invalid_user_inputs(minutes,energy):
    data=frame([30,20,10,40])
    with pytest.raises(ValueError): plan(data,data.start_utc.iloc[0],data.end_utc.iloc[-1],minutes,energy)
