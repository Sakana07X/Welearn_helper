"""努力值预估必须按当前要刷的那一路算:只刷时长就只算时长分,只刷完成度就只算完成度分。"""
import inspect

import welearn_score


ROWS = [
    {"done": False, "location": "Unit1 > 听力训练", "title": "听力训练"},
    {"done": False, "location": "Unit1 > 阅读理解", "title": "阅读理解"},
    {"done": False, "location": "Unit1 > 词汇练习", "title": "词汇练习"},
    {"done": True, "location": "Unit1 > 已完成章节", "title": "已完成"},
]


def test_time_mode_only_counts_study():
    r = welearn_score.plan(ROWS, per_section_seconds=60, mode="time")
    assert r["mode"] == "time"
    assert r["total"] == r["time_points"]
    assert r["total"] > 0
    assert r["accuracy_points"] > 0          # 仍然算出来,只是不进 total
    assert r["login_points"] == 0
    assert r["pending"] == 3


def test_accuracy_mode_only_counts_accuracy():
    r = welearn_score.plan(ROWS, per_section_seconds=60, mode="accuracy")
    assert r["mode"] == "accuracy"
    assert r["total"] == r["accuracy_points"]
    assert r["login_points"] == 0
    assert r["time_points"] > 0              # 字段保留,但不计入


def test_all_mode_sums_three_sources():
    r = welearn_score.plan(ROWS, per_section_seconds=60, mode="all")
    assert r["total"] == r["time_points"] + r["accuracy_points"] + r["login_points"]
    assert r["login_points"] == 50


def test_unknown_mode_falls_back_to_all():
    assert welearn_score.plan(ROWS, mode="whatever")["mode"] == "all"


def test_accuracy_percent_lowers_points():
    full = welearn_score.plan(ROWS, mode="accuracy", percent=100)["accuracy_points"]
    low = welearn_score.plan(ROWS, mode="accuracy", percent=70)["accuracy_points"]
    assert low < full
    mid = welearn_score.plan(ROWS, mode="accuracy", percent=(70, 100))
    assert mid["percent"] == 85.0
    assert low <= mid["accuracy_points"] <= full


def test_time_target_expressed_in_chapters_only():
    r = welearn_score.plan(ROWS, per_section_seconds=60, mode="time", target=100000)
    assert r["gap"] > 0 and r["reachable"] is False
    assert r["sections_needed"] > 0
    assert r["tasks_needed"] == 0
    assert r["days_needed"] >= 1


def test_accuracy_target_expressed_in_chapters_only():
    r = welearn_score.plan(ROWS, mode="accuracy", percent=100, target=100000)
    assert r["gap"] > 0
    assert r["tasks_needed"] > 0
    assert r["sections_needed"] == r["tasks_needed"]
    assert r["days_needed"] == 0
    assert r["minutes_needed"] == 0


def test_format_plan_speaks_one_path_only():
    time_lines = " ".join(welearn_score.format_plan(
        welearn_score.plan(ROWS, mode="time", target=99999)))
    acc_lines = " ".join(welearn_score.format_plan(
        welearn_score.plan(ROWS, mode="accuracy", target=99999)))
    assert "只刷学习时长" in time_lines
    assert "完成度" not in time_lines.split("(")[0]
    assert "只刷完成度" in acc_lines
    assert "学习时长" not in acc_lines.split("(")[0]
    assert "每日上限" in time_lines


def test_gui_forwards_mode_to_plan():
    import web_gui

    src = inspect.getsource(web_gui)
    assert "mode=raw_mode" in src
    assert 'raw_mode = payload.get("mode") or state.mode' in src
    assert 'percent=payload.get("accuracy") or payload.get("param") or 100' in src
    page = web_gui.create_app()[0].test_client().get("/").get_data(as_text=True)
    assert "mode: getMode()" in page
    assert "预计可得(仅完成度)" in page and "预计可得(仅学习时长)" in page
