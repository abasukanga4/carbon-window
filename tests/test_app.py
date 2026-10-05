from streamlit.testing.v1 import AppTest
from pathlib import Path


def test_saved_demo_renders_and_long_history_is_supported(tmp_path, monkeypatch):
    from carbon_window import pipeline
    from carbon_window import analysis
    database = tmp_path / "demo.sqlite"
    monkeypatch.setattr(pipeline, "DB", database)
    original_latest, original_daily = analysis.latest, analysis.daily
    monkeypatch.setattr(analysis, "latest", lambda: original_latest(database))
    monkeypatch.setattr(analysis, "daily", lambda: original_daily(database))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=30).run()
    assert not app.exception
    assert any(m.label=="Estimated CO₂ · selected window" for m in app.metric)
    app.selectbox[2].set_value(90).run()
    assert not app.exception
    app.selectbox[0].set_value(24).run()
    app.selectbox[1].set_value(6).run()
    assert not app.exception
    assert any("No complete window" in w.value for w in app.warning)
