# -*- coding: utf-8 -*-
"""努力值预估:分类、单章点数、缺口反解、日志文案。"""

import welearn_effort as effort
import welearn_score as score


def test_classify_kinds():
    assert score.classify({"location": "Unit 1 Listening practice"}) == score.KIND_LISTEN
    assert score.classify({"location": "Unit 2 Reading & Interacting"}) == score.KIND_READ
    assert score.classify({"location": "Words & Expressions"}) == score.KIND_EXERCISE
    assert score.classify({"location": "听力训练"}) == score.KIND_LISTEN


def test_accuracy_points_full_marks():
    listen = score.accuracy_points({"location": "Listening"})
    reading = score.accuracy_points({"location": "Reading"})
    other = score.accuracy_points({"location": "Words & Expressions"})
    assert listen == 50 + 20 + 20 + 10
    assert reading == 80 + 10 + 10
    assert other == effort.TASK_ON_TIME_POINTS


def test_accuracy_points_with_lower_percent():
    assert score.accuracy_points({"location": "Listening"}, 80) == 40
    assert score.accuracy_points({"location": "Reading"}, 80) == 64
    assert score.accuracy_points({"location": "Words & Expressions"}, 90) == 0


def test_time_minutes_rounding():
    assert score.time_minutes(60) == 1
    assert score.time_minutes(30) == 1
    assert score.time_minutes(600) == 10
    assert score.time_minutes(None) == 1
    assert score.time_minutes("bad") == 1
    assert score.time_minutes(0) == 1


def test_time_points_per_chapter():
    assert score.time_points(60) == effort.study_total(1)
    assert score.time_points(600) == effort.study_total(10)
    assert score.time_points(12000) == effort.study_total(200)


def test_minutes_for_is_monotonic_and_consistent():
    for points in (6, 19, 60, 110, 230, 270):
        minutes = score.minutes_for(points)
        assert effort.study_total(minutes) >= points
        assert minutes == 0 or effort.study_total(minutes - 1) < points
    assert score.minutes_for(0) == 0
    # 超过单章节上限时直接返回上限对应的分钟数
    assert score.minutes_for(10 ** 6) == score.minutes_for(score.MAX_SINGLE_SECTION_POINTS)


def test_sections_for_counts_chapters():
    per = score.time_points(600)
    assert score.sections_for(0, 600) == 0
    assert score.sections_for(per, 600) == 1
    assert score.sections_for(per + 1, 600) == 2
    assert score.sections_for(per * 5, 600) == 5


def test_plan_counts_only_pending_rows():
    rows = [
        {"location": "Unit 1 Listening", "done": True},
        {"location": "Unit 1 Listening", "done": False},
        {"location": "Unit 1 Reading", "done": False},
        {"location": "Unit 1 Words", "done": False},
    ]
    result = score.plan(rows, per_section_seconds=600)
    assert result["pending"] == 3
    assert result["done"] == 1
    assert result["minutes_each"] == 10
    assert result["per_chapter_time_points"] == effort.study_total(10)
    assert result["time_points"] == effort.study_total(10) * 3
    assert result["accuracy_points"] == 100 + 100 + effort.TASK_ON_TIME_POINTS
    assert result["login_points"] == effort.LOGIN_POINTS
    assert result["total"] == (
        result["time_points"] + result["accuracy_points"] + effort.LOGIN_POINTS
    )


def test_plan_with_unreachable_target():
    rows = [{"location": "Unit 1 Words", "done": False}]
    result = score.plan(rows, per_section_seconds=60, target=1000)
    assert result["target"] == 1000
    assert result["reachable"] is False
    assert result["gap"] == 1000 - result["total"]
    assert result["minutes_needed"] == result["sections_needed"] * result["minutes_each"]
    assert result["sections_needed"] >= 1
    assert result["tasks_needed"] >= 1
    assert result["days_needed"] >= 1


def test_plan_with_reachable_target():
    rows = [{"location": "Unit 1 Listening", "done": False}]
    result = score.plan(rows, per_section_seconds=600, target=100)
    assert result["reachable"] is True
    assert result["gap"] == 0
    assert result["minutes_needed"] == 0


def test_plan_ignores_bad_target():
    rows = [{"location": "Unit 1 Words", "done": False}]
    result = score.plan(rows, per_section_seconds=60, target="abc")
    assert "target" not in result


def test_format_plan_mentions_both_paths_and_gap():
    rows = [{"location": "Unit 1 Reading", "done": False}]
    result = score.plan(rows, per_section_seconds=600, target=2000)
    lines = "\n".join(score.format_plan(result))
    assert "刷学习时长" in lines
    assert "刷完成度满分" in lines
    assert "还差" in lines
    assert "目标 2000 点" in lines


def test_days_for_study_uses_daily_cap():
    assert score.days_for_study(0) == 0
    assert score.days_for_study(score.DAILY_STUDY_CAP) == 1
    assert score.days_for_study(score.DAILY_STUDY_CAP + 1) == 2
