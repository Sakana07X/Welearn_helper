"""正确率节奏参数必须从页面一路落到 AccuracyTask(真实走 /api/start)。"""
import time

import pytest

import welearn_accuracy
import welearn_common
import web_gui


class PaceFake:
    """记录 run() 收到的关键字,验证 GUI 是否把 qsec/qcount/realtime 传下去。"""

    instances = []

    def __init__(self, username, password, on_log=None, stop_event=None, session=None):
        self.on_log = on_log
        self.stop_event = stop_event
        self.session = session or object()
        self.cid, self.uid, self.classid = "", "", ""
        self.units = []
        self.kwargs = None
        PaceFake.instances.append(self)

    def login(self):
        return None

    def fetch_courses(self):
        return [{"cid": 11, "per": 60, "name": "C"}]

    def fetch_units(self, cid):
        return [{"unitname": "U1", "name": "U", "visible": "true"}]

    def fetch_sections(self, unit_index):
        return [{"location": "L1", "iscomplete": "未完成", "isvisible": "true"}]

    def run(self, units, param, sections=None, **kwargs):
        self.kwargs = kwargs
        time.sleep(0.05)
        return {"way1_succeed": 1, "way1_failed": 0, "way2_succeed": 1, "way2_failed": 0}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(welearn_accuracy, "AccuracyTask", PaceFake)
    PaceFake.instances = []
    app, state = web_gui.create_app()
    return app.test_client(), state


def _login(c):
    c.post("/api/login", json={"username": "u", "password": "p", "mode": "accuracy"})
    c.post("/api/units", json={"cid": 11})


def test_defaults_forwarded(client):
    c, state = client
    _login(c)
    c.post("/api/start", json={"units": [1], "param": "100"})
    state.worker.join(timeout=5)
    kw = PaceFake.instances[-1].kwargs
    assert kw["realtime"] is True
    assert kw["qsec"] == 25.0 and kw["qcount"] == 10
    assert kw["jitter"] == 20.0


def test_custom_pace_forwarded(client):
    c, state = client
    _login(c)
    c.post("/api/start", json={"units": [1], "param": "100", "qsec": 40,
                               "qcount": 15, "realtime": False, "jitter": 0})
    state.worker.join(timeout=5)
    kw = PaceFake.instances[-1].kwargs
    assert kw["qsec"] == 40.0 and kw["qcount"] == 15
    assert kw["realtime"] is False and kw["jitter"] == 0.0


def test_bad_pace_falls_back_to_defaults(client):
    c, state = client
    _login(c)
    c.post("/api/start", json={"units": [1], "param": "100", "qsec": "abc", "qcount": ""})
    state.worker.join(timeout=5)
    kw = PaceFake.instances[-1].kwargs
    assert kw["qsec"] == 25.0 and kw["qcount"] == 10


def test_start_log_shows_pace(client):
    c, state = client
    _login(c)
    c.post("/api/start", json={"units": [1], "param": "100", "qsec": 30, "qcount": 20})
    state.worker.join(timeout=5)
    logs = " ".join(m for _s, _t, m in c.get("/api/status").get_json()["logs"])
    assert "每题 30 秒 x 20 题" in logs
