"""乱序刷的界面钩子:勾选框、提交字段、后端解析、传给任务、日志。"""

import inspect

import web_gui


def _page() -> str:
    app, _state = web_gui.create_app()
    return app.test_client().get("/").get_data(as_text=True)


def _module_source() -> str:
    """后端解析不在页面里,而在 create_app 内的 api_start 闭包里。"""
    return inspect.getsource(web_gui)


def test_shuffle_checkbox_exists():
    body = _page()
    assert '<input type="checkbox" id="shuffle">' in body
    assert '乱序刷(打乱章节执行顺序)' in body
    assert '<div class="p-sub-title">节奏</div>' in body


def test_shuffle_participates_in_visibility():
    """重排后段落元素可能不存在,所以这里断言的是「守卫式赋值」而不是裸 style 赋值。"""
    body = _page()
    assert "var setDisabled = function (el, on) { if (el) { el.disabled = !on; } };" in body
    assert "setDisabled($('shuffle'), isTime);" in body
    assert "var pace = $('paceField');" in body
    assert "if (pace) { pace.style.display = isTime ? 'none' : ''; }" in body


def test_shuffle_submitted_and_shown():
    body = _page()
    assert "shuffle: $('shuffle').checked" in body
    assert "if ($('shuffle').checked) suffix += ' 乱序';" in body
    assert "$('shuffle').addEventListener('change', updateSummary);" in body


def test_backend_parses_and_forwards_shuffle():
    src = _module_source()
    assert 'raw_shuffle = payload.get("shuffle", False)' in src
    assert 'else str(raw_shuffle).strip().lower() in ("1", "true", "yes", "on"))' in src
    assert "shuffle=shuffle)" in src
    assert "乱序={'开' if shuffle else '关'}" in src


def test_task_receives_shuffle_and_logs():
    """真的走一遍 /api/start:后端解析出的 shuffle 必须落到 TimeTask 上。"""
    import json
    import time as _t
    from tests.fakes import FakeResponse, FakeSession
    import welearn_time

    cmi = {"score": {"scaled": ""}, "completion_status": "unknown",
           "progress_measure": "0", "total_time": "0", "session_time": "0"}
    fake = FakeSession()
    fake.routes["https://welearn.sflep.com/ajax/StudyStat.aspx?action=scoLeaves"] = FakeResponse(
        json_data={"info": [{"id": 1, "location": "A", "learntime": 0},
                            {"id": 2, "location": "B", "learntime": 0}]})
    fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = FakeResponse(
        json_data={"comment": json.dumps({"cmi": cmi})})

    app, state = web_gui.create_app()
    task = welearn_time.TimeTask("u", "p", on_log=state.log, stop_event=state.stop_event)
    task.session = fake
    task.cid, task.uid, task.classid = "C1", "U1", "K1"
    task.units = [{"unitname": "U1", "name": "Unit One"}]
    task.request_interval = 0
    state.task = task
    state.units = [{"unitname": "U1", "name": "Unit One"}]
    state.mode = "time"

    client = app.test_client()
    assert client.post("/api/start", json={"units": 0, "param": "1",
                                           "realtime": True, "shuffle": True}).status_code == 200
    for _ in range(1000):
        if not state.running:
            break
        _t.sleep(0.02)
    logs = "\n".join(m for _, _, m in list(state.logs))
    assert state.last_result["shuffle"] is True
    assert "乱序=开" in logs
    assert "已乱序:" in logs
