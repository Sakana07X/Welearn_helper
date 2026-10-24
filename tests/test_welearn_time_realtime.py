"""真实时间开关(realtime)的用例:开=按目标秒数占满真实时间,关=并发立即刷完。"""
import inspect
import json
import time

from tests.fakes import FakeResponse, FakeSession
import welearn_time


def make_task(fake, chapters):
    task = welearn_time.TimeTask("u", "p", on_log=lambda m: None)
    task.session = fake
    task.cid, task.uid, task.classid = "C1", "U1", "K1"
    task.units = [{"unitname": "U1", "name": "Unit One"}]
    task.request_interval = 0
    fake.routes["https://welearn.sflep.com/ajax/StudyStat.aspx?action=scoLeaves"] = \
        FakeResponse(json_data={"info": chapters})
    return task


def ok_ajax_response():
    cmi = {"score": {"scaled": ""}, "completion_status": "unknown",
           "progress_measure": "0", "total_time": "0", "session_time": "0"}
    return FakeResponse(json_data={"comment": json.dumps({"cmi": cmi})})


def chapters(n):
    return [{"id": i, "location": f"C{i}", "learntime": 0} for i in range(1, n + 1)]


def saved_ids(fake):
    return [r[2]["scoid"] for r in fake.requests
            if r[2] and r[2].get("action") == "savescoinfo160928"]


class TestRealtimeSignature:
    def test_default_is_realtime_on(self):
        params = inspect.signature(welearn_time.TimeTask.run).parameters
        assert "realtime" in params
        assert params["realtime"].default is True

    def test_result_reports_realtime_flag(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        task = make_task(fake, chapters(1))
        assert task.run(0, 1, realtime=True)["realtime"] is True
        assert task.run(0, 1, realtime=False)["realtime"] is False


class TestRealtimeOff:
    def test_finishes_immediately(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        task = make_task(fake, chapters(6))
        start = time.monotonic()
        result = task.run(0, 30, realtime=False)
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, f"关掉真实时间应秒完,实际 {elapsed:.2f}s"
        assert result["seconds"] == 0          # 不占用时长
        assert result["total"] == 6 and result["failed"] == 0

    def test_all_chapters_saved(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        task = make_task(fake, chapters(5))
        task.run(0, 10, realtime=False)
        assert sorted(saved_ids(fake)) == [1, 2, 3, 4, 5]

    def test_jitter_does_not_add_waiting(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        task = make_task(fake, chapters(4))
        start = time.monotonic()
        result = task.run(0, 30, jitter=50, realtime=False)
        assert time.monotonic() - start < 3.0
        assert result["seconds"] == 0


class TestRealtimeOn:
    def test_waits_target_seconds(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        task = make_task(fake, chapters(2))
        start = time.monotonic()
        result = task.run(0, 1, realtime=True)
        elapsed = time.monotonic() - start
        assert elapsed >= 2.0, f"两章各 1 秒应至少 2 秒,实际 {elapsed:.2f}s"
        assert result["seconds"] == 2

    def test_chapters_sequential(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax_response()
        task = make_task(fake, chapters(3))
        task.run(0, 1, realtime=True)
        assert saved_ids(fake) == [1, 2, 3]   # 串行,顺序保存
