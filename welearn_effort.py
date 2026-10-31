# -*- coding: utf-8 -*-
"""努力值(Effort)计分规则 + 只读抓分探针。

规则来源:icourse.apk(package com.sflep.course)的 resources.arsc 字符串,
即 App 内「努力值计算规则」页原文,见 RULES。App 的 dex 被 360 加固
(com.stub.StubApp + assets/libjiagu*.so),Java 层代码取不到,因此计分是
服务端实现;这里把规则条文固化成常量,并提供**只读**抓取。

只读保证:probe() 只发 GET(action=scoLeaves,章节清单与完成状态),
不调用 startsco160928 / setscoinfo / savescoinfo160928,
服务器上的学习数据、努力值都不会因为抓取而改变。
"""

from typing import Any, Callable, Dict, List, Optional

import welearn_common

# ---------------- 规则常量(全部来自 App 规则页原文) ----------------
ONLINE_POINT_PER_MIN = 1      # 在线时长:1 点/分钟
ONLINE_DAILY_CAP = 100        # 每天最多 100 点
FREQUENCY_POINT_PER_MIN = 1   # 学习频度:同一天 1 点/分钟
FREQUENCY_DAILY_CAP = 60      # 每天最多 60 点
SLOT_POINT_PER_MIN = 1        # 学习时段:有效时段内 1 点/分钟
SLOT_DAILY_CAP = 60           # 每天最多 60 点
SLOT_START_HOUR = 6           # 有效时段 6:00
SLOT_END_HOUR = 24            # 有效时段 24:00
SESSION_MIN_SECONDS = 180     # 单次学习满 3 分钟算一次有效学习
SESSION_POINTS = 10           # 每次 10 点
SESSION_SCO_CAP = 50          # 同一练习最多 50 点
QA_POINTS = 5                 # 发布/回复问答 5 点/次
QA_DAILY_CAP = 50             # 每天最多 50 点
TASK_ON_TIME_POINTS = 10      # 测试/作业按时完成 10 点/任务
NOTICE_POINTS = 100           # 公告首次查看 100 点/每公告
MATERIAL_POINTS = 100         # 资料首次查看 100 点/每资料
LISTEN_BASE = 50              # 听力训练:50 点 × 得分百分比
READ_BASE = 80                # 阅读理解:80 点 × 得分百分比
LISTEN_FIX_REPLAY = 20        # 完成复听订正 +20
LISTEN_FIX_SCRIPT = 20        # 完成看脚本订正 +20
LISTEN_FINISH = 10            # 完成训练 +10
READ_FIX_WRONG = 10           # 订正错题 +10
READ_FINISH = 10              # 完成训练 +10
LOGIN_POINTS = 50             # 登录 50 点/次,每天只计一次

RULES: List[str] = [
    "努力值为学生参与该课程学习活动获得点数总和,包括以下几个方面:",
    f"在线时长:{ONLINE_POINT_PER_MIN}点/分钟,每天最多{ONLINE_DAILY_CAP}点;"
    f"登录次数:{LOGIN_POINTS}点/次,每天只计一次;",
    f"学习频度:课程学习时长按天分散情况,每天最多{FREQUENCY_DAILY_CAP}点,同一天{FREQUENCY_POINT_PER_MIN}点/分钟;",
    f"学习时段:有效时段内({SLOT_START_HOUR}:00-{SLOT_END_HOUR}:00),课程学习{SLOT_POINT_PER_MIN}点/分钟,"
    f"每天最多{SLOT_DAILY_CAP}点;",
    f"学习次数:单次学习时长达{SESSION_MIN_SECONDS // 60}分钟计为一次有效学习,每次获得{SESSION_POINTS}点,"
    f"同一练习最多{SESSION_SCO_CAP}点;",
    f"任务按时完成:测试、作业任务按时完成获得{TASK_ON_TIME_POINTS}点/每任务;",
    "教师奖励努力值:根据教师奖励点数计算;",
    f"问答参与情况:发布、回复问答一次{QA_POINTS}点努力值,每天最多{QA_DAILY_CAP}点;",
    "测试努力值:根据百分制得分获得努力值;",
    "作业努力值:根据百分制得分获得努力值;"
    f"(注:1:听力训练按首次答题={LISTEN_BASE}点*得分百分比;完成复听订正+{LISTEN_FIX_REPLAY}点;"
    f"完成看脚本订正+{LISTEN_FIX_SCRIPT}点;完成训练+{LISTEN_FINISH}点。"
    f"2:阅读理解按首次答题={READ_BASE}点*得分百分比;订正错题+{READ_FIX_WRONG}点;"
    f"完成训练+{READ_FINISH}点。)",
    f"公告努力值:{NOTICE_POINTS}点/每公告,同一个公告仅计算首次查看;",
    f"资料努力值:{MATERIAL_POINTS}点/每资料,同一个资料仅计算首次查看;",
]

# 学习类三项(在线/频度/时段)同分钟数叠加,是"学习多久加多少分"的基数
STUDY_POINTS_PER_MIN = (ONLINE_POINT_PER_MIN + FREQUENCY_POINT_PER_MIN + SLOT_POINT_PER_MIN)


def _default_log(msg: str) -> None:
    print(msg)


