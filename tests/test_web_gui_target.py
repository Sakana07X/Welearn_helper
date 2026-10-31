# -*- coding: utf-8 -*-
"""目标努力值:输入框存在、probe 回传预估、日志给出两条路径与缺口。"""

import json

import web_gui
import welearn_score
from tests.fakes import FakeResponse, FakeSession

SCO = "https://welearn.sflep.com/ajax/StudyStat.aspx"


class FakeProbeTask:
    def __init__(self, session, cid="42", uid="7", classid="9"):
        self.session = session
        self.cid = cid
        self.uid = uid
        self.classid = classid


def make_session(units):
    session = FakeSession()

    def route(url, data):
        unitidx = int(url.split("unitidx=")[1].split("&")[0])
        if unitidx not in units:
            return FakeResponse(text="服务器异常")
        return FakeResponse(json_data={"info": units[unitidx]})

    session.routes[SCO] = route
    return session


def reset_state(task, units):
    with web_gui.state.lock:
        web_gui.state.logs.clear()
        web_gui.state.task = task
        web_gui.state.units = list(units)
        web_gui.state.running = False
        web_gui.state.fetching = False


def log_text():
    parts = []
    for item in web_gui.state.logs:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, (tuple, list)):
            parts.append(" ".join(str(x) for x in item))
    return "\n".join(parts)


UNITS = {
    0: [{"id": 11, "location": "U1 Listening practice", "iscomplete": "未完成",
         "isvisible": "true", "learntime": 0},
        {"id": 12, "location": "U1 Reading & Interacting", "iscomplete": "已完成",
         "isvisible": "true", "learntime": 600},
        {"id": 13, "location": "U1 Words & Expressions", "iscomplete": "未完成",
         "isvisible": "true", "learntime": 0}],
}


def test_page_has_target_field():
    html = web_gui.app.test_client().get("/").get_data(as_text=True)
    assert 'id="target"' in html
    assert '目标努力值' in html
    assert '按目标刷' in html
    assert 'id="target"' in html
    assert "目标努力值" in html


def test_probe_returns_plan_with_target():
    client = web_gui.app.test_client()
    reset_state(FakeProbeTask(make_session(UNITS)), [{"unitname": "U1", "visible": "true"}])
    resp = client.post("/api/probe", json={"units": [1], "duration": 600, "target": 5000, "mode": "all"})
    assert resp.status_code == 200
    data = resp.get_json()
    plan = data["plan"]
    # 1 章待刷(听力) + 1 章练习待刷
    assert plan["pending"] == 2
    assert plan["minutes_each"] == 10
    assert plan["accuracy_points"] == 100 + 10
    assert plan["time_points"] == welearn_score.time_points(600) * 2
    assert plan["target"] == 5000
    assert plan["reachable"] is False
    assert plan["gap"] == 5000 - plan["total"]
    assert plan["sections_needed"] == welearn_score.sections_for(plan["gap"], 600)

    logs = log_text()
    assert "预计可得" in logs
    assert "刷学习时长" in logs
    assert "刷完成度满分" in logs
    assert "还差" in logs


def test_probe_without_target_has_no_gap():
    client = web_gui.app.test_client()
    reset_state(FakeProbeTask(make_session(UNITS)), [{"unitname": "U1", "visible": "true"}])
    data = client.post("/api/probe", json={"units": [1], "duration": 60, "mode": "all"}).get_json()
    assert "target" not in data["plan"]
    assert data["plan"]["total"] == (
        data["plan"]["time_points"] + data["plan"]["accuracy_points"] + data["plan"]["login_points"]
    )


def test_probe_plan_is_json_safe():
    client = web_gui.app.test_client()
    reset_state(FakeProbeTask(make_session(UNITS)), [{"unitname": "U1", "visible": "true"}])
    data = client.post("/api/probe", json={"units": [1], "target": "900", "mode": "all"}).get_json()
    json.dumps(data)  # 不抛异常即可
    assert data["plan"]["target"] == 900
