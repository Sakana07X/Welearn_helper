"""正确率模式的真实节奏:每章按「每题秒数 x 题量」铺开,可被停止信号立刻打断。"""
import time

import welearn_accuracy
from tests.fakes import FakeResponse, FakeSession


def make_task(chapters, stop_event=None, logs=None):
    task = welearn_accuracy.AccuracyTask(
        "u", "p", on_log=(logs.append if logs is not None else (lambda m: None)),
        stop_event=stop_event)
    fake = FakeSession()
    task.session = fake
    task.cid, task.uid, task.classid = "C1", "U1", "K1"
    task.units = [{"unitname": "U1", "name": "Unit One", "visible": "true"}]
    fake.routes["https://welearn.sflep.com/Ajax/SCO.aspx"] = FakeResponse(text='{"ret":0}')
    fake.routes["https://welearn.sflep.com/ajax/StudyStat.aspx?action=scoLeaves"] = (
        lambda url, data: FakeResponse(json_data={"info": chapters})
        if "unitidx=0" in url else FakeResponse(text="出错了"))
    return task, fake


CH = [{"id": i, "location": "L%d" % i, "isvisible": "true", "iscomplete": "未完成"}
      for i in (1, 2)]


class TestPaceOff:
    def test_default_is_instant(self):
        task, _ = make_task(CH)
        start = time.monotonic()
        result = task.run(1, 100)
        assert time.monotonic() - start < 0.5
        assert result["realtime"] is False
        assert result["way1_succeed"] == 2

    def test_realtime_false_flag_in_result(self):
        task, _ = make_task(CH)
        result = task.run(1, 100, realtime=False)
        assert result["way1_succeed"] == 2 and result["realtime"] is False


class TestPaceOn:
    def test_waits_product_of_qsec_and_qcount(self):
        task, _ = make_task(CH)
        start = time.monotonic()
        result = task.run(1, 100, realtime=True, qsec=0.1, qcount=2)
        elapsed = time.monotonic() - start
        assert result["realtime"] is True
        assert result["seconds_per_chapter"] == 0.2
        assert result["qsec"] == 0.1 and result["qcount"] == 2
        assert elapsed >= 0.3, elapsed          # 两章各自铺开,不再瞬间结束
        assert result["way1_succeed"] == 2

    def test_order_kept_and_posts_still_sequential(self):
        task, fake = make_task(CH)
        task.run(1, 100, realtime=True, qsec=0.05, qcount=1)
        sco = [r for r in fake.requests if r[1].endswith("Ajax/SCO.aspx")]
        assert [r[2]["scoid"] for r in sco] == [1, 1, 1, 2, 2, 2]
        assert [r[2]["action"] for r in sco][::3] == ["startsco160928"] * 2

    def test_logs_pace(self):
        logs = []
        task, _ = make_task(CH, logs=logs)
        task.run(1, 100, realtime=True, qsec=0.05, qcount=4)
        assert any("作答 4 题" in line for line in logs), logs

    def test_stop_interrupts_wait(self):
        import threading
        stop = threading.Event()
        task, _ = make_task(CH, stop_event=stop)

        def killer():
            time.sleep(0.05)
            stop.set()

        threading.Thread(target=killer, daemon=True).start()
        start = time.monotonic()
        task.run(1, 100, realtime=True, qsec=30, qcount=30)   # 15 分钟/章,靠停止打断
        assert time.monotonic() - start < 3.0