def estimate(minutes: int) -> Dict[str, int]:
    """给定该章节的累计有效学习分钟数,返回学习类各项可得点数(受每日/每练习上限约束)。

    注意:在线时长/频度/时段的上限是**每天**,不是每章;这里按单章归属计算,
    结果不会超过该章自己的上限,是保守下界。
    """
    minutes = max(0, int(minutes))
    seconds = minutes * 60
    return {
        "online": min(minutes * ONLINE_POINT_PER_MIN, ONLINE_DAILY_CAP),
        "frequency": min(minutes * FREQUENCY_POINT_PER_MIN, FREQUENCY_DAILY_CAP),
        "slot": min(minutes * SLOT_POINT_PER_MIN, SLOT_DAILY_CAP),
        "sessions": min(SESSION_POINTS * (seconds // SESSION_MIN_SECONDS), SESSION_SCO_CAP),
    }


def study_total(minutes: int) -> int:
    """学习类三项+学习次数合计点数。"""
    parts = estimate(minutes)
    return parts["online"] + parts["frequency"] + parts["slot"] + parts["sessions"]


def marginal_per_minute(minutes: int) -> int:
    """再多学 1 分钟大约再加多少点(用于界面提示:按当前状态算边际收益)。"""
    return study_total(minutes + 1) - study_total(minutes)


def exam_points(base: int, percent: float) -> int:
    """测试/作业按百分制得分折算:点数 = 基准 × 得分百分比。"""
    percent = max(0.0, min(100.0, float(percent)))
    return int(round(base * percent / 100.0))


def describe() -> List[str]:
    """规则原文,供界面/日志直接打印。"""
    return list(RULES)


def _learn_seconds(chapter: Dict[str, Any]) -> int:
    """从章节字典里取累计学习秒数(字段名各端不一致,做兼容)。"""
    for key in ("learntime", "learnTime", "studytime", "studyTime", "time", "costtime"):
        if key not in chapter:
            continue
        raw = chapter.get(key)
        try:
            value = int(float(str(raw).strip() or 0))
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        return value
    return 0


def probe(session: Any, cid: str, uid: str, classid: str,
          unit_indices: Optional[List[int]] = None,
          on_log: Optional[Callable[[str], None]] = None,
          stop_event: Any = None,
          advance_until_error: bool = False,
          max_units: int = 40) -> Dict[str, Any]:
    """只读抓分:逐单元 GET 章节清单,汇总每章节的已学分钟与学习类可得点数。

    unit_indices: 0-based 的单元序号列表(与 scoLeaves 的 unitidx 一致);
                  None 表示从头逐个探测,直到服务器报错(advance_until_error)。
    返回 {"rows": [...], "totals": {...}, "units_scanned": n, "stopped": bool}
    """
    log = on_log or _default_log
    rows: List[Dict[str, Any]] = []
    units_scanned = 0
    errors: List[str] = []
    stopped = False

    def handle(unit_idx: int, uno: int) -> bool:
        nonlocal units_scanned
        try:
            chapters = welearn_common.fetch_sections(session, cid, uid, unit_idx, classid)
        except welearn_common.WelearnError as exc:
            errors.append(f"U{uno}: {exc}")
            return False
        units_scanned += 1
        for no, chapter in enumerate(chapters, start=1):
            visible = chapter.get("isvisible", "true") != "false"
            done = "未" not in str(chapter.get("iscomplete", "未完成"))
            seconds = _learn_seconds(chapter)
            minutes = seconds // 60
            parts = estimate(minutes)
            rows.append({
                "unit": uno,
                "no": no,
                "location": str(chapter.get("location", "")),
                "visible": visible,
                "done": done,
                "seconds": seconds,
                "minutes": minutes,
                "detail": parts,
                "study_points": parts["online"] + parts["frequency"] + parts["slot"] + parts["sessions"],
                "next_minute": marginal_per_minute(minutes),
            })
        log(f"[抓分] 单元 {uno}: {len(chapters)} 个章节(只读,未提交任何数据)")
        return True

    if unit_indices is None:
        unit = 0
        while unit < max_units:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                stopped = True
                break
            if not handle(unit, unit + 1):
                break
            unit += 1
    else:
        for uno in unit_indices:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                stopped = True
                break
            if not handle(int(uno) - 1, int(uno)):
                log(f"[抓分] 单元 {uno} 不可用,已跳过")

    totals = {
        "chapters": len(rows),
        "done": sum(1 for r in rows if r["done"]),
        "pending": sum(1 for r in rows if not r["done"] and r["visible"]),
        "minutes": sum(r["minutes"] for r in rows),
        "study_points": sum(r["study_points"] for r in rows),
        "next_minute": sum(r["next_minute"] for r in rows),
    }
    log(f"[抓分] 合计: {totals['chapters']} 章节(已完成 {totals['done']},待刷 {totals['pending']}),"
        f"累计 {totals['minutes']} 分钟,学习类努力值约 {totals['study_points']} 点")
    return {"rows": rows, "totals": totals, "units_scanned": units_scanned,
            "errors": errors, "stopped": stopped}


def format_row(row: Dict[str, Any]) -> str:
    """把一行结果格式成可直接进日志的字符串。"""
    state = "已完成" if row["done"] else ("待刷" if row["visible"] else "未开放")
    d = row["detail"]
    return (f"U{row['unit']}-{row['no']:>2} [{state}] {row['location']}  "
            f"{row['minutes']} 分钟 → 在线{d['online']} 频度{d['frequency']} 时段{d['slot']} "
            f"次数{d['sessions']} = {row['study_points']} 点")
