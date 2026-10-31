"""乱序刷章节:TimeTask._shuffle_order 与 run(shuffle=...) 的用例。"""
import json
import random

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


def ok_ajax():
    cmi = {"score": {"scaled": ""}, "completion_status": "unknown",
           "progress_measure": "0", "total_time": "0", "session_time": "0"}
    return FakeResponse(json_data={"comment": json.dumps({"cmi": cmi})})


def chapters(n):
    return [{"id": i, "location": f"C{i}", "learntime": 0} for i in range(1, n + 1)]


def saved_ids(fake):
    return [r[2]["scoid"] for r in fake.requests
            if r[2] and r[2].get("action") == "savescoinfo160928"]


class TestShuffleOrder:
    def test_off_keeps_order(self):
        pending = [(base, base, {"id": i}) for i, base in enumerate([1, 2, 3, 4, 5], start=1)]
        before = [c["id"] for _, _, c in pending]
        assert welearn_time.TimeTask._shuffle_order(pending, False) is False
        assert [c["id"] for _, _, c in pending] == before

    def test_on_reorders_and_keeps_every_item(self):
        pending = [(base, base, {"id": i}) for i, base in enumerate(range(1, 9), start=1)]
        assert welearn_time.TimeTask._shuffle_order(pending, True) is True
        ids = [c["id"] for _, _, c in pending]
        assert sorted(ids) == list(range(1, 9)), "打乱后每章仍必须恰好出现一次"
        assert ids != list(range(1, 9)) or True   # 单次可能碰巧有序,连续多次见下条

    def test_single_chapter_not_shuffled(self):
        pending = [(3, 3, {"id": 1})]
        assert welearn_time.TimeTask._shuffle_order(pending, True) is False

    def test_order_actually_varies(self):
        seen = set()
        for _ in range(40):
            pending = [(1, 1, {"id": i}) for i in range(1, 7)]
            welearn_time.TimeTask._shuffle_order(pending, True)
            seen.add(tuple(c["id"] for _, _, c in pending))
        assert len(seen) > 1, "40 次里顺序完全没变,说明没打乱"


class TestRunShuffle:
    def test_run_reports_and_permutes(self):
        random.seed(7)
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax()
        task = make_task(fake, chapters(2))
        result = task.run(0, 1, realtime=True, shuffle=True)
        assert result["shuffle"] is True
        assert result["total"] == 2 and result["failed"] == 0
        assert sorted(saved_ids(fake)) == [1, 2], "乱序不能漏章或重复"

    def test_run_default_is_in_order(self):
        fake = FakeSession()
        fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = ok_ajax()
        task = make_task(fake, chapters(3))
        result = task.run(0, 1, realtime=True)
        assert result["shuffle"] is False
        assert saved_ids(fake) == [1, 2, 3], "默认必须保持原有顺序"
