"""「按目标刷」端点:参数校验、只跑当前模式那一路、结果落到日志。"""
import time

import pytest

import welearn_accuracy
import welearn_time
import welearn_target
import web_gui


class FakeTask:
    instances = []

    def __init__(self, username, password, on_log=None, stop_event=None, session=None):
        self.on_log = on_log
        self.stop_event = stop_event
        self.session = session or object()
        self.cid, self.uid, self.classid = "", "", ""
        self.units = []
        FakeTask.instances.append(self)

    def login(self):
        return None

    def fetch_courses(self):
        return [{"cid": 11, "per": 60, "name": "C"}]

    def fetch_units(self, cid):
        return [{"unitname": "U1", "name": "U", "visible": "true"}]

    def fetch_sections(self, unit_index):
        return [{"location": "L1", "iscomplete": "未完成", "isvisible": "true"}]

    @staticmethod
    def normalize_jitter(jitter):
        try:
            value = float(jitter)
        except (TypeError, ValueError):
            return 20.0
        if value != value:          # NaN
            return 20.0
        return max(0.0, min(100.0, value))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(welearn_accuracy, "AccuracyTask", FakeTask)
    monkeypatch.setattr(welearn_time, "TimeTask", FakeTask)
    FakeTask.instances = []
    app, state = web_gui.create_app()
    c = app.test_client()
    c.post("/api/login", json={"username": "u", "password": "p", "mode": "time"})
    c.post("/api/units", json={"cid": 11})
    return c, state


def test_requires_target(client):
    c, _ = client
    r = c.post("/api/target_start", json={"mode": "time", "param": "60"})
    assert r.status_code == 400
    assert "目标努力值" in r.get_json()["error"]


def test_rejects_non_numeric_target(client):
    c, _ = client
    r = c.post("/api/target_start", json={"target": "abc"})
    assert r.status_code == 400
    assert "必须是数字" in r.get_json()["error"]


def test_runs_target_runner_and_logs(client, monkeypatch):
    c, state = client
    seen = {}

    def fake_run(task, mode, target, param=60, **kwargs):
        seen.update({"mode": mode, "target": target, "param": param, **kwargs})
        kwargs.get("on_log", lambda m: None)("目标进度:0 / 200 点(差 200 点)")
        time.sleep(0.05)
        return {"target": target, "mode": mode, "score": 210, "brushed": 1,
                "stopped": False, "reached": True, "reason": "已达标"}

    monkeypatch.setattr(welearn_target, "run", fake_run)
    r = c.post("/api/target_start", json={"target": 200, "mode": "time",
                                          "param": "60", "jitter": 10,
                                          "realtime": True, "shuffle": False})
    assert r.get_json()["ok"] is True
    state.worker.join(timeout=5)
    assert seen["mode"] == "time" and seen["target"] == 200
    assert seen["param"] == 60 and seen["jitter"] == 10.0
    logs = " ".join(m for _s, _t, m in c.get("/api/status").get_json()["logs"])
    assert "按目标刷开始:目标 200 点,只算学习时长" in logs
    assert "已达标" in logs
    assert state.running is False
    assert state.last_result["reached"] is True


def test_accuracy_mode_forwarded(client, monkeypatch):
    c, state = client
    seen = {}

    def fake_run(task, mode, target, param=60, **kwargs):
        seen.update({"mode": mode, "param": param, "qsec": kwargs.get("qsec"),
                     "qcount": kwargs.get("qcount")})
        return {"target": target, "mode": mode, "score": 0, "brushed": 0,
                "stopped": False, "reached": False, "reason": "没有可刷的章节了"}

    monkeypatch.setattr(welearn_target, "run", fake_run)
    c.post("/api/target_start", json={"target": 900, "mode": "accuracy",
                                      "param": "100", "qsec": 30, "qcount": 12})
    state.worker.join(timeout=5)
    assert seen["mode"] == "accuracy" and seen["param"] == 100
    assert seen["qsec"] == 30.0 and seen["qcount"] == 12


def test_page_has_target_button(client):
    c, _ = client
    html = c.get("/").get_data(as_text=True)
    assert 'id="targetBtn"' in html
    assert "/api/target_start" in html
    assert "按目标刷" in html
