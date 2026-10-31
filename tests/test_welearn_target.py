"""按目标努力值刷的编排:每章后重新抓分,达标即停,今日上限到顶也会停。"""
import welearn_effort
import welearn_score
import welearn_target


class FakeTask:
    def __init__(self):
        self.session = object()
        self.cid, self.uid, self.classid = "C1", "U1", "K1"
        self.calls = []

    def run(self, units, param, sections=None, **kwargs):
        self.calls.append({"units": units, "param": param, "sections": sections, **kwargs})


def make_snapshot(sequence):
    """把 probe 的返回值按顺序吐出来,模拟『刷一章涨一点分』。"""
    state = {"i": 0}

    def fake_probe(task, on_log=None):
        i = min(state["i"], len(sequence) - 1)
        state["i"] += 1
        return sequence[i]

    return fake_probe


def chapter(unit, no, done=False, location="U1 > L"):
    return {"unit": unit, "no": no, "location": location, "visible": "true",
            "done": done, "seconds": 0, "minutes": 0}


class TestHelpers:
    def test_pick_skips_done_and_invisible(self):
        rows = [chapter(1, 1, done=True), {"unit": 1, "no": 2, "visible": "false"}, chapter(1, 3)]
        assert welearn_target.pick_chapter(rows)["no"] == 3

    def test_pick_returns_none_when_finished(self):
        assert welearn_target.pick_chapter([chapter(1, 1, done=True)]) is None

    def test_scored_time_mode_reads_totals(self):
        rows = [chapter(1, 1, done=True)]
        assert welearn_target.scored(rows, {"study_points": 137}, "time", 60) == 137

    def test_scored_accuracy_mode_sums_done_chapters(self):
        rows = [chapter(1, 1, done=True, location="Listening"),
                chapter(1, 2, done=True, location="Reading"),
                chapter(1, 3, done=False, location="Listening")]
        want = (welearn_score.accuracy_points(rows[0], 100)
                + welearn_score.accuracy_points(rows[1], 100))
        assert welearn_target.scored(rows, {}, "accuracy", "100") == want

    def test_percent_param_used_for_accuracy(self):
        rows = [chapter(1, 1, done=True, location="Listening")]
        full = welearn_target.scored(rows, {}, "accuracy", 100)
        low = welearn_target.scored(rows, {}, "accuracy", 70)
        assert low < full


class TestRun:
    def test_reaches_target_and_stops(self, monkeypatch):
        task = FakeTask()
        # 第一次 100 分(未达标) -> 刷一章 -> 第二次 210 分(达标)
        seq = [
            {"rows": [chapter(1, 1)], "totals": {"study_points": 100}},
            {"rows": [chapter(1, 1, done=True)], "totals": {"study_points": 210}},
        ]
        monkeypatch.setattr(welearn_target, "snapshot",
                            make_snapshot(seq))
        logs = []
        out = welearn_target.run(task, "time", 200, 60, on_log=logs.append)
        assert out["reached"] is True and out["score"] == 210
        assert out["brushed"] == 1
        assert task.calls[0]["units"] == [1] and task.calls[0]["sections"] == {1: [1]}
        assert any("目标进度" in line for line in logs)

    def test_single_chapter_scope_avoids_whole_unit(self, monkeypatch):
        task = FakeTask()
        seq = [
            {"rows": [chapter(2, 5)], "totals": {"study_points": 0}},
            {"rows": [chapter(2, 5, done=True)], "totals": {"study_points": 270}},
        ]
        monkeypatch.setattr(welearn_target, "snapshot", make_snapshot(seq))
        welearn_target.run(task, "time", 100, 60)
        call = task.calls[0]
        assert call["units"] == [2] and call["sections"] == {2: [5]}

    def test_stops_when_no_progress(self, monkeypatch):
        task = FakeTask()
        seq = [{"rows": [chapter(1, 1)], "totals": {"study_points": 50}}]  # 分数一直不涨
        monkeypatch.setattr(welearn_target, "snapshot", make_snapshot(seq))
        out = welearn_target.run(task, "time", 999, 60, max_chapters=10)
        assert out["reached"] is False
        assert out["brushed"] <= 1
        assert "没有新增分数" in out["reason"] or "上限" in out["reason"]

    def test_stops_when_nothing_left(self, monkeypatch):
        task = FakeTask()
        monkeypatch.setattr(welearn_target, "snapshot",
                            make_snapshot([{"rows": [chapter(1, 1, done=True)],
                                            "totals": {"study_points": 10}}]))
        out = welearn_target.run(task, "time", 999, 60)
        assert out["brushed"] == 0 and "没有可刷" in out["reason"]

    def test_stop_event_breaks_immediately(self, monkeypatch):
        import threading
        task = FakeTask()
        stop = threading.Event()
        stop.set()
        monkeypatch.setattr(welearn_target, "snapshot",
                            make_snapshot([{"rows": [chapter(1, 1)],
                                            "totals": {"study_points": 0}}]))
        out = welearn_target.run(task, "time", 100, 60, stop_event=stop)
        assert out["stopped"] is True and not task.calls

    def test_accuracy_mode_passes_pace_kwargs(self, monkeypatch):
        task = FakeTask()
        seq = [
            {"rows": [chapter(1, 1, location="Listening")], "totals": {}},
            {"rows": [chapter(1, 1, done=True, location="Listening")], "totals": {}},
        ]
        monkeypatch.setattr(welearn_target, "snapshot", make_snapshot(seq))
        welearn_target.run(task, "accuracy", 50, "100", qsec=30, qcount=8)
        call = task.calls[0]
        assert call["qsec"] == 30 and call["qcount"] == 8
        assert "shuffle" not in call

    def test_time_mode_passes_shuffle_not_pace(self, monkeypatch):
        task = FakeTask()
        seq = [
            {"rows": [chapter(1, 1)], "totals": {"study_points": 0}},
            {"rows": [chapter(1, 1, done=True)], "totals": {"study_points": 270}},
        ]
        monkeypatch.setattr(welearn_target, "snapshot", make_snapshot(seq))
        welearn_target.run(task, "time", 100, 60, shuffle=True)
        call = task.calls[0]
        assert call["shuffle"] is True
        assert "qsec" not in call

    def test_format_result_lines(self):
        lines = welearn_target.format_result(
            {"mode": "time", "score": 120, "target": 200, "brushed": 2,
             "reached": False, "reason": "今天上限满了"})
        assert "120 / 200" in lines[0]
        assert "只算学习时长" in lines[0]
        assert "今天上限满了" in lines[1]
        done = welearn_target.format_result(
            {"mode": "accuracy", "score": 300, "target": 300, "brushed": 3,
             "reached": True, "reason": ""})
        assert "已达标" in done[0]

    def test_probe_is_read_only(self, monkeypatch):
        """编排层只能通过 probe(GET)读分,不许自己拼写接口。"""
        import inspect
        src = inspect.getsource(welearn_target)
        assert "welearn_effort.probe" in src
        assert "savescoinfo160928" not in src and "setscoinfo" not in src
