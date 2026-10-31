# -*- coding: utf-8 -*-
"""界面/接口层:抓分(只读)与计分规则的端到端断言。"""

import pytest

import web_gui
import welearn_effort
from tests.fakes import FakeResponse, FakeSession

SCO = "https://welearn.sflep.com/ajax/StudyStat.aspx"


class FakeProbeTask:
    """只提供 api_probe 需要的字段(probe 走 welearn_common.fetch_sections)。"""

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


def log_text():
    out = []
    for item in web_gui.state.logs:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, (tuple, list)):
            out.append(" ".join(str(part) for part in item))
        else:
            out.append(str(item))
    return "\n".join(out)


@pytest.fixture
def client():
    web_gui.app.config.update(TESTING=True)
    with web_gui.state.lock:
        web_gui.state.logs.clear()
    yield web_gui.app.test_client()
    with web_gui.state.lock:
        web_gui.state.task = None
        web_gui.state.units = []
        web_gui.state.running = False
        web_gui.state.fetching = False


def reset_state(task=None, units=None):
    with web_gui.state.lock:
        web_gui.state.task = task
        web_gui.state.units = list(units or [])
        web_gui.state.running = False
        web_gui.state.fetching = False


def test_page_has_probe_and_rules_buttons(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="probeBtn"' in html
    assert 'id="rulesBtn"' in html
    assert "抓分(只读)" in html
    assert "计分规则" in html
    assert "/api/probe" in html
    assert "/api/rules" in html


def test_probe_endpoint_is_read_only(client):
    units = {
        0: [{"id": 11, "location": "U1 Words", "iscomplete": "已完成",
             "isvisible": "true", "learntime": 600},
            {"id": 12, "location": "U1 Opener", "iscomplete": "未完成",
             "isvisible": "true", "learntime": 60}],
    }
    session = make_session(units)
    reset_state(FakeProbeTask(session), units=[{"unitname": "U1", "visible": "true"}])

    resp = client.post("/api/probe", json={"units": [1]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["totals"]["chapters"] == 2
    assert data["totals"]["done"] == 1
    assert data["totals"]["pending"] == 1
    assert data["totals"]["minutes"] == 11
    assert data["totals"]["study_points"] == 63
    assert data["rows"][0]["minutes"] == 10
    assert data["rows"][0]["study_points"] == 60

    # 一条 POST 都不许发:只读
    assert set(m for m, _u, _d in session.requests) == {"GET"}
    assert session.requests, "至少读过一次章节清单"
    assert all("scoLeaves" in u for _m, u, _d in session.requests)

    logs = log_text()
    assert "开始抓分(只读" in logs
    assert "抓分完成" in logs


def test_probe_requires_login(client):
    reset_state(None)
    resp = client.post("/api/probe", json={"units": [1]})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_probe_requires_course(client):
    reset_state(FakeProbeTask(FakeSession(), cid=""), units=[{"unitname": "U1"}])
    resp = client.post("/api/probe", json={"units": [1]})
    assert resp.status_code == 400
    assert "课程" in resp.get_json()["error"]


def test_probe_rejects_bad_units(client):
    reset_state(FakeProbeTask(FakeSession()), units=[{"unitname": "U1"}])
    resp = client.post("/api/probe", json={"units": "abc"})
    assert resp.status_code == 400


def test_rules_endpoint_prints_rule_text(client):
    reset_state(None)
    resp = client.post("/api/rules", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["rules"] == welearn_effort.RULES
    logs = log_text()
    assert "努力值计分规则" in logs
    assert "1点/分钟" in logs
    assert "80点*得分百分比" in logs
