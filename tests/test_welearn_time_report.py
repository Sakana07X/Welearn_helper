"""时长上报:session/total 要随真实经过的秒数递增,并且每分钟落一次保存。"""
import json

import pytest

from tests.fakes import FakeResponse, FakeSession
import welearn_time


def ok_ajax_response(session_time="0", total_time="0"):
    cmi = {"score": {"scaled": ""}, "completion_status": "unknown",
           "progress_measure": "0", "total_time": total_time, "session_time": session_time}
    return FakeResponse(json_data={"comment": json.dumps({"cmi": cmi})})


def make_task(fake, chapters):
    task = welearn_time.TimeTask("u", "p", on_log=lambda m: None)
    task.session = fake
    task.cid, task.uid, task.classid = "C1", "U1", "K1"
    task.units = [{"unitname": "U1", "name": "Unit One"}]
    task.request_interval = 0
    fake.routes["https://welearn.sflep.com/ajax/StudyStat.aspx?action=scoLeaves"] = \
        FakeResponse(json_data={"info": chapters})
    return task


class TestDurationFormat:
    def test_plain_seconds(self):
        assert welearn_time.TimeTask._duration_seconds("133") == 133
        assert welearn_time.TimeTask._format_duration(133, "0") == "133"

    def test_hhmmss(self):
        assert welearn_time.TimeTask._duration_seconds("00:02:13") == 133
        assert welearn_time.TimeTask._format_duration(133, "00:00:00") == "00:02:13"

    def test_iso8601(self):
        assert welearn_time.TimeTask._duration_seconds("PT2M13S") == 133
        assert welearn_time.TimeTask._format_duration(133, "PT0S") == "PT2M13S"
        assert welearn_time.TimeTask._duration_seconds("PT1H0M5S") == 3605

    def test_empty_and_garbage(self):
        assert welearn_time.TimeTask._duration_seconds("") == 0
        assert welearn_time.TimeTask._duration_seconds(None) == 0
        assert welearn_time.TimeTask._duration_seconds("abc") == 0

    def test_elapsed_added_on_top_of_base(self):
        task = welearn_time.TimeTask("u", "p", on_log=lambda m: None)
        opened = {"session_time": "00:01:00", "total_time": "00:10:00"}
        session_time, total_time = task._elapsed_durations(opened, 65)
        assert session_time == "00:02:05"
        assert total_time == "00:11:05"


class TestRealReporting:
    def test_keepsco_carries_growing_durations(self, monkeypatch):
        monkeypatch.setattr(welearn_time, "KEEPALIVE_INTERVAL", 0.15)
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response(
            session_time="00:00:10", total_time="00:05:00")
        chapters = [{"id": 1, "location": "A", "learntime": 0}]
        task = make_task(fake, chapters)
        task.run(0, 1)                                  # 目标 1 秒,续期间隔 0.15 秒 → 多次心跳

        keeps = [d for _m, _u, d in [(r[0], r[1], r[2]) for r in fake.requests]
                 if isinstance(d, dict) and d.get("action") == "keepsco_with_getticket_with_updatecmitime"]
        assert len(keeps) >= 2, "续期不止一次"
        sessions = [welearn_time.TimeTask._duration_seconds(d["session_time"]) for d in keeps]
        totals = [welearn_time.TimeTask._duration_seconds(d["total_time"]) for d in keeps]
        assert sessions[0] >= 10 and totals[0] >= 300, "起始值等于服务端给的基准"
        assert sessions == sorted(sessions), "session_time 只增不减"
        assert totals == sorted(totals), "total_time 只增不减"
        assert max(sessions) - 10 <= 2, "上增量不超过真实经过的秒数"

    def test_saves_progress_each_keepalive(self, monkeypatch):
        monkeypatch.setattr(welearn_time, "KEEPALIVE_INTERVAL", 0.5)
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        chapters = [{"id": 1, "location": "A", "learntime": 0}]
        task = make_task(fake, chapters)
        task.run(0, 3)                                  # 3 秒:续期 3 次 + 章末 1 次
        saves = [d for _s, _u, d in fake.requests
                 if isinstance(d, dict) and d.get("action") == "savescoinfo160928"]
        assert len(saves) >= 3, "到期就保存一次 + 章末一次,不能只在结尾保存"
        assert all(s["scoid"] == 1 for s in saves)

    def test_fast_mode_still_saves_once(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        chapters = [{"id": 1, "location": "A", "learntime": 0},
                    {"id": 2, "location": "B", "learntime": 0}]
        task = make_task(fake, chapters)
        task.run(0, 1, realtime=False)
        saves = [d for _s, _u, d in fake.requests
                 if isinstance(d, dict) and d.get("action") == "savescoinfo160928"]
        assert len(saves) == 2


class TestNoCrashOnWeirdServerValues:
    def test_missing_cmi_fields(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = FakeResponse(text="no cmi here")
        chapters = [{"id": 1, "location": "A", "learntime": 0}]
        task = make_task(fake, chapters)
        result = task.run(0, 1)
        assert result["succeed"] == 1
        keeps = [d for _s, _u, d in fake.requests
                 if isinstance(d, dict) and d.get("action") == "keepsco_with_getticket_with_updatecmitime"]
        assert keeps and keeps[0]["session_time"] in ("0", "00:00:00", "PT0S")
