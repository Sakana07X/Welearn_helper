"""按目标努力值刷:每刷一章就重新抓一次分,累计到目标就停。

只做编排,不直接拼请求:抓分走 welearn_effort.probe(纯 GET),
刷分走 TimeTask / AccuracyTask 的 run(单章范围),两者都用任务对象上已有的登录会话。
"""
import time
from typing import Any, Callable, Dict, List, Optional

import welearn_effort
import welearn_score

MAX_CHAPTERS_DEFAULT = 400


def _noop(msg: str) -> None:
    pass


def snapshot(task: Any, on_log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """只读抓一次当前进度(所有单元),返回 probe 的原始结果。"""
    return welearn_effort.probe(
        task.session, task.cid, task.uid, task.classid,
        unit_indices=None, on_log=on_log or _noop,
        advance_until_error=True)


def pick_chapter(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """挑第一个还没刷完、且对当前模式有用的章节。"""
    for row in rows:
        if row.get("done"):
            continue
        if str(row.get("visible", "true")).lower() == "false":
            continue
        return row
    return None


def percent_of(param: Any, mode: str) -> float:
    """正确率模式下把 param 折成百分比;时长模式无关,给 100。"""
    if mode != "accuracy":
        return 100.0
    return welearn_score.normalize_percent(param)


def scored(rows: List[Dict[str, Any]], totals: Optional[Dict[str, Any]],
           mode: str, param: Any) -> int:
    """当前已经拿到的分(只算这一路)。"""
    if mode == "time":
        return int((totals or {}).get("study_points", 0))
    percent = percent_of(param, mode)
    return int(sum(welearn_score.accuracy_points(row, percent)
                   for row in rows if row.get("done")))


def _brush(task: Any, mode: str, row: Dict[str, Any], param: Any, jitter: float,
           realtime: bool, qsec: float, qcount: int, shuffle: bool) -> None:
    """只刷一章:sections 精确到小节,避免顺手把整单元刷了。"""
    unit, no = int(row["unit"]), int(row["no"])
    if mode == "time":
        task.run([unit], param, {unit: [no]}, jitter=jitter,
                 realtime=realtime, shuffle=shuffle)
    else:
        task.run([unit], param, {unit: [no]}, jitter=jitter,
                 realtime=realtime, qsec=qsec, qcount=qcount)


def run(task: Any, mode: str, target: Any, param: Any = 60,
        jitter: float = 20.0, realtime: bool = True, qsec: float = 25.0,
        qcount: int = 10, shuffle: bool = False,
        max_chapters: int = MAX_CHAPTERS_DEFAULT,
        on_log: Optional[Callable[[str], None]] = None,
        stop_event: Any = None) -> Dict[str, Any]:
    """刷到「已得分 >= target」为止。

    返回 {target, mode, score, brushed, stopped, reached, reason}。
    """
    log = on_log or _noop
    mode = (mode or "time").strip().lower()
    if mode not in ("time", "accuracy"):
        mode = "time"
    try:
        goal = max(1, int(float(target)))
    except (TypeError, ValueError):
        goal = 1

    brushed: List[Dict[str, Any]] = []
    score = 0
    reached = False
    stopped = False
    reason = ""
    last_pair: Optional[Any] = None

    for _ in range(max(1, int(max_chapters))):
        if stop_event is not None and stop_event.is_set():
            stopped = True
            reason = "已停止"
            break

        result = snapshot(task)
        rows = result.get("rows", [])
        score = scored(rows, result.get("totals"), mode, param)
        log(f"目标进度:{score} / {goal} 点(差 {max(0, goal - score)} 点)")
        if score >= goal:
            reached = True
            reason = "已达标"
            break

        row = pick_chapter(rows)
        if row is None:
            reason = "没有可刷的章节了"
            break

        pair = (score, len(rows))
        if last_pair is not None and pair == last_pair:
            reason = "这一轮没有新增分数(多半是当天上限已满,或章节已被服务器判定完成)"
            break
        last_pair = pair

        log(f"刷第 {len(brushed) + 1} 章:{row.get('location', '?')}")
        _brush(task, mode, row, param, jitter, realtime, qsec, qcount, shuffle)
        brushed.append(row)

    return {
        "target": goal,
        "mode": mode,
        "score": score,
        "brushed": len(brushed),
        "stopped": stopped,
        "reached": reached,
        "reason": reason,
    }


def format_result(result: Dict[str, Any]) -> List[str]:
    """把 run() 的结果整理成日志行。"""
    mode = "学习时长" if result.get("mode") == "time" else "完成度"
    head = (f"按目标刷完成:当前 {result.get('score', 0)} / {result.get('target', 0)} 点"
            f"(只算{mode}),共刷 {result.get('brushed', 0)} 章")
    if result.get("reached"):
        return [head + ",已达标"]
    tail = result.get("reason") or "未达标"
    return [head, f"  未达标:{tail}"]
