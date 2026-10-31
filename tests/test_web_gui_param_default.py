"""参数框只有 placeholder 时前端传空串,不该报 int('') —— 前后端都要兜底。"""

import time

import pytest

import welearn_accuracy
import welearn_target
import welearn_time
import web_gui


class ParamFake:
    """记录 run() 收到的 param,用来验证空参是否被换成模式默认值。"""

    instances = []

    @staticmethod
    def normalize_jitter(value):
        try:
            return min(100.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def __init__(self, username, password, on_log=None, stop_event=None, session=None):
        self.on_log = on_log
        self.stop_event = stop_event
        self.session = session or object()
        self.cid, self.uid, self.classid = "", "", ""
        self.units = []
        self.param = None
        ParamFake.instances.append(self)

    def login(self):
        return None

    def fetch_courses(self):
        return [{"cid": 11, "per": 60, "name": "C"}]

    def fetch_units(self, cid):
        return [{"unitname": "U1", "name": "U", "visible": "true"}]

    def fetch_sections(self, unit_index):
        return [{"location": "L1", "iscomplete": "未完成", "isvisible": "true"}]

    def run(self, units, param, sections=None, **kwargs):
        self.param = param
        time.sleep(0.02)
        return {"errors": [], "failed": 0, "succeed": 1, "seconds": 0, "chapters": 1,
                "way1_succeed": 1, "way1_failed": 0, "way2_succeed": 1, "way2_failed": 0}


@pytest.fixture()
def client(monkeypatch):
    """两个任务类都换掉,否则 /api/login 会真的去连 welearn 站点。"""
    monkeypatch.setattr(welearn_accuracy, "AccuracyTask", ParamFake)
    monkeypatch.setattr(welearn_time, "TimeTask", ParamFake)
    ParamFake.instances = []
    app, state = web_gui.create_app()
    return app.test_client(), state


def _login(c, mode="accuracy"):
    c.post("/api/login", json={"username": "u", "password": "p", "mode": mode})
    c.post("/api/units", json={"cid": 11})


def test_param_input_has_value_and_placeholder_switch():
    app, _state = web_gui.create_app()
    body = app.test_client().get("/").get_data(as_text=True)
    assert '<input type="text" id="param" placeholder="100" value="100">' in body
    assert "paramEl.placeholder = isTime ? '60' : '100';" in body
    assert "if (!paramEl.value) { paramEl.value = paramEl.placeholder; }" in body


def test_blank_param_defaults_to_accuracy_full_marks(client):
    c, state = client
    _login(c, "accuracy")
    r = c.post("/api/start", json={"units": [1], "param": "", "mode": "accuracy"})
    assert r.status_code == 200, r.get_data(as_text=True)
    state.worker.join(timeout=5)
    assert ParamFake.instances[-1].param == 100


def test_blank_param_defaults_to_sixty_seconds_in_time_mode(client):
    c, state = client
    _login(c, "time")
    r = c.post("/api/start", json={"units": [1], "param": "   ", "mode": "time"})
    assert r.status_code == 200, r.get_data(as_text=True)
    state.worker.join(timeout=5)
    assert ParamFake.instances[-1].param == 60


def test_target_start_accepts_blank_param(client, monkeypatch):
    seen = {}

    def fake_run(task, mode, target, param=60, **kwargs):
        seen["param"] = param
        seen["mode"] = mode
        seen["target"] = target
        return {"mode": mode, "target": target, "reached": True, "rounds": 0,
                "earned": 0, "start": 0, "pending": 0, "stopped": False, "attempts": 0}

    monkeypatch.setattr(welearn_target, "run", fake_run)
    c, state = client
    _login(c, "accuracy")
    r = c.post("/api/target_start", json={"mode": "accuracy", "param": "", "target": "4000",
                                         "realtime": True, "jitter": 20})
    assert r.status_code == 200, r.get_data(as_text=True)
    state.worker.join(timeout=5)
    assert seen["param"] == 100
    assert seen["mode"] == "accuracy" and seen["target"] == 4000


def test_target_start_still_rejects_garbage_param(client):
    c, _state = client
    _login(c, "accuracy")
    r = c.post("/api/target_start", json={"mode": "accuracy", "param": "abc", "target": "900"})
    assert r.status_code == 400
    assert "参数格式错误" in r.get_json()["error"]
