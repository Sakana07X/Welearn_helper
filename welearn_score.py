# -*- coding: utf-8 -*-
"""努力值预估:分别算「刷完成度正确率」与「刷学习时长」两条路径能拿多少点,并按目标分反解缺口。

规则依据见 welearn_effort.RULES(来自 App 计分规则页原文):
- 学习类(在线时长 + 学习频度 + 学习时段 + 学习次数)只跟学习时长有关,每题/每章节独立计,
  每分钟固定 3 点,每满 3 分钟再加 10 点(同一练习封顶 50 点);三项还有每日上限(100/60/60)。
- 得分类(测试努力值 / 作业努力值)与时长无关,只看百分制得分:
  听力训练 50×得分% + 复听订正 20 + 看脚本订正 20 + 完成训练 10;
  阅读理解 80×得分% + 订正错题 10 + 完成训练 10;
  其它练习做完满分只算「任务按时完成」10 点。
"""

from typing import Any, Dict, List, Optional

import welearn_effort as effort

# 每日学习类上限(在线 100 + 频度 60 + 时段 60),跨天才有更多空间
DAILY_STUDY_CAP = (
    effort.ONLINE_DAILY_CAP + effort.FREQUENCY_DAILY_CAP + effort.SLOT_DAILY_CAP
)

# 单个练习(章节)能拿到的学习类点数上限:三项日上限 + 学习次数单练习封顶 50
MAX_SINGLE_SECTION_POINTS = effort.study_total(10 ** 6)

KIND_LISTEN = "listen"
KIND_READ = "read"
KIND_EXERCISE = "exercise"


def classify(chapter: Dict[str, Any]) -> str:
    """按章节名判断属于听力训练 / 阅读理解 / 其它练习。"""
    text = " ".join(
        str(chapter.get(key) or "") for key in ("location", "title", "name", "type")
    ).lower()
    if "listen" in text or "听力" in text:
        return KIND_LISTEN
    if "read" in text or "阅读" in text:
        return KIND_READ
    return KIND_EXERCISE


def accuracy_points(chapter: Dict[str, Any], percent: float = 100.0) -> int:
    """刷完成度到给定得分率时,这一章能拿到的得分类努力值(满分口径)。"""
    kind = classify(chapter)
    if kind == KIND_LISTEN:
        base = effort.exam_points(effort.LISTEN_BASE, percent)
        extra = 0
        if percent >= 100:
            extra = (
                effort.LISTEN_FIX_REPLAY + effort.LISTEN_FIX_SCRIPT + effort.LISTEN_FINISH
            )
        return base + extra
    if kind == KIND_READ:
        base = effort.exam_points(effort.READ_BASE, percent)
        extra = 0
        if percent >= 100:
            extra = effort.READ_FIX_WRONG + effort.READ_FINISH
        return base + extra
    return effort.TASK_ON_TIME_POINTS if percent >= 100 else 0


def time_minutes(per_section_seconds: Any) -> int:
    """把界面上「每练习时长(秒)」换成章节分钟数(至少 1 分钟)。"""
    try:
        sec = float(per_section_seconds)
    except (TypeError, ValueError):
        sec = 60.0
    if sec <= 0:
        sec = 60.0
    return max(1, int(round(sec / 60.0)))


def time_points(per_section_seconds: Any) -> int:
    """按指定每章时长刷,单章能拿到的学习类点数。"""
    return effort.study_total(time_minutes(per_section_seconds))


def minutes_for(points: int) -> int:
    """单条学习流拿到 points 点需要多少分钟;超过单章节上限时按上限返回。"""
    target = max(0, min(int(points), MAX_SINGLE_SECTION_POINTS))
    if target <= 0:
        return 0
    lo, hi = 0, 1
    while effort.study_total(hi) < target and hi < 1000000:
        hi *= 2
    while lo < hi:
        mid = (lo + hi) // 2
        if effort.study_total(mid) >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo


def sections_for(points: int, per_section_seconds: Any = 60) -> int:
    """要拿到 points 点学习类努力值,按当前每章时长需要刷多少章。"""
    target = max(0, int(points))
    if target <= 0:
        return 0
    per = time_points(per_section_seconds)
    return (target + per - 1) // per


def days_for_study(points: int, daily_cap: int = DAILY_STUDY_CAP) -> int:
    """学习类点数按每日上限折算需要几天。"""
    target = max(0, int(points))
    if target <= 0:
        return 0
    cap = max(1, int(daily_cap))
    return (target + cap - 1) // cap


def normalize_percent(percent: Any = 100) -> float:
    """把「正确率」参数(单值或 (下限,上限))折成一个百分比,用于估算完成度努力值。"""
    if isinstance(percent, (tuple, list)):
        values = [float(v) for v in percent if str(v).strip() != ""]
        percent = sum(values) / len(values) if values else 100.0
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return 100.0
    return max(0.0, min(100.0, value))


