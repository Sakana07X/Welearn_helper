"""Welearn_helper Web 图形界面(Flask)。刷课任务在后台线程运行,可随时停止。

启动: py -u web_gui.py  →  自动打开浏览器 http://127.0.0.1:17777/
环境变量 WELEARN_NO_OPEN=1 跳过自动开浏览器(冒烟/测试用)。
"""
import logging
import os
import threading
import time
import webbrowser
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request

import welearn_accuracy
import welearn_common
import welearn_time
import welearn_effort
import welearn_target
import welearn_score
from welearn_common import LoginError, WelearnError

BANNER = """**********  Created By Avenshy & SSmJaE  **********
                    Web界面Version:1.0
        原项目作者github https://github.com/Avenshy
        更新维护者github https://github.com/lxbsdsg/Welearn_helper/
***************************************************"""


class AppState:
    """单个应用实例的共享状态。所有变更持锁;log() 可从任意线程调用。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.task = None                      # AccuracyTask 或 TimeTask
        self.mode = "accuracy"
        self.courses: List[dict] = []
        self.units: List[dict] = []
        self.sections_map: Dict[int, List[dict]] = {}   # {单元编号: 小节列表},供小节范围校验
        self.running = False
        self.running_what = "待命"
        self.fetching = False       # 课程/单元/小节拉取进行中(与任务运行区分)
        self.last_result: Optional[dict] = None
        self.logs: Deque[Tuple[int, str, str]] = deque(maxlen=2000)
        self._log_seq = 0

    def log(self, msg: str):
        with self.lock:
            self._log_seq += 1
            self.logs.append((self._log_seq, time.strftime("%H:%M:%S"), str(msg)))

    def worker_busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _set(self, running: bool, running_what: str):
        with self.lock:
            self.running = running
            self.running_what = running_what

    def _hint(self, what: str):
        """只更新状态文本,不改变 running(避免拉取瞬态被误判为任务运行)"""
        with self.lock:
            self.running_what = what

    def _fetch_begin(self, hint: str):
        with self.lock:
            self.fetching = True
        self._hint(hint)

    def _fetch_end(self):
        with self.lock:
            self.fetching = False
        self._hint("待命")


def _format_result(mode: str, result: dict) -> List[str]:
    if mode == "time":
        lines = [f"运行结束!!错误: {result['failed']} 个"]
        for i, err in enumerate(result["errors"], start=1):
            lines.append(f"第{i}个错误:{err}")
        return lines
    return ["方式1: {} 成功, {} 失败".format(result["way1_succeed"], result["way1_failed"]),
            "方式2: {} 成功, {} 失败".format(result["way2_succeed"], result["way2_failed"])]


def create_app():
    """应用工厂,便于测试隔离。返回 (app, state)。"""
    app = Flask(__name__)
    state = AppState()

    @app.route("/")
    def index():
        response = app.response_class(INDEX_HTML, mimetype="text/html")
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.route("/api/login", methods=["POST"])
    def api_login():
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        mode = payload.get("mode", "accuracy")
        if mode not in ("accuracy", "time"):
            return jsonify(ok=False, error="未知模式"), 400
        if not username or not password:
            return jsonify(ok=False, error="请输入账号和密码"), 400
        if state.worker_busy():
            return jsonify(ok=False, error="任务正在进行,请先停止"), 409
        state.stop_event.clear()   # 此前点过"停止"不得阻塞新的登录
        state._hint("登录中...")
        cls = welearn_accuracy.AccuracyTask if mode == "accuracy" else welearn_time.TimeTask
        task = cls(username, password, on_log=state.log, stop_event=state.stop_event)
        try:
            task.login()
            courses = task.fetch_courses()
        except (LoginError, WelearnError) as e:
            state._hint("待命")
            state.log(f"登录失败:{e}")
            return jsonify(ok=False, error=str(e))
        except Exception as e:
            state._hint("待命")
            state.log(f"登录失败:{e!r}")
            return jsonify(ok=False, error=repr(e))
        with state.lock:
            state.task = task
            state.mode = mode
            state.courses = courses
            state.units = []
            state.sections_map = {}
            state.last_result = None
        state._hint("待命")
        state.log(f"共 {len(courses)} 门课程,请在课程列表中选择")
        return jsonify(ok=True, courses=courses)

    @app.route("/api/units", methods=["POST"])
    def api_units():
        payload = request.get_json(silent=True) or {}
        cid = payload.get("cid")
        if state.task is None:
            return jsonify(ok=False, error="请先登录"), 400
        if state.running:
            return jsonify(ok=False, error="任务正在进行,请先停止"), 409
        state.stop_event.clear()
        state._fetch_begin("加载单元中...")
        try:
            units = state.task.fetch_units(cid)
        except (LoginError, WelearnError) as e:
            state._fetch_end()
            state.log(f"加载单元失败:{e}")
            return jsonify(ok=False, error=str(e))
        except Exception as e:
            state._fetch_end()
            state.log(f"加载单元失败:{e!r}")
            return jsonify(ok=False, error=repr(e))
        finally:
            state._fetch_end()   # 幂等:正常/异常路径都确保清除拉取标记
        with state.lock:
            state.units = units
        state.log(f"已加载 {len(units)} 个单元")
        return jsonify(ok=True, units=units)

    @app.route("/api/refresh", methods=["POST"])
    def api_refresh():
        if state.task is None:
            return jsonify(ok=False, error="请先登录"), 400
        if state.running:
            return jsonify(ok=False, error="任务正在进行,请先停止"), 409
        state.stop_event.clear()
        state._fetch_begin("刷新课程中...")
        try:
            courses = state.task.fetch_courses()
        except (LoginError, WelearnError) as e:
            state._fetch_end()
            state.log(f"刷新失败:{e}")
            return jsonify(ok=False, error=str(e))
        except Exception as e:
            state._fetch_end()
            state.log(f"刷新失败:{e!r}")
            return jsonify(ok=False, error=repr(e))
        finally:
            state._fetch_end()
        with state.lock:
            state.courses = courses
        state.log(f"已刷新,共 {len(courses)} 门课程")
        return jsonify(ok=True, courses=courses)

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        """作废当前登录:旧 task 不得再用新参数运行"""
        if state.worker_busy() or state.fetching:
            return jsonify(ok=False, error="任务正在进行,请先停止"), 409
        with state.lock:
            state.task = None
            state.courses = []
            state.units = []
            state.sections_map = {}
            state.last_result = None
        state.log("已退出登录,请重新登录")
        return jsonify(ok=True)

    @app.route("/api/mode", methods=["POST"])
    def api_mode():
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode")
        if mode not in ("accuracy", "time"):
            return jsonify(ok=False, error="未知模式"), 400

        with state.lock:
            if state.mode == mode:
                return jsonify(ok=True, mode=mode)

            if state.task is None:
                state.mode = mode
            else:
                if state.running or state.worker_busy() or state.fetching:
                    return jsonify(ok=False, error="任务正在进行,请先停止"), 409

                old_task = state.task
                cls = (welearn_accuracy.AccuracyTask if mode == "accuracy"
                       else welearn_time.TimeTask)
                task = cls(old_task.username, old_task.password,
                           on_log=state.log, stop_event=state.stop_event,
                           session=old_task.session)
                for attribute in ("cid", "uid", "classid"):
                    setattr(task, attribute, getattr(old_task, attribute, ""))
                task.units = list(getattr(old_task, "units", []) or [])

                state.task = task
                state.mode = mode
                state.last_result = None

        mode_name = "刷学习时长" if mode == "time" else "刷完成度正确率"
        state.log(f"已切换到{mode_name}模式,无需重新登录")
        return jsonify(ok=True, mode=mode)

    @app.route("/api/sections", methods=["POST"])
    def api_sections():
        payload = request.get_json(silent=True) or {}
        if state.task is None:
            return jsonify(ok=False, error="请先登录"), 400
        if not state.units:
            return jsonify(ok=False, error="请先选择课程加载单元"), 400
        try:
            unit_index = int(payload.get("unit_index", 0))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="单元序号必须是数字"), 400
        if not 1 <= unit_index <= len(state.units):
            return jsonify(ok=False,
                           error=f"请选择具体单元(1~{len(state.units)});0=全部,无单独小节可显示"), 400
        if state.running:
            return jsonify(ok=False, error="任务正在进行,请先停止"), 409
        state._fetch_begin("加载小节中...")
        try:
            raw = state.task.fetch_sections(unit_index)
        except (LoginError, WelearnError) as e:
            state._fetch_end()
            state.log(f"加载小节失败:{e}")
            return jsonify(ok=False, error=str(e))
        except Exception as e:
            state._fetch_end()
            state.log(f"加载小节失败:{e!r}")
            return jsonify(ok=False, error=repr(e))
        finally:
            state._fetch_end()
        sections = [{"location": x.get("location", ""),
                     "complete": "未" not in x.get("iscomplete", "未"),
                     "visible": x.get("isvisible", "true") != "false"} for x in raw]
        with state.lock:
            state.sections_map[unit_index] = sections   # 记住编号依据,供 /api/start 校验小节范围
        state.log(f"单元 {unit_index} 共 {len(sections)} 个小节")
        return jsonify(ok=True, sections=sections)

    @app.route("/api/start", methods=["POST"])
    def api_start():
        payload = request.get_json(silent=True) or {}
        if state.task is None:
            return jsonify(ok=False, error="请先登录"), 400
        if not state.units:
            return jsonify(ok=False, error="请先选择课程加载单元"), 400
        # 兼容旧字段 unit_index/section_index 与新字段 units/sections
        raw_units = payload.get("units", payload.get("unit_index", 0))
        raw_secs = payload.get("sections", payload.get("section_index") or None)
        try:
            all_units, unit_list, section_map = welearn_common.normalize_targets(raw_units, raw_secs)
        except (TypeError, ValueError) as e:
            return jsonify(ok=False, error=str(e)), 400
        if section_map:
            with state.lock:
                known_map = dict(state.sections_map)
            for uno, secs in section_map.items():
                known = known_map.get(uno)
                if known and max(secs) > len(known):
                    return jsonify(ok=False,
                                   error=f"单元{uno}小节id超出范围(1~{len(known)})"), 400
        try:
            param = welearn_common.parse_numeric_param(str(payload.get("param", "")))
        except ValueError as e:
            return jsonify(ok=False, error=f"参数格式错误:{e}\n模式1: 100   模式2: 70,100"), 400
        jitter = welearn_time.TimeTask.normalize_jitter(
            payload.get("jitter", welearn_time.JITTER_DEFAULT))
        raw_shuffle = payload.get("shuffle", False)
        shuffle = (raw_shuffle if isinstance(raw_shuffle, bool)
                   else str(raw_shuffle).strip().lower() in ("1", "true", "yes", "on"))
        raw_realtime = payload.get("realtime", True)
        realtime = (raw_realtime if isinstance(raw_realtime, bool)
                    else str(raw_realtime).strip().lower() not in ("0", "false", "no", "off", ""))
        raw_qsec = payload.get("qsec", welearn_accuracy.ANSWER_SECONDS_DEFAULT)
        try:
            qsec = float(raw_qsec)
        except (TypeError, ValueError):
            qsec = welearn_accuracy.ANSWER_SECONDS_DEFAULT
        raw_qcount = payload.get("qcount", welearn_accuracy.QUESTION_COUNT_DEFAULT)
        try:
            qcount = int(float(raw_qcount))
        except (TypeError, ValueError):
            qcount = welearn_accuracy.QUESTION_COUNT_DEFAULT

        desc = "全部单元" if all_units else "单元" + ",".join(str(u) for u in unit_list)
        if section_map:
            desc += " 小节" + ",".join(
                f"{u}:[{'/'.join(str(s) for s in secs)}]" for u, secs in section_map.items())

        def work():
            try:
                if state.mode == "time":
                    result = state.task.run(0 if all_units else unit_list, param,
                                            section_map, jitter=jitter, realtime=realtime,
                                            shuffle=shuffle)
                else:
                    result = state.task.run(0 if all_units else unit_list, param, section_map,
                                            jitter=jitter, realtime=realtime,
                                            qsec=qsec, qcount=qcount)
            except Exception as e:
                msg = repr(e)   # except 块结束会自动删除 e,先取值再使用
                state._set(False, "待命")
                state.log(f"任务异常终止:{msg}")
                return
            with state.lock:
                state.last_result = result
            state._set(False, "待命")
            for line in _format_result(state.mode, result):
                state.log(line)

        with state.lock:   # 忙检查与线程登记同锁,防止并发请求双双通过后重复起线程
            if state.worker_busy() or state.fetching:
                return jsonify(ok=False, error="任务正在进行,请先停止"), 409
            state.stop_event.clear()
            # 通过忙检后才置运行状态:409 拒绝路径若提前置位,running 永远无人清除,界面卡死
            state.running = True
            state.running_what = "任务运行中"
            state.worker = threading.Thread(target=work, daemon=True)
        pace = (f" 每题 {qsec:.0f} 秒 x {qcount} 题 ≈ {qsec * qcount / 60:.1f} 分钟/章"
                if state.mode == "accuracy" else f" 乱序={'开' if shuffle else '关'}")
        state.log(f"开始任务:{desc} 参数={param}" +
                  ((f" 真实时间={'开' if realtime else '关'} 随机性={jitter:.0f}%" + pace)
                   if realtime else " 立即刷完"))
        state.worker.start()
        return jsonify(ok=True)

    @app.route("/api/rules", methods=["POST"])
    def api_rules():
        """把努力值计分规则原文(来自 icourse.apk 资源里的规则页)打到日志里。"""
        state.log(f"努力值计分规则(共 {len(welearn_effort.RULES)} 条,原文来自 App 计分规则页):")
        for line in welearn_effort.RULES:
            state.log("  · " + line)
        state.log("  学习类每分钟固定拿 3 点(在线+频度+时段各 1 点/分钟),"
                  "每满 3 分钟再加 10 点学习次数(同一练习上限 50 点);"
                  "测试/作业按百分制得分折算(听力 50 点、阅读 80 点 × 得分百分比)。")
        return jsonify(ok=True, rules=welearn_effort.RULES)


    @app.route("/api/probe", methods=["POST"])
    def api_probe():
        """只读抓分:只 GET 章节清单(scoLeaves),不 startsco/setscoinfo/savescoinfo。"""
        payload = request.get_json(silent=True) or {}
        with state.lock:
            task = state.task
            units = list(state.units or [])
            busy = state.running or state.worker_busy() or state.fetching
        if task is None:
            return jsonify(ok=False, error="请先登录"), 400
        if busy:
            return jsonify(ok=False, error="任务正在进行,请先停止"), 409
        if not getattr(task, "cid", ""):
            return jsonify(ok=False, error="请先选择课程并加载单元"), 400
        try:
            all_units, unit_list, _section_map = welearn_common.normalize_targets(
                payload.get("units", 0), payload.get("sections"))
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, error=f"参数错误:{exc}"), 400

        state.log("开始抓分(只读:仅读取章节清单,不提交任何学习数据)")
        try:
            result = welearn_effort.probe(
                task.session, task.cid, task.uid, task.classid,
                None if all_units else unit_list,
                on_log=state.log, stop_event=state.stop_event,
                advance_until_error=all_units,
                max_units=len(units) + 5)
        except (LoginError, WelearnError) as exc:
            state.log(f"抓分失败:{exc}")
            return jsonify(ok=False, error=str(exc)), 400

        for row in result["rows"]:
            state.log("  " + welearn_effort.format_row(row))
        totals = result["totals"]
        state.log(f"抓分完成:已完成 {totals['done']} 章,待刷 {totals['pending']} 章,"
                  f"合计 {totals['minutes']} 分钟 / 学习类约 {totals['study_points']} 点")
        raw_mode = payload.get("mode") or state.mode
        plan = welearn_score.plan(
            result["rows"],
            per_section_seconds=payload.get("duration") or payload.get("param") or 60,
            target=payload.get("target"),
            mode=raw_mode,
            percent=payload.get("accuracy") or payload.get("param") or 100)
        for line in welearn_score.format_plan(plan):
            state.log(line)
        totals["plan"] = plan
        return jsonify(ok=True, rows=result["rows"], totals=totals, errors=result["errors"],
                       plan=plan)


    @app.route("/api/target_start", methods=["POST"])
    def api_target_start():
        """按目标努力值刷:每刷一章就重新抓分,累计到目标就停(只算当前模式那一路)。"""
        payload = request.get_json(silent=True) or {}
        if state.task is None:
            return jsonify(ok=False, error="请先登录并选择课程"), 400
        raw_target = str(payload.get("target", "")).strip()
        if not raw_target:
            return jsonify(ok=False, error="请先在「目标努力值」里填写要刷到的分数"), 400
        try:
            target = int(float(raw_target))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="目标努力值必须是数字"), 400
        if target <= 0:
            return jsonify(ok=False, error="目标努力值要大于 0"), 400
        mode = payload.get("mode") or state.mode
        if mode not in ("time", "accuracy"):
            mode = "time"
        try:
            param = welearn_common.parse_numeric_param(str(payload.get("param", "60")))
        except ValueError as e:
            return jsonify(ok=False, error=f"参数格式错误:{e}"), 400
        jitter = welearn_time.TimeTask.normalize_jitter(
            payload.get("jitter", welearn_time.JITTER_DEFAULT))
        raw_realtime = payload.get("realtime", True)
        realtime = (raw_realtime if isinstance(raw_realtime, bool)
                    else str(raw_realtime).strip().lower() not in ("0", "false", "no", "off", ""))
        raw_shuffle = payload.get("shuffle", False)
        shuffle = (raw_shuffle if isinstance(raw_shuffle, bool)
                   else str(raw_shuffle).strip().lower() in ("1", "true", "yes", "on"))
        try:
            qsec = float(payload.get("qsec", welearn_accuracy.ANSWER_SECONDS_DEFAULT))
        except (TypeError, ValueError):
            qsec = welearn_accuracy.ANSWER_SECONDS_DEFAULT
        try:
            qcount = int(float(payload.get("qcount", welearn_accuracy.QUESTION_COUNT_DEFAULT)))
        except (TypeError, ValueError):
            qcount = welearn_accuracy.QUESTION_COUNT_DEFAULT

        def work():
            try:
                result = welearn_target.run(
                    state.task, mode, target, param, jitter=jitter, realtime=realtime,
                    qsec=qsec, qcount=qcount, shuffle=shuffle,
                    on_log=state.log, stop_event=state.stop_event)
            except Exception as e:
                msg = repr(e)
                state._set(False, "待命")
                state.log(f"按目标刷异常终止:{msg}")
                return
            with state.lock:
                state.last_result = result
            state._set(False, "待命")
            for line in welearn_target.format_result(result):
                state.log(line)

        with state.lock:
            if state.worker_busy() or state.fetching:
                return jsonify(ok=False, error="任务正在进行,请先停止"), 409
            state.stop_event.clear()
            state.running = True
            state.running_what = "按目标刷"
            state.worker = threading.Thread(target=work, daemon=True)
        state.log(f"按目标刷开始:目标 {target} 点,只算"
                  + ("学习时长" if mode == "time" else "完成度"))
        state.worker.start()
        return jsonify(ok=True, target=target, mode=mode)

    @app.route("/api/stop", methods=["POST"])
    def api_stop():
        state.stop_event.set()
        state.log("已请求停止,等待任务收尾...")
        return jsonify(ok=True)

    @app.route("/api/status")
    def api_status():
        try:
            after = int(request.args.get("after", "0"))
        except ValueError:
            after = 0
        with state.lock:
            logs = [list(entry) for entry in state.logs if entry[0] > after]
            return jsonify(running=state.running, running_what=state.running_what,
                           has_task=state.task is not None, mode=state.mode, logs=logs)

    return app, state


# raw 字符串:JS 里的 \n 必须原样送达浏览器;若被 Python 转义成真换行,字符串字面量断行 → SyntaxError
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Welearn Helper · 刷课工作台</title>
<style>
  :root {
    --bg: #EDF0F4;            /* 工作台底色 */
    --panel: #FFFFFF;         /* 配置区面板 */
    --ops: #141C26;           /* 执行区(运行条+控制台)深底 */
    --ops-line: #2C3A49;
    --ink: #1C2836;
    --ink-2: #56677A;
    --ink-3: #75828F;
    --line: #D8DEE6;
    --line-soft: #E8ECF1;
    --hover: #F4F7FA;
    --blue: #3D6FA5;          /* 选择/聚焦 */
    --run: #12753F;           /* 开始 */
    --run-h: #0E6134;
    --danger: #C13B3B;        /* 停止 */
    --ops-ink: #E1E8EF;       /* 执行区浅字 */
    --ops-dim: #8FA0B2;
    --console-dim: #6E8096;
    --mono: "Cascadia Mono", Consolas, "Courier New", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; }
  body { font: 14px/1.55 "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
         background: var(--bg); color: var(--ink); }
  .shell { max-width: 1240px; margin: 0 auto; padding: 18px 20px 24px; }

  /* 顶栏:品牌 + 出处 + 状态灯 */
  .topbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
            background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
            padding: 12px 18px; margin-bottom: 14px; }
  .brand { display: flex; align-items: baseline; gap: 12px; margin-right: auto; min-width: 0; }
  .brand h1 { font-size: 16px; font-weight: 700; margin: 0; letter-spacing: .2px; }
  .brand .sub { font-size: 12px; color: var(--ink-3); }
  .brand .sub a { color: var(--blue); text-decoration: none; }
  .brand .sub a:hover { text-decoration: underline; }
  .pill { display: inline-flex; align-items: center; gap: 8px; height: 28px; padding: 0 13px;
          border: 1px solid var(--line); border-radius: 14px; font-size: 12.5px; font-weight: 600;
          color: var(--ink-2); background: #fff; white-space: nowrap; }
  .pill .dot { width: 7px; height: 7px; border-radius: 50%; background: #93A2B1; }
  .pill.running { color: #8C5800; background: #FBF2DE; border-color: #EBD5A4; }
  .pill.running .dot { background: #D9940A; animation: blink 1.4s ease-in-out infinite; }
  @keyframes blink { 50% { opacity: .35; } }

  /* 双栏工作区:左=配置(浅),右=执行(深) */
  .workspace { display: grid; grid-template-columns: 400px minmax(0, 1fr); gap: 14px;
               align-items: stretch; }
  .rail { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .ops-col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
  .p-head { display: flex; align-items: center; gap: 9px; padding: 10px 14px;
            border-bottom: 1px solid var(--line-soft); }
  .p-step { width: 20px; height: 20px; flex: none; border: 1px solid var(--line); border-radius: 5px;
            display: grid; place-items: center; font: 600 11px/1 var(--mono); color: var(--ink-3); }
  .p-title { font-size: 13.5px; font-weight: 650; }
  .p-body { padding: 12px 14px 14px; }

  /* 表单 */
  .field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
  .f-label { font-size: 12px; color: var(--ink-2); }
  input[type=text], input[type=password], select {
    height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 6px;
    background: #fff; color: var(--ink); font: inherit; min-width: 0; width: 100%;
  }
  input:focus, select:focus { outline: none; border-color: var(--blue);
                              box-shadow: 0 0 0 3px rgba(61, 111, 165, .15); }
  input[type=checkbox], input[type=radio] { accent-color: var(--blue); width: 15px; height: 15px;
                                             flex: none; }
  .radio { display: inline-flex; align-items: center; gap: 7px; margin-right: 16px; cursor: pointer; }
  .row-inline { display: flex; align-items: center; gap: 8px; }
  .grow { flex: 1; }
  .tip { margin: 0 0 8px; font-size: 12px; color: var(--ink-3); }

  /* 按钮 */
  .btn { height: 34px; padding: 0 16px; border-radius: 6px; border: 1px solid var(--line);
         background: #fff; color: var(--ink); font-family: inherit; font-size: 13.5px;
         font-weight: 600; cursor: pointer; white-space: nowrap; }
  .btn:hover:not(:disabled) { border-color: #B9C4D0; background: var(--hover); }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .btn:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
  .btn-blue { border-color: var(--blue); color: var(--blue); }
  .btn-blue:hover:not(:disabled) { border-color: #33608F; background: #F0F5FA; }
  .btn-block { width: 100%; margin-top: 12px; }
  .btn-lg { height: 36px; padding: 0 22px; }
  .btn-run { background: var(--run); border-color: var(--run); color: #fff; }
  .btn-run:hover:not(:disabled) { background: var(--run-h); border-color: var(--run-h); }
  .btn-stop { background: transparent; border-color: #7E4A4A; color: #E89B9B; }
  .btn-stop:hover:not(:disabled) { background: #241A1A; border-color: #9C5B5B; }

  /* 单元/小节树 */
  .all-row { display: flex; align-items: center; gap: 8px; padding-bottom: 8px; margin-bottom: 8px;
             border-bottom: 1px dashed var(--line-soft); font-size: 13px; color: var(--ink-2);
             cursor: pointer; }
  #unitTree { max-height: 430px; overflow-y: auto; border: 1px solid var(--line-soft);
              border-radius: 6px; padding: 6px; }
  .unit-row { margin: 1px 0 5px; }
  .unit-row > label { display: flex; align-items: center; gap: 9px; padding: 6px 7px;
                      border-radius: 6px; cursor: pointer; user-select: none; }
  .unit-row > label:hover { background: var(--hover); }
  .u-no { flex: none; font: 600 11px var(--mono); color: var(--ink-3); letter-spacing: .3px; }
  .u-name { font-weight: 600; font-size: 13px; overflow-wrap: anywhere; }
  .sec-all-btn { flex: none; margin-left: auto; height: 22px; padding: 0 9px; border-radius: 5px;
                 border: 1px dashed var(--line-soft); background: transparent; color: var(--ink-3);
                 font-weight: 600; font-size: 11px; line-height: 1; font-family: inherit; cursor: pointer; }
  .sec-all-btn:hover { color: var(--blue); border-color: var(--blue); }
  .sec-all-btn:disabled { opacity: .5; cursor: default; }
  .unit-off .u-name { color: var(--ink-3); }
  .sec-box { margin: 1px 0 4px 31px; padding-left: 12px; border-left: 2px solid var(--line-soft); }
  .sec-row { display: flex; align-items: center; gap: 8px; padding: 3px 6px; border-radius: 5px;
             font-size: 13px; cursor: pointer; }
  .sec-row:hover { background: var(--hover); }
  .sec-name { color: var(--ink-2); overflow-wrap: anywhere; }
  .sec-hidden .sec-name { color: var(--ink-3); }
  .tag { flex: none; font-size: 11px; font-weight: 600; line-height: 1; padding: 3px 7px;
         border-radius: 4px; }
  .tag-done { color: #0E6B3C; background: #E7F4EC; }
  .tag-todo { color: #A33333; background: #FAECEC; }
  .tag-off { color: #6A7683; background: #EEF1F4; }
  .dim { color: var(--ink-3); font-size: 12.5px; padding: 4px 6px; }
  .f-note { color: var(--ink-3); font-size: 12px; font-weight: 600; }
  input[type=range] { width: 100%; margin-top: 6px; accent-color: var(--blue); }
  input[type=range]:disabled { cursor: not-allowed; }

  /* 执行区:深色,呼应控制台 */
  .ops { background: var(--ops); border-color: var(--ops-line); color: var(--ops-ink); }
  .runbar { padding: 12px 16px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .run-summary { display: flex; align-items: baseline; gap: 10px; min-width: 0; flex: 1; }
  .rs-k { flex: none; font-size: 11px; color: var(--ops-dim); font-weight: 600; }
  #runSummary { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .run-actions { display: flex; gap: 10px; margin-left: auto; }
  .console { flex: 1; display: flex; flex-direction: column; min-height: 420px;
             max-height: calc(100vh - 250px); overflow: hidden; }
  .console.collapsed { flex: none; min-height: 0; max-height: none; }
  .console.collapsed #log { display: none; }
  .p-head-d { border-bottom: 1px solid var(--ops-line); }
  .p-head-d .p-title { color: #D5DEE7; }
  .btn-ghost-d { height: 26px; padding: 0 11px; margin-left: auto; border-radius: 5px;
                 border: 1px solid #3A4A5A; background: transparent; color: var(--ops-dim);
                 font-family: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
  .btn-ghost-d:hover { color: var(--ops-ink); border-color: #54687C; background: #1B2632; }
  .btn-ghost-d:focus-visible { outline: 2px solid #7FA3CC; outline-offset: 2px; }
  #log { flex: 1; min-height: 0; overflow-y: auto; padding: 10px 16px 14px; white-space: pre-wrap;
         font: 12.5px/1.7 var(--mono); color: #C6D1DC; border-radius: 0 0 8px 8px; }
  #log .t { color: var(--console-dim); }
  #log .n { color: var(--console-dim); }
  #log .more { color: var(--console-dim); font-style: italic; }
  .log-tools { margin-left: auto; display: flex; align-items: center; gap: 6px; }
  .log-tools .btn-ghost-d { margin-left: 0; }
  .log-count { font: 11px var(--mono); color: var(--ops-dim); font-variant-numeric: tabular-nums; }

  @media (max-width: 940px) {
    .workspace { grid-template-columns: 1fr; }
    .console { min-height: 380px; }
  }
  @media (max-width: 460px) {
    .grid-2 { grid-template-columns: 1fr; }   /* 极窄屏:账号/密码上下排布 */
  }
  @media (prefers-reduced-motion: reduce) {
    .pill.running .dot { animation: none; }
  }
</style>
</head>
<body>
<div class="shell">

<header class="topbar">
  <div class="brand">
    <h1>Welearn Helper</h1>
    <span class="sub">刷课工作台(原项目 <a href="https://github.com/Avenshy" target="_blank"
      rel="noreferrer">Avenshy</a> &amp; SSmJaE,由 <a
      href="https://github.com/lxbsdsg/Welearn_helper/" target="_blank"
      rel="noreferrer">lxbsdsg</a> 维护)</span>
  </div>
  <span class="pill" id="statusPill"><span class="dot"></span><span id="statusText">待命</span></span>
</header>

<div class="workspace">

  <div class="rail">
    <section class="panel">
      <div class="p-head"><span class="p-step">1</span><span class="p-title">会话</span></div>
      <div class="p-body">
        <div class="grid-2">
          <div class="field"><span class="f-label">账号</span>
            <input type="text" id="username" autocomplete="username"></div>
          <div class="field"><span class="f-label">密码</span>
            <input type="password" id="password" autocomplete="current-password"></div>
        </div>
        <div class="field"><span class="f-label">模式</span>
          <span class="row-inline">
            <label class="radio"><input type="radio" name="mode" value="accuracy" checked>刷完成度正确率</label>
            <label class="radio"><input type="radio" name="mode" value="time">刷学习时长</label>
          </span>
        </div>
        <button id="loginBtn" class="btn btn-blue btn-block">登录</button>
      </div>
    </section>

    <section class="panel">
      <div class="p-head"><span class="p-step">2</span><span class="p-title">课程</span></div>
      <div class="p-body row-inline">
        <select id="courseSel" class="grow"><option value="">-- 请先登录 --</option></select>
        <button id="refreshBtn" class="btn">刷新课程</button>
      </div>
    </section>

    <section class="panel">
      <div class="p-head"><span class="p-step">3</span><span class="p-title">单元与小节</span></div>
      <div class="p-body">
        <p class="tip">勾选单元会自动全选它下面的小节;可用「全选小节/取消全选」按钮或单独取消某些小节来微调,一个都不勾 = 不刷这个单元。</p>
        <label class="all-row" for="allUnits"><input type="checkbox" id="allUnits">全部单元(忽略下方勾选)</label>
        <div id="unitTree"></div>
      </div>
    </section>

    <section class="panel">
      <div class="p-head"><span class="p-step">4</span><span class="p-title">任务参数</span></div>
      <div class="p-body">
        <div class="field"><span class="f-label" id="paramLabel">正确率(100 或 70,100):</span>
          <input type="text" id="param"></div>
        <div class="field" id="realtimeField"><label class="all-row" for="realtime"><input type="checkbox" id="realtime" checked>按真实节奏刷(时长=每章占满秒数;正确率=按「每题秒数 × 题量」铺开作答;取消勾选=立即刷完)<span class="f-note" id="realtimeHint"></span></label></div>
        <div class="field" id="jitterField"><span class="f-label">随机性 <b id="jitterVal">20%</b> (每章时长在 ±该比例内浮动,0 = 完全规律):</span>
          <input type="range" id="jitter" class="range" min="0" max="100" step="5" value="20"></div>
        <div class="field" id="paceField"><span class="f-label">作答节奏(正确率模式):每题预估秒数 / 题量 —— 每章按两者乘积铺开,不会瞬间提交</span>
          <div style="display:flex; gap:8px;">
            <input type="number" id="qsec" min="1" max="600" step="1" value="25" title="每题预估秒数">
            <input type="number" id="qcount" min="1" max="200" step="1" value="10" title="一份练习的题量">
          </div></div>
        <div class="field" id="targetField"><label class="all-row" for="target">目标努力值(留空 = 只显示预计;填了就算还差多少)</label><input type="number" id="target" min="0" max="100000" step="10" placeholder="例如 900,留空则只显示预计"></div><div class="field" id="shuffleField"><label class="all-row" for="shuffle"><input type="checkbox" id="shuffle">乱序刷(打乱章节执行顺序,不按单元/小节从小到大)</label></div>
          <span class="f-note">填了分数就点下面蓝色「按目标刷」:每刷一章重新抓分,累计到目标自动停;只想看预计值就留空,点「抓分(只读)」</span>
      </div>
    </section>
  </div>

  <div class="ops-col">
    <section class="panel ops runbar">
      <div class="run-summary">
        <span class="rs-k">目标</span>
        <span id="runSummary">未选择单元</span>
      </div>
      <div class="run-actions">
        <button id="probeBtn" class="btn btn-lg" type="button" title="只读抓取每章节已学时长与学习类努力值,不提交任何数据" style="margin-right:8px;background:#eef2f7;color:#334155;box-shadow:none;">抓分(只读)</button>
        <button id="targetBtn" class="btn btn-lg" type="button" title="按目标努力值一直刷到达标为止(每章后重新抓分)" style="margin-right:8px;background:#2563eb;color:#fff;box-shadow:none;">按目标刷</button>
        <button id="rulesBtn" class="btn btn-lg" type="button" title="显示努力值计分规则(来自 App 计分规则页)" style="margin-right:8px;background:#eef2f7;color:#334155;box-shadow:none;">计分规则</button>
        <button id="startBtn" class="btn btn-lg btn-run" disabled>开始任务</button>
        <button id="stopBtn" class="btn btn-lg btn-stop" disabled>停止</button>
      </div>
    </section>

    <section class="panel ops console">
      <div class="p-head p-head-d">
        <span class="p-title">运行日志</span>
        <span class="log-tools">
          <span id="logCount" class="log-count"></span>
          <button id="logToggle" class="btn-ghost-d" type="button">收起</button>
          <button id="clearBtn" class="btn-ghost-d" type="button">清空</button>
        </span>
      </div>
      <div id="log"></div>
    </section>
  </div>

</div>
</div>

<script>
'use strict';
var lastSeq = 0;
var courses = [];
var modeSwitching = false;
window.__hasTask = false;
var $ = function (id) { return document.getElementById(id); };

function apiPost(path, body) {
  return fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify(body || {})})
    .then(function (r) { return r.json(); });
}

function getMode() {
  var checked = document.querySelector('input[name=mode]:checked');
  return checked ? checked.value : 'accuracy';
}

function setModeLabel() {
  $('paramLabel').textContent = getMode() === 'time'
    ? '每练习时长秒(30 或 10,30):' : '正确率(100 或 70,100):';
  updateJitterVisibility();
}

/* 始终可见:只有「刷学习时长」模式可改,正确率模式下禁用并淡化,免得以为没有这项 */
function updateJitterVisibility() {
  var isTime = getMode() === 'time';
  var rt = $('realtime').checked;
  $('realtime').disabled = false;
  $('jitter').disabled = !rt;
  $('jitterField').style.opacity = rt ? '' : '0.55';
  $('shuffle').disabled = !isTime;
  $('shuffleField').style.opacity = isTime ? '' : '0.55';
  $('paceField').style.display = isTime ? 'none' : '';
  $('paceField').style.opacity = rt ? '' : '0.55';
  $('qsec').disabled = !rt;
  $('qcount').disabled = !rt;
  $('realtimeHint').textContent = isTime ? '' : '  ← 正确率模式按「每题秒数 x 题量」铺开作答';
}

function selectMode(mode) {
  var radio = document.querySelector('input[name=mode][value="' + mode + '"]');
  if (radio) radio.checked = true;
  setModeLabel();
}

function setControls(running) {
  var busy = running || modeSwitching;
  $('loginBtn').disabled = busy;
  $('refreshBtn').disabled = busy;
  $('startBtn').disabled = busy || !window.__hasTask;
  $('stopBtn').disabled = !busy;
  document.querySelectorAll('input[name=mode]').forEach(function (radio) {
    radio.disabled = busy;
  });
  $('statusPill').classList.toggle('running', busy);
}

function fillCourses(list, keepSelection) {
  courses = list || [];
  var sel = $('courseSel');
  var prev = keepSelection ? sel.value : '';
  sel.innerHTML = '';
  if (!courses.length) {
    sel.appendChild(new Option('-- 无课程 --', ''));
    return;
  }
  for (var i = 0; i < courses.length; i++) {
    var c = courses[i];
    sel.appendChild(new Option('完成度' + c.per + '%  ' + c.name, String(c.cid)));
  }
  if (prev) {   // 刷新后恢复原选择,避免用户重新点一遍
    for (var k = 0; k < sel.options.length; k++) {
      if (sel.options[k].value === prev) { sel.value = prev; break; }
    }
  }
}

function tag(text, cls) {
  var s = document.createElement('span');
  s.className = 'tag ' + cls;
  s.textContent = text;
  return s;
}

function renderTree(units) {
  var tree = $('unitTree');
  tree.innerHTML = '';
  var list = units || [];
  if (!list.length) {
    var hint = document.createElement('div');
    hint.className = 'dim';
    hint.textContent = '-- 请先选择课程加载单元 --';
    tree.appendChild(hint);
    updateSummary();
    return;
  }
  for (var i = 0; i < list.length; i++) {
    var u = list[i];
    var no = i + 1;
    var visible = String(u.visible || 'true').toLowerCase() === 'true';
    var row = document.createElement('div');
    row.className = 'unit-row';
    var label = document.createElement('label');
    if (!visible) label.className = 'unit-off';   // 未开放单元灰显,仍可勾(其小节会被跳过)
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'unit-cb';
    cb.dataset.no = String(no);
    cb.addEventListener('change', function () { onUnitToggled(this); });
    label.appendChild(cb);
    var noSpan = document.createElement('span');
    noSpan.className = 'u-no';
    noSpan.textContent = 'NO.' + no;
    label.appendChild(noSpan);
    if (!visible) label.appendChild(tag('未开放', 'tag-off'));
    var nameSpan = document.createElement('span');
    nameSpan.className = 'u-name';
    nameSpan.textContent = u.unitname + ' ' + u.name;
    label.appendChild(nameSpan);
    var secAllBtn = document.createElement('button');
    secAllBtn.type = 'button';
    secAllBtn.className = 'sec-all-btn';
    secAllBtn.dataset.no = String(no);
    secAllBtn.textContent = '全选小节';
    /* 按钮在 label 内部:必须掐掉默认动作,否则点它会连带把单元勾选翻面 */
    secAllBtn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var n = this.dataset.no;
      var secs = unitSecs(n);
      var allOn = secs.length > 0 && secs.every(function (sc) { return sc.checked; });
      setUnitSecs(n, !allOn);
    });
    label.appendChild(secAllBtn);
    var secBox = document.createElement('div');
    secBox.className = 'sec-box';
    secBox.dataset.no = String(no);
    secBox.style.display = 'none';
    row.appendChild(label);
    row.appendChild(secBox);
    tree.appendChild(row);
  }
  updateSummary();
}

function renderSecs(no, sections) {
  var box = document.querySelector('.sec-box[data-no="' + no + '"]');
  if (!box) return;
  box.innerHTML = '';
  if (!sections || !sections.length) {
    var d = document.createElement('div');
    d.className = 'dim';
    d.textContent = '-- 该单元无小节数据 --';
    box.appendChild(d);
    return;
  }
  for (var i = 0; i < sections.length; i++) {
    var s = sections[i];
    var secNo = i + 1;   // 编号即小节参数依据
    var label = document.createElement('label');
    label.className = 'sec-row ' + (!s.visible ? 'sec-hidden'
                                     : (s.complete ? 'sec-done' : 'sec-todo'));
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'sec-cb';
    cb.dataset.no = String(no);
    cb.dataset.sec = String(secNo);
    if (!s.visible) { cb.disabled = true; cb.dataset.baseDisabled = '1'; }   // 未开放小节不可刷
    cb.addEventListener('change', function () { onSecToggled(this); });
    label.appendChild(cb);
    var noSpan = document.createElement('span');
    noSpan.className = 'u-no';
    noSpan.textContent = secNo + '.';
    label.appendChild(noSpan);
    label.appendChild(tag(!s.visible ? '未开放' : (s.complete ? '已完成' : '未完成'),
                          !s.visible ? 'tag-off' : (s.complete ? 'tag-done' : 'tag-todo')));
    var nameSpan = document.createElement('span');
    nameSpan.className = 'sec-name';
    nameSpan.textContent = s.location;
    label.appendChild(nameSpan);
    box.appendChild(label);
  }
  syncSecAllLabel(no);
  updateSummary();
}

/* 勾选单元时懒加载其小节;done 回调用于恢复勾选场景(加载完成后再打勾)。
   加载中重复调用会把 done 入队,由同一次请求返回后统一触发——
   若按"有子节点即已完成"判断,先到的无回调调用会让后到的 done 在
   "加载中"占位符上空跑,勾选恢复静默失效 */
function ensureSecs(no, done) {
  var box = document.querySelector('.sec-box[data-no="' + no + '"]');
  if (!box) { if (done) done(); return; }
  if (box.dataset.loaded === '1') { if (done) done(); return; }
  if (done) {
    if (!box._pendingDones) box._pendingDones = [];
    box._pendingDones.push(done);
  }
  if (box.dataset.loading === '1') return;
  box.dataset.loading = '1';
  var loading = document.createElement('div');
  loading.className = 'dim';
  loading.textContent = '-- 小节加载中... --';
  box.appendChild(loading);
  var flush = function () {
    var q = box._pendingDones || [];
    box._pendingDones = [];
    q.forEach(function (fn) { fn(); });
  };
  apiPost('/api/sections', {unit_index: no})
    .then(function (j) {
      box.dataset.loading = '';
      if (!j.ok) {
        box.innerHTML = '';
        appendLocal('加载小节失败:' + j.error);
      } else {
        renderSecs(no, j.sections);
        applyAllUnits();
        box.dataset.loaded = '1';
      }
      flush();
    })
    .catch(function (err) {
      box.dataset.loading = '';
      box.innerHTML = '';
      appendLocal('请求失败:' + err);
      flush();
    });
}

/* 某个单元下所有可勾选的小节(未开放小节的 disabled 不算) */
function unitSecs(no) {
  var all = document.querySelectorAll('.sec-cb[data-no="' + no + '"]');
  var out = [];
  for (var i = 0; i < all.length; i++) {
    if (!all[i].disabled) out.push(all[i]);
  }
  return out;
}

/* 父单元勾选跟随小节:全勾=实心勾,部分=横线(半选),全不勾=取消勾选(仍留着面板可再勾) */
function syncUnitCb(no) {
  var cb = document.querySelector('.unit-cb[data-no="' + no + '"]');
  if (!cb) return;
  var secs = unitSecs(no);
  if (!secs.length) { cb.indeterminate = false; return; }
  var on = 0;
  for (var i = 0; i < secs.length; i++) if (secs[i].checked) on++;
  if (on === secs.length) {
    cb.checked = true;
    cb.indeterminate = false;
  } else if (on > 0) {
    cb.checked = true;
    cb.indeterminate = true;
  } else {
    cb.checked = false;
    cb.indeterminate = false;
  }
}

/* 按钮文案跟着状态走 */
function syncSecAllLabel(no) {
  var btn = document.querySelector('.sec-all-btn[data-no="' + no + '"]');
  if (!btn) return;
  var secs = unitSecs(no);
  var allOn = secs.length > 0 && secs.every(function (sc) { return sc.checked; });
  btn.textContent = allOn ? '取消全选' : '全选小节';
}

/* 全选/全不选某单元下的小节(按钮用) */
function setUnitSecs(no, on) {
  var cb = document.querySelector('.unit-cb[data-no="' + no + '"]');
  if (on && cb && !cb.checked) {   // 单元还没勾:先勾上(会自动展开并全选)
    cb.checked = true;
    onUnitToggled(cb, true);
    return;
  }
  ensureSecs(no, function () {
    unitSecs(no).forEach(function (sc) { sc.checked = on; });
    if (!on && cb) cb.indeterminate = false;
    syncUnitCb(no);
    syncSecAllLabel(no);
    updateSummary();
  });
}

function onSecToggled(sc) {
  var no = sc.dataset.no;
  syncUnitCb(no);
  syncSecAllLabel(no);
  updateSummary();
}

/* autoSelect: 用户手动勾单元时自动全选小节;恢复勾选(刷新后复原)传 false,避免覆盖用户原有的部分选择 */
function onUnitToggled(cb, autoSelect) {
  var no = Number(cb.dataset.no);
  var box = document.querySelector('.sec-box[data-no="' + no + '"]');
  if (!box) return;
  var auto = autoSelect !== false;
  if (cb.checked) {
    box.style.display = '';
    ensureSecs(no, function () {
      if (auto) unitSecs(no).forEach(function (sc) { sc.checked = true; });
      syncUnitCb(no);
      syncSecAllLabel(no);
      updateSummary();
    });
  } else {
    box.style.display = 'none';
    unitSecs(no).forEach(function (sc) { sc.checked = false; });
    cb.indeterminate = false;
    syncSecAllLabel(no);
    updateSummary();
  }
}

function applyAllUnits() {
  var on = $('allUnits').checked;
  document.querySelectorAll('.unit-cb').forEach(function (cb) { cb.disabled = on; });
  document.querySelectorAll('.sec-cb').forEach(function (sc) {
    sc.disabled = on || sc.dataset.baseDisabled === '1';
  });
  document.querySelectorAll('.sec-all-btn').forEach(function (b) { b.disabled = on; });
  $('unitTree').style.opacity = on ? '0.5' : '';
  updateSummary();
}

/* 运行条摘要:实时反映勾选目标与参数(纯展示,选择仍以 buildSelection 为准) */
function updateSummary() {
  var el = $('runSummary');
  if (!el) return;
  var p = $('param').value.trim();
  var suffix = p ? '(参数 ' + p + ')' : '';
  if (getMode() === 'time') {
    suffix += $('realtime').checked
      ? ' 真实时间刷 随机性 ' + $('jitter').value + '%'
      : ' 立即刷完';
    if ($('shuffle').checked) suffix += ' 乱序';
  if ($('target').value.trim()) {
    suffix += ' 目标 ' + $('target').value.trim() + ' 点(只算'
      + (getMode() === 'time' ? '学习时长' : '完成度') + ')';
  }
  }
  if (getMode() !== 'time' && $('realtime').checked) {
    suffix += ' 每题 ' + ($('qsec').value || 25) + ' 秒 x ' + ($('qcount').value || 10) + ' 题';
  }
  if ($('allUnits').checked) {
    el.textContent = '全部单元' + suffix;
    return;
  }
  var byNo = {};
  var unitCount = 0;
  document.querySelectorAll('.unit-cb').forEach(function (cb) {
    if (cb.checked) { byNo[cb.dataset.no] = true; unitCount++; }
  });
  if (!unitCount) { el.textContent = '未选择单元'; return; }
  var secCount = 0;
  document.querySelectorAll('.sec-cb:checked').forEach(function (sc) {
    if (!sc.disabled && byNo[sc.dataset.no]) secCount++;
  });
  el.textContent = unitCount + ' 个单元' + (secCount ? ' / ' + secCount + ' 个小节' : '') + suffix;
}

function resetTree() {
  $('allUnits').checked = false;
  applyAllUnits();
  renderTree([]);
}

/* 从界面收集任务目标:{allUnits, units:[编号], sections:{编号:[小节编号]}} */
function buildSelection() {
  var sel = {allUnits: $('allUnits').checked, units: [], sections: {}};
  if (sel.allUnits) return sel;
  var byNo = {};
  document.querySelectorAll('.unit-cb').forEach(function (cb) {
    if (cb.checked) { byNo[cb.dataset.no] = true; sel.units.push(Number(cb.dataset.no)); }
  });
  document.querySelectorAll('.sec-cb').forEach(function (sc) {
    if (!sc.checked || sc.disabled) return;
    if (!byNo[sc.dataset.no]) return;   // 单元未勾选时其小节勾选无效
    if (!sel.sections[sc.dataset.no]) sel.sections[sc.dataset.no] = [];
    sel.sections[sc.dataset.no].push(Number(sc.dataset.sec));
  });
  return sel;
}

/* 刷新/重登后恢复勾选。顺序很重要:先拉小节、按记录打勾,再决定单元勾选状态——
   否则 onUnitToggled 在"小节暂时都是 0 勾"的瞬间会把单元勾掉,恢复静默失效。
   有明确小节记录的按记录恢复;没有记录(旧数据/未加载)的按整单元处理。 */
function restoreSelection(sel) {
  if (!sel) return;
  $('allUnits').checked = !!sel.allUnits;
  applyAllUnits();
  var want = {};
  (sel.units || []).forEach(function (no) { want[no] = true; });
  var secs = sel.sections || {};
  Object.keys(secs).forEach(function (noStr) {
    if (!want[noStr]) return;
    var list = secs[noStr] || [];
    var cb = document.querySelector('.unit-cb[data-no="' + noStr + '"]');
    var box = document.querySelector('.sec-box[data-no="' + noStr + '"]');
    if (!cb || !list.length) return;
    cb.checked = true;
    if (box) box.style.display = '';
    ensureSecs(Number(noStr), function () {
      document.querySelectorAll('.sec-cb[data-no="' + noStr + '"]').forEach(function (sc) {
        sc.checked = list.indexOf(Number(sc.dataset.sec)) >= 0;
      });
      syncUnitCb(noStr);
      syncSecAllLabel(noStr);
      updateSummary();
    });
  });
  document.querySelectorAll('.unit-cb').forEach(function (cb) {
    var no = cb.dataset.no;
    if (!want[no]) return;
    if (secs[no] && secs[no].length) return;   // 已按记录恢复
    cb.checked = true;
    onUnitToggled(cb, true);                   // 无小节记录 = 整单元,自动全选
  });
  updateSummary();
}

/* 日志区自我保护:DOM 最多留 LOG_MAX 行(刷上百章时不会越滚越卡),
   相邻同文本的行合并成一条并标 ×N,新行进来时旧行按上限丢弃 */
var LOG_MAX = 600;
var dupEl = null;
var dupText = null;
var dupN = 0;

function logLine(timeText, text) {
  var line = document.createElement('div');
  var t = document.createElement('span');
  t.className = 't';
  t.textContent = '[' + timeText + '] ';
  var m = document.createElement('span');
  m.className = 'm';
  m.textContent = text;
  var badge = document.createElement('span');
  badge.className = 'n';
  line.appendChild(t);
  line.appendChild(m);
  line.appendChild(badge);
  return line;
}

function trimLog(box) {
  while (box.children.length > LOG_MAX) {
    var first = box.firstChild;
    if (first === dupEl) { dupEl = null; dupText = null; dupN = 0; }
    box.removeChild(first);
  }
}

function updateLogCount() {
  var el = $('logCount');
  var box = $('log');
  if (!el || !box) return;
  var n = box.children.length;
  el.textContent = n ? n + ' 行 / 最多 ' + LOG_MAX : '';
}

function pushLogLine(box, timeText, text) {
  if (dupEl && dupText === text && dupEl.parentNode === box) {
    dupN++;
    dupEl.firstChild.textContent = '[' + timeText + '] ';
    var badge = dupEl.querySelector('.n');
    if (badge) badge.textContent = '  ×' + dupN;
    return;
  }
  var line = logLine(timeText, text);
  box.appendChild(line);
  dupEl = line;
  dupText = text;
  dupN = 1;
  trimLog(box);
}

function appendLogs(entries) {
  var box = $('log');
  var autoscroll = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  for (var i = 0; i < entries.length; i++) {
    var e = entries[i];
    if (e[0] > lastSeq) lastSeq = e[0];
    pushLogLine(box, e[1], e[2]);
  }
  if (autoscroll) box.scrollTop = box.scrollHeight;
  updateLogCount();
}

/* 本地提示行:不经过 appendLogs,避免伪造序号污染 lastSeq 导致后续服务端日志全部丢失 */
function appendLocal(text) {
  var box = $('log');
  var d = new Date();
  var hh = ('0' + d.getHours()).slice(-2);
  var mi = ('0' + d.getMinutes()).slice(-2);
  var ss = ('0' + d.getSeconds()).slice(-2);
  pushLogLine(box, hh + ':' + mi + ':' + ss, text);
  box.scrollTop = box.scrollHeight;
  updateLogCount();
}

function resetLog() {
  var box = $('log');
  box.innerHTML = '';
  dupEl = null;
  dupText = null;
  dupN = 0;
  updateLogCount();
}

/* 刷新课程(保留选择);若已选课程则连单元一起刷新并恢复单元/小节勾选 */
function refreshAll(kind) {
  var prevSel = buildSelection();
  apiPost('/api/refresh', {})
    .then(function (j) {
      if (!j.ok) { if (kind === 'manual') alert('刷新失败\n' + j.error); return; }
      fillCourses(j.courses, true);
      if (!$('courseSel').value) { renderTree([]); return; }
      apiPost('/api/units', {cid: Number($('courseSel').value)})
        .then(function (j2) {
          if (!j2.ok) { renderTree([]); return; }
          renderTree(j2.units);
          restoreSelection(prevSel);
        })
        .catch(function () {});
    })
    .catch(function (err) {
      if (kind === 'manual') alert('请求失败:' + err);
    });
}

document.querySelectorAll('input[name=mode]').forEach(function (radio) {
  radio.addEventListener('change', function () {
    var newMode = getMode();
    var previousMode = newMode === 'time' ? 'accuracy' : 'time';
    setModeLabel();
    if (!window.__hasTask) {
      appendLocal('已切换模式,登录后生效');
      return;
    }

    modeSwitching = true;
    setControls(false);
    apiPost('/api/mode', {mode: newMode})
      .then(function (j) {
        if (!j.ok) {
          selectMode(previousMode);
          alert('切换模式失败\n' + j.error);
          modeSwitching = false;
          setControls(false);
          return;
        }
        appendLocal('已切换模式,无需重新登录');
        modeSwitching = false;
        setControls(false);
      })
      .catch(function () {
        selectMode(previousMode);
        apiPost('/api/mode', {mode: previousMode})
          .catch(function () {})
          .then(function () {
            modeSwitching = false;
            setControls(false);
          });
      });
  });
});

$('loginBtn').addEventListener('click', function () {
  var username = $('username').value.trim();
  var password = $('password').value;
  if (!username || !password) { alert('请输入账号和密码'); return; }
  var prevCourse = $('courseSel').value;
  var prevSel = buildSelection();
  apiPost('/api/login', {username: username, password: password, mode: getMode()})
    .then(function (j) {
      if (!j.ok) { alert('登录失败\n' + j.error); return; }
      window.__hasTask = true;
      fillCourses(j.courses, true);
      if (prevCourse && $('courseSel').value === prevCourse
          && (prevSel.units.length || prevSel.allUnits)) {
        /* 重新登录且课程未变:自动恢复单元与小节勾选,无需重选 */
        apiPost('/api/units', {cid: Number(prevCourse)})
          .then(function (j2) {
            if (!j2.ok) { renderTree([]); return; }
            renderTree(j2.units);
            restoreSelection(prevSel);
          })
          .catch(function () { renderTree([]); });
      } else {
        /* 首次登录:下拉默认高亮第一门课,但浏览器不会再触发 change——
           必须主动加载其单元,否则"看起来选了课却没有单元" */
        var firstCid = $('courseSel').value;
        if (firstCid) {
          apiPost('/api/units', {cid: Number(firstCid)})
            .then(function (j2) { renderTree(j2.ok ? j2.units : []); })
            .catch(function () { renderTree([]); });
        } else {
          renderTree([]);
        }
      }
      setControls(false);
    })
    .catch(function (err) { alert('请求失败:' + err); });
});

$('refreshBtn').addEventListener('click', function () {
  refreshAll('manual');
});

$('courseSel').addEventListener('change', function () {
  var cid = $('courseSel').value;
  if (!cid) return;
  apiPost('/api/units', {cid: Number(cid)})
    .then(function (j) {
      if (!j.ok) { alert('加载单元失败\n' + j.error); renderTree([]); return; }
      renderTree(j.units);
    })
    .catch(function (err) { alert('请求失败:' + err); });
});

$('allUnits').addEventListener('change', applyAllUnits);
$('param').addEventListener('input', updateSummary);
$('jitter').addEventListener('input', function () {
  $('jitterVal').textContent = $('jitter').value + '%';
  updateSummary();
});
$('realtime').addEventListener('change', function () {
  updateJitterVisibility();
  updateSummary();
});
$('shuffle').addEventListener('change', updateSummary);
updateJitterVisibility();
$('clearBtn').addEventListener('click', resetLog);

/* 收起日志面板:整块收成只剩标题栏,页面不再被长日志拖长 */
$('logToggle').addEventListener('click', function () {
  var panel = document.querySelector('.console');
  panel.classList.toggle('collapsed');
  var collapsed = panel.classList.contains('collapsed');
  $('logToggle').textContent = collapsed ? '展开' : '收起';
});

$('rulesBtn').addEventListener('click', function () {
  apiPost('/api/rules', {})
    .then(function (j) { if (!j.ok) alert(j.error); })
    .catch(function (err) { alert('请求失败:' + err); });
});

$('probeBtn').addEventListener('click', function () {
  var sel = buildSelection();
  if (!sel.allUnits && !sel.units.length) {
    alert('请至少勾选一个单元,或勾选"全部单元"');
    return;
  }
  $('probeBtn').disabled = true;
  $('statusText').textContent = '抓分中(只读)...';
  apiPost('/api/probe', {units: sel.allUnits ? 0 : sel.units,
                         sections: sel.allUnits ? null : sel.sections,
                         mode: getMode(),
                         accuracy: $('param').value,
                         duration: getMode() === 'time'
                           ? (Number($('param').value) || 60)
                           : (Number($('qsec').value) || 25),
                         target: $('target').value.trim()})
    .then(function (j) {
      if (!j.ok) { alert(j.error); return; }
      var t = j.totals || {};
      var p = j.plan || {};
      var head = '抓分完成:' + (t.chapters || 0) + ' 章';
      if (p.total !== undefined) {
        if (p.mode === 'accuracy') {
          head += ' | 预计可得(仅完成度)' + (p.accuracy_points || 0) + ' 点';
        } else if (p.mode === 'time') {
          head += ' | 预计可得(仅学习时长)' + (p.time_points || 0) + ' 点';
        } else {
          head += ' | 预计可得 ' + p.total + ' 点(时长 +' + (p.time_points || 0)
            + ' / 完成度 +' + (p.accuracy_points || 0) + ')';
        }
        if (p.target !== undefined) {
          if (p.reachable) {
            head += ' 已达目标 ' + p.target;
          } else if (p.mode === 'accuracy') {
            head += ' | 目标 ' + p.target + ' 还差 ' + p.gap + ' 点,需再刷 '
              + p.tasks_needed + ' 章到 ' + p.percent + '% 正确率';
          } else {
            head += ' | 目标 ' + p.target + ' 还差 ' + p.gap + ' 点,需再刷 '
              + p.sections_needed + ' 章';
          }
        }
      }
      $('statusText').textContent = head;
    })
    .catch(function (err) { alert('请求失败:' + err); })
    .finally(function () { $('probeBtn').disabled = false; });
});

$('targetBtn').addEventListener('click', function () {
  var sel = buildSelection();
  if (!sel.allUnits && !sel.units.length) {
    alert('请至少勾选一个单元(按目标刷会按这些单元里的章节逐个补分)');
    return;
  }
  var goal = $('target').value.trim();
  if (!goal) {
    alert('请先在「目标努力值」里填写要刷到的分数');
    $('target').focus();
    return;
  }
  if (!confirm('按目标刷到 ' + goal + ' 点(只算''
    + (getMode() === 'time' ? '学习时长' : '完成度') + ''),开始后每刷一章都会重新抓分?')) return;
  $('targetBtn').disabled = true;
  $('statusText').textContent = '按目标刷中...';
  apiPost('/api/target_start', {mode: getMode(),
                                param: $('param').value,
                                target: goal,
                                jitter: Number($('jitter').value),
                                realtime: $('realtime').checked,
                                qsec: Number($('qsec').value) || 25,
                                qcount: Number($('qcount').value) || 10,
                                shuffle: $('shuffle').checked})
    .then(function (j) {
      if (!j.ok) { alert(j.error); $('targetBtn').disabled = false; return; }
      setControls(true);
      $('statusText').textContent = '按目标刷中(目标 ' + goal + ' 点)';
    })
    .catch(function (err) { alert('请求失败:' + err); $('targetBtn').disabled = false; });
});

$('target').addEventListener('input', updateSummary);
$('qsec').addEventListener('input', updateSummary);
$('qcount').addEventListener('input', updateSummary);

$('startBtn').addEventListener('click', function () {
  var sel = buildSelection();
  if (!sel.allUnits && !sel.units.length) {
    alert('请至少勾选一个单元,或勾选"全部单元"');
    return;
  }
  var body = {units: sel.allUnits ? 0 : sel.units,
              sections: sel.allUnits ? null : sel.sections,
              param: $('param').value,
              jitter: Number($('jitter').value),
              realtime: $('realtime').checked,
              shuffle: $('shuffle').checked,
              qsec: Number($('qsec').value) || 25,
              qcount: Number($('qcount').value) || 10};
  apiPost('/api/start', body)
    .then(function (j) {
      if (!j.ok) { alert(j.error); return; }
      setControls(true);
      $('statusText').textContent = '任务运行中';
    })
    .catch(function (err) { alert('请求失败:' + err); });
});

$('stopBtn').addEventListener('click', function () {
  apiPost('/api/stop', {}).catch(function (err) { alert('请求失败:' + err); });
});

var wasRunning = false;

function poll() {
  fetch('/api/status?after=' + lastSeq)
    .then(function (r) { return r.json(); })
    .then(function (j) {
      appendLogs(j.logs);
      if (wasRunning && !j.running) {
        /* 任务刚结束:课程完成度与小节状态已变,自动刷新(保留全部选择) */
        appendLocal('[界面] 任务结束,自动刷新课程与小节状态...');
        refreshAll('task-end');
      }
      wasRunning = j.running;
      window.__hasTask = j.has_task;
      if (j.has_task && j.mode && !modeSwitching) selectMode(j.mode);
      $('statusText').textContent = j.running_what;
      setControls(j.running);
    })
    .catch(function () { /* 服务未就绪或已关闭,下轮重试 */ });
}

renderTree([]);
poll();
setInterval(poll, 1000);
</script>
</body>
</html>
"""


class _QuietStatusPoll(logging.Filter):
    """控制台隐藏 /api/status 轮询访问行(每秒一条会刷屏),其余访问行保留"""

    def filter(self, record):
        return "/api/status" not in record.getMessage()


def main():
    logging.getLogger("werkzeug").addFilter(_QuietStatusPoll())
    port = int(os.environ.get("WELEARN_PORT", "17777"))
    url = f"http://127.0.0.1:{port}/"
    print(BANNER)
    print(f"Web 界面地址: {url}  (Ctrl+C 退出)")
    if os.environ.get("WELEARN_NO_OPEN") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True)


app, state = create_app()

if __name__ == '__main__':
    main()
