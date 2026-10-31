# -*- coding: utf-8 -*-
"""welearn_effort 单测:规则常量、estimate 上限、只读保证(不出现任何 POST)。"""

import welearn_effort as effort
from tests.fakes import FakeResponse, FakeSession

SCO = "https://welearn.sflep.com/ajax/StudyStat.aspx"


def make_session(units):
    """units: {unitidx: [chapter, ...]};未列出的 unitidx 返回"异常"。"""
    session = FakeSession()

    def route(url, data):
        unitidx = int(url.split("unitidx=")[1].split("&")[0])
        if unitidx not in units:
            return FakeResponse(text="服务器异常")
        return FakeResponse(json_data={"info": units[unitidx]})

    session.routes[SCO] = route
    return session


def test_rules_contain_extracted_numbers():
    text = "\n".join(effort.RULES)
    for needle in ("1点/分钟", "每天最多100点", "每天最多60点", "3分钟", "每次获得10点",
                   "同一练习最多50点", "50点*得分百分比", "80点*得分百分比",
                   "复听订正+20点", "看脚本订正+20点", "订正错题+10点", "100点/每公告",
                   "100点/每资料", "一次5点努力值", "每天最多50点"):
        assert needle in text, needle


def test_estimate_zero():
    assert effort.estimate(0) == {"online": 0, "frequency": 0, "slot": 0, "sessions": 0}
    assert effort.study_total(0) == 0


def test_estimate_two_minutes_matches_app_detail():
    # 截图里 2 分钟出现「学习频度/学习时长/学习时段」各 +2 点
    parts = effort.estimate(2)
    assert parts == {"online": 2, "frequency": 2, "slot": 2, "sessions": 0}
    assert effort.study_total(2) == 6


def test_estimate_session_points_step_by_three_minutes():
    assert effort.estimate(3)["sessions"] == 10
    assert effort.estimate(5)["sessions"] == 10
    assert effort.estimate(6)["sessions"] == 20
    assert effort.estimate(20)["sessions"] == 50   # 6 次 = 60 → 封顶 50


def test_estimate_daily_caps():
    parts = effort.estimate(500)
    assert parts["online"] == 100
    assert parts["frequency"] == 60
    assert parts["slot"] == 60
    assert parts["sessions"] == 50


def test_marginal_per_minute():
    assert effort.marginal_per_minute(0) == 3      # 3 分钟前无次数加成
    assert effort.marginal_per_minute(2) == 13     # 第 3 分钟触发一次有效学习
    assert effort.marginal_per_minute(150) == 0    # 三项全封顶后不再涨


def test_exam_points():
    assert effort.exam_points(effort.LISTEN_BASE, 80) == 40
    assert effort.exam_points(effort.READ_BASE, 100) == 80
    assert effort.exam_points(effort.READ_BASE, 0) == 0
    assert effort.exam_points(effort.READ_BASE, 150) == 80   # 百分比钳到 100


def test_learn_seconds_field_fallback():
    assert effort._learn_seconds({"learntime": "125"}) == 125
    assert effort._learn_seconds({"studytime": 90}) == 90
    assert effort._learn_seconds({"time": "60.0"}) == 60
    assert effort._learn_seconds({}) == 0
    assert effort._learn_seconds({"learntime": "abc"}) == 0


def test_probe_reads_everything_and_posts_nothing():
    units = {
        0: [{"id": 11, "location": "U1 Words & Expressions", "iscomplete": "已完成",
             "isvisible": "true", "learntime": 720},
            {"id": 12, "location": "U1 Opener", "iscomplete": "未完成",
             "isvisible": "true", "learntime": 130}],
        1: [{"id": 21, "location": "U2 Reading", "iscomplete": "未完成",
             "isvisible": "false", "learntime": 0}],
    }
    session = make_session(units)
    logs = []
    result = effort.probe(session, "cid1", "uid1", "class1", [1, 2],
                          on_log=logs.append, max_units=5)

    methods = [m for m, _u, _d in session.requests]
    assert set(methods) == {"GET"}, methods        # 只读:一条 POST 都没有
    for _m, url, _d in session.requests:
        assert "scoLeaves" in url
        assert "startsco" not in url and "setscoinfo" not in url

    assert result["units_scanned"] == 2
    assert result["totals"]["chapters"] == 3
    assert result["totals"]["done"] == 1
    assert result["totals"]["pending"] == 1        # 未开放的章节不计入待刷
    assert result["totals"]["minutes"] == 12 + 2 + 0

    first, second, third = result["rows"]
    assert first["minutes"] == 12 and first["done"] is True
    assert first["detail"] == {"online": 12, "frequency": 12, "slot": 12, "sessions": 40}
    assert first["study_points"] == 76
    assert second["done"] is False and second["detail"]["sessions"] == 0
    assert third["visible"] is False


def test_probe_stops_on_missing_unit():
    session = make_session({0: [{"id": 1, "location": "A", "iscomplete": "未完成",
                                 "isvisible": "true", "learntime": 60}]})
    logs = []
    result = effort.probe(session, "c", "u", "cl", [1, 2], on_log=logs.append)
    assert result["units_scanned"] == 1
    assert result["errors"], result["errors"]
    assert any("不可用" in line for line in logs)


def test_probe_advance_until_error():
    session = make_session({0: [{"id": 1, "location": "A", "iscomplete": "未完成",
                                 "isvisible": "true", "learntime": 60}],
                            1: [{"id": 2, "location": "B", "iscomplete": "已完成",
                                 "isvisible": "true", "learntime": 300}]})
    result = effort.probe(session, "c", "u", "cl", None, advance_until_error=True, max_units=10)
    assert result["units_scanned"] == 2
    assert len(result["rows"]) == 2


def test_format_row_is_readable():
    row = {"unit": 1, "no": 2, "location": "U1 Opener", "visible": True, "done": False,
           "minutes": 3, "detail": {"online": 3, "frequency": 3, "slot": 3, "sessions": 10},
           "study_points": 19, "next_minute": 3}
    line = effort.format_row(row)
    assert "待刷" in line and "19 点" in line and "U1 Opener" in line