def plan(
    rows: List[Dict[str, Any]],
    per_section_seconds: Any = 60,
    target: Optional[Any] = None,
    include_login: bool = True,
    mode: str = "all",
    percent: Any = 100,
) -> Dict[str, Any]:
    """给勾选范围内的章节算两条路径的预计值,并按目标分算缺口与补法。

    rows 只需要 probe 产出的字段:done / location / title / minutes 等。
    """
    mode = (mode or "all").strip().lower()
    if mode not in ("time", "accuracy", "all"):
        mode = "all"
    pct = normalize_percent(percent)
    pending = [row for row in rows if not row.get("done")]
    minutes_each = time_minutes(per_section_seconds)
    per_chapter = effort.study_total(minutes_each)
    study = per_chapter * len(pending)
    accuracy = sum(accuracy_points(row, pct) for row in pending)
    login = effort.LOGIN_POINTS if (include_login and mode == "all") else 0
    if mode == "time":          # 只刷学习时长:完成度那部分一分都不算
        total = study
    elif mode == "accuracy":    # 只刷完成度:学习时长那部分一分都不算
        total = accuracy
    else:
        total = study + accuracy + login
    avg_accuracy = round(accuracy / len(pending), 1) if pending else 0.0

    result: Dict[str, Any] = {
        "pending": len(pending),
        "done": len(rows) - len(pending),
        "minutes_each": minutes_each,
        "per_chapter_time_points": per_chapter,
        "time_points": study,
        "accuracy_points": accuracy,
        "login_points": login,
        "total": total,
        "daily_study_cap": DAILY_STUDY_CAP,
        "days_for_study": days_for_study(study),
        "mode": mode,
        "percent": round(pct, 1),
        "avg_accuracy": avg_accuracy,
    }

    if target not in (None, ""):
        try:
            goal = int(float(target))
        except (TypeError, ValueError):
            goal = None
        if goal is not None:
            gap = max(0, goal - total)
            result.update({"target": goal, "gap": gap, "reachable": gap == 0})
            if mode == "time":
                sections = sections_for(gap, per_section_seconds) if gap else 0
                result.update(
                    {
                        "minutes_needed": sections * minutes_each,
                        "sections_needed": sections,
                        "tasks_needed": 0,
                        "days_needed": max(days_for_study(gap), 1 if gap else 0),
                    }
                )
            elif mode == "accuracy":
                per_chapter_accuracy = avg_accuracy or 1.0
                chapters = int(-(-gap // per_chapter_accuracy)) if gap else 0
                result.update(
                    {
                        "minutes_needed": 0,
                        "sections_needed": chapters,
                        "tasks_needed": chapters,
                        "days_needed": 0,
                    }
                )
            else:
                sections = sections_for(gap, per_section_seconds) if gap else 0
                result.update(
                    {
                        "minutes_needed": sections * minutes_each,
                        "sections_needed": sections,
                        "tasks_needed": (gap + 99) // 100 if gap else 0,
                        "days_needed": max(days_for_study(gap), 1 if gap else 0),
                    }
                )

    return result


def format_plan(result: Dict[str, Any]) -> List[str]:
    """把 plan() 的结果整理成可直接打进日志的几行(只写当前要刷的那一路)。"""
    mode = result.get("mode", "all")
    lines: List[str] = []
    if mode == "time":
        lines.append(
            f"预计可得(只刷学习时长,完成度那部分不计):+{result['time_points']} 点"
            f"({result['pending']} 章 × 每章 {result['minutes_each']} 分钟 = "
            f"{result['per_chapter_time_points']} 点/章)"
        )
    elif mode == "accuracy":
        lines.append(
            f"预计可得(只刷完成度,正确率按 {result['percent']}% 计):"
            f"+{result['accuracy_points']} 点({result['pending']} 章,平均 "
            f"{result['avg_accuracy']} 点/章)"
        )
    else:
        lines.append(
            f"预计可得:刷学习时长 +{result['time_points']} 点"
            f"({result['pending']} 章 × 每章 {result['minutes_each']} 分钟 = "
            f"{result['per_chapter_time_points']} 点/章)," 
            f"刷完成度满分 +{result['accuracy_points']} 点,"
            f"登录 +{result['login_points']} 点 → 合计 {result['total']} 点"
        )
    if mode in ("time", "all") and result["time_points"]:
        lines.append(
            f"  学习类受每日上限限制:每天最多 {result['daily_study_cap']} 点,"
            f"这部分需要 {result['days_for_study']} 天(同一天反复刷不再增加)"
        )
    if "target" in result:
        if result["reachable"]:
            lines.append(
                f"  目标 {result['target']} 点:按当前勾选可以覆盖,富余 {result['total'] - result['target']} 点"
            )
        else:
            if mode == "time":
                lines.append(
                    f"  目标 {result['target']} 点:还差 {result['gap']} 点 → 需再刷 "
                    f"{result['sections_needed']} 章(每章 {result['minutes_each']} 分钟,共 "
                    f"{result['minutes_needed']} 分钟,约 {result['days_needed']} 天,受每日上限)"
                )
            elif mode == "accuracy":
                lines.append(
                    f"  目标 {result['target']} 点:还差 {result['gap']} 点 → 需再刷 "
                    f"{result['tasks_needed']} 个章节到 {result['percent']:.0f}% 正确率"
                )
            else:
                lines.append(
                    f"  目标 {result['target']} 点:还差 {result['gap']} 点 → 需再刷 "
                    f"{result['sections_needed']} 章(每章 {result['minutes_each']} 分钟,共 "
                    f"{result['minutes_needed']} 分钟,约 {result['days_needed']} 天,受每日上限),"
                    f"或刷 {result['tasks_needed']} 个测试/作业满分(每个约 100 点)"
                )
    return lines
