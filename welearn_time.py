import asyncio
import json
import random
import threading
import time
from textwrap import dedent
from typing import Any, Callable, Dict, List, Optional, Tuple

import welearn_common
from welearn_common import LoginError, WelearnError

REQUEST_INTERVAL = 2
HEARTBEAT_INTERVAL = 1
KEEPALIVE_INTERVAL = 60      # 每真实约 60 秒向服务端续期一次学习时长
JITTER_DEFAULT = 20.0        # 默认随机性(%):每章时长在目标值上下该比例内浮动
GAP_BASE = 1.5               # 换章基准间隔(秒),随机性为 0 时不等待
AJAX_URL = "https://welearn.sflep.com/Ajax/SCO.aspx"


def _default_log(msg):
    print(msg)


class TimeTask:
    """刷学习时长任务控制器(CLI 与 GUI 共用)。所有输出经 on_log 回调,可在工作线程中调用。

    realtime=True(默认):时长按真实时间推进,章节串行执行,每章等待满目标秒数
    (单调时钟计时,不受请求耗时影响),期间每约 60 秒续期一次,总耗时约等于各章目标秒数之和。
    realtime=False:回到旧版节奏,所有章节并发请求并立即保存,不占用实际时长。

    jitter(随机性,0~100 %):每章实际时长 = 目标值 ± 该比例内随机,同时抖动换章间隔、
    请求间隔与续期时刻,避免每次都是整秒、整 60 秒的机械节奏。
    """

    def __init__(self, username: str, password: str,
                 on_log: Optional[Callable[[str], None]] = None,
                 stop_event: Optional[threading.Event] = None,
                 session: Optional[Any] = None):
        self.username = username
        self.password = password
        self.on_log = on_log or _default_log
        self.stop_event = stop_event or threading.Event()
        self.session = session or welearn_common.new_session()
        self.cid = ''
        self.uid = ''
        self.classid = ''
        self.units: List[Any] = []
        self.request_interval = REQUEST_INTERVAL
        self.jitter = JITTER_DEFAULT

    def _stopped(self) -> bool:
        return self.stop_event.is_set()

    def login(self) -> None:
        welearn_common.login(self.session, self.username, self.password,
                             on_log=self.on_log, stop_event=self.stop_event)

    def fetch_courses(self) -> List[dict]:
        return welearn_common.fetch_courses(self.session)

    def fetch_units(self, cid) -> List[dict]:
        self.cid = str(cid)
        # 与原实现一致:先请求 /2019/ 页面(响应弃用,保持请求序列),再请求解析 uid/classid 的页面
        self.session.get(
            'https://welearn.sflep.com/2019/student/course_info.aspx?cid=' + self.cid,
            headers={'Referer': 'https://welearn.sflep.com/2019/student/index.aspx'})
        self.uid, self.classid = welearn_common.get_course_ids(self.session, self.cid)
        self.units = welearn_common.fetch_units(self.session, self.cid, self.uid)
        return self.units

    def fetch_sections(self, unit_index: int) -> List[dict]:
        """只读获取指定单元(1-based)的小节列表,供界面展示"""
        return welearn_common.fetch_sections(self.session, self.cid, self.uid,
                                             unit_index - 1, self.classid)

    # ---- 随机性工具 ----

    @staticmethod
    def normalize_jitter(jitter) -> float:
        """把任意输入收拢到 0~100 的百分比"""
        try:
            value = float(jitter)
        except (TypeError, ValueError):
            return JITTER_DEFAULT
        if value != value:            # NaN
            return JITTER_DEFAULT
        return max(0.0, min(100.0, value))

    def _spread(self, base: float, jitter: float, ratio: float = 1.0) -> float:
        """在 base 上下浮动 ratio*jitter% 内随机取值;jitter=0 时原样返回"""
        if jitter <= 0 or base <= 0:
            return base
        span = base * ratio * jitter / 100.0
        return max(0.05, random.uniform(base - span, base + span))

    def _jittered(self, base: int, jitter: float) -> int:
        """单章目标秒数加随机偏移,最少 1 秒"""
        return max(1, int(round(self._spread(float(base), jitter))))

    def _chapter_gap(self, jitter: float) -> float:
        """换章之间的空档:随机性为 0 时无缝衔接,否则 0.7~2.3 秒上下浮动"""
        if jitter <= 0:
            return 0.0
        return self._spread(GAP_BASE, jitter)

    @staticmethod
    def _shuffle_order(pending: List[Tuple[int, int, dict]], shuffle: bool) -> bool:
        """按需原地打乱章节顺序;真正打乱返回 True。只有一章时无从打乱。"""
        if not shuffle or len(pending) < 2:
            return False
        random.shuffle(pending)
        return True

    @staticmethod
    def _keepalive_step(jitter: float) -> float:
        """续期间隔:随机性开启时在 60 秒上下浮动,不再卡整数 60"""
        if jitter <= 0:
            return float(KEEPALIVE_INTERVAL)
        return KEEPALIVE_INTERVAL * random.uniform(0.85, 1.15)

    def _generate_learning_time(self, target_time) -> int:
        if type(target_time) is int:
            return target_time
        return random.randint(target_time[0], target_time[1])

    def run(self, units, param, sections=None, jitter: float = 0.0,
            realtime: bool = True, shuffle: bool = False) -> Dict[str, Any]:
        """units: 0=全部单元, [n,...]=指定单元; sections: None=整单元,
        {单元编号: [小节编号]}=只刷勾选小节; jitter: 随机性百分比(0~100);
        realtime: True=按真实时间串行刷(每章占满目标秒数), False=并发立即刷完(旧版节奏);
        shuffle: True=把待刷章节的执行顺序随机打乱,不再每次从第一个单元第一节开始。

        默认按真实时间:章节逐个执行,每章占用它自己的目标秒数真实时间;
        开启随机性后每章时长各有偏移,时间线不会整整齐齐。
        """
        all_units, unit_list, section_map = welearn_common.normalize_targets(units, sections)
        target_time = param
        jitter = self.normalize_jitter(jitter)
        realtime = bool(realtime)
        shuffle = bool(shuffle)
        errors: List[str] = []

        indices = range(0, len(self.units)) if all_units else [u - 1 for u in unit_list]

        pending: List[Tuple[int, int, dict]] = []   # (基准秒数, 实际秒数, 章节)
        for unit in indices:
            if self._stopped():
                break
            response = self.session.get(
                f"https://welearn.sflep.com/ajax/StudyStat.aspx?action=scoLeaves&cid={self.cid}&uid={self.uid}&unitidx={str(unit)}&classid={self.classid}",
                headers={'Referer': 'https://welearn.sflep.com/2019/student/course_info.aspx?cid=' + self.cid})
            wanted = section_map.get(unit + 1) if section_map else None
            for no, chapter in enumerate(response.json()['info'], start=1):
                if wanted and no not in wanted:
                    continue   # 指定小节模式:只刷勾选的小节
                base = self._generate_learning_time(target_time)
                pending.append((base, self._jittered(base, jitter), chapter))

        if self._shuffle_order(pending, shuffle):
            self.on_log(f"已乱序:{len(pending)} 个章节的执行顺序随机打乱")

        total_learning_time = sum(actual for _, actual, _ in pending) if realtime else 0
        if not realtime:
            self.on_log("已关闭真实时间:并发刷完,不占用实际时长")

        async def runner():
            done = asyncio.Event()
            heartbeat = asyncio.ensure_future(
                self._heartbeat(total_learning_time, len(pending), done))
            try:
                if realtime:
                    for index, (base, actual, chapter) in enumerate(pending, start=1):
                        if self._stopped():
                            break
                        if index > 1:
                            gap = self._chapter_gap(jitter)
                            if gap >= 0.8:
                                self.on_log(f"换章间隔 {gap:.1f} 秒")
                            await asyncio.sleep(gap)
                            if self._stopped():
                                break
                        await self._simulate(index, len(pending), base, actual, jitter,
                                             chapter, errors)
                else:
                    await asyncio.gather(*(
                        self._fast_chapter(index, len(pending), chapter, jitter, errors)
                        for index, (_, _, chapter) in enumerate(pending, start=1)))
            finally:
                done.set()
                await heartbeat

        asyncio.run(runner())
        return {'total': len(pending), 'succeed': len(pending) - len(errors),
                'failed': len(errors), 'errors': list(errors),
                'seconds': total_learning_time, 'jitter': jitter, 'realtime': realtime,
                'shuffle': shuffle}

    async def _open_chapter(self, chapter: Dict, jitter: float,
                            errors: List[str]) -> Optional[Dict[str, Any]]:
        """请求 getscoinfo_v7(必要时重试一次),返回续期与保存所需的会话数据"""
        common_headers = {'Referer': 'https://welearn.sflep.com/student/StudyCourse.aspx'}
        common_data = {'uid': self.uid, 'cid': self.cid, 'scoid': chapter['id']}

        await asyncio.sleep(self._spread(self.request_interval, jitter))
        response = self.session.post(
            AJAX_URL, data={**common_data, 'action': 'getscoinfo_v7'}, headers=common_headers)

        if '学习数据不正确' in response.text:  # 重试
            await asyncio.sleep(self._spread(self.request_interval, jitter))
            self.session.post(
                AJAX_URL, data={**common_data, 'action': 'startsco160928'}, headers=common_headers)
            response = self.session.post(
                AJAX_URL, data={**common_data, 'action': 'getscoinfo_v7'}, headers=common_headers)
            if '学习数据不正确' in response.text:
                self.on_log(f"错误: {chapter['location']}")
                errors.append(chapter['location'])
                return None

        return_json = response.json()['comment']
        if 'cmi' in return_json:
            cmi = json.loads(return_json)['cmi']
            crate = cmi['score']['scaled']
            cstatus = cmi['completion_status']
            progress = cmi['progress_measure']
            total_time = cmi['total_time']
            session_time = cmi['session_time']
        else:
            crate = ''
            cstatus = 'not_attempted'
            progress = '0'
            total_time = '0'
            session_time = '0'

        await asyncio.sleep(self._spread(self.request_interval, jitter))
        self.session.post(
            AJAX_URL,
            data={**common_data, 'action': 'keepsco_with_getticket_with_updatecmitime',
                  'session_time': session_time, 'total_time': total_time},
            headers=common_headers)
        return {'headers': common_headers, 'data': common_data, 'crate': crate,
                'cstatus': cstatus, 'progress': progress,
                'total_time': total_time, 'session_time': session_time}

    def _save_chapter(self, opened: Dict[str, Any]) -> None:
        self.session.post(
            AJAX_URL,
            data={**opened['data'], 'action': 'savescoinfo160928',
                  'crate': opened['crate'], 'cstatus': opened['cstatus'],
                  'status': 'unknown', 'progress': opened['progress'],
                  'trycount': '0'},
            headers=opened['headers'])

    def _describe_chapter(self, index: int, total: int, base_time: int, learning_time: int,
                          jitter: float, chapter: Dict) -> None:
        self.on_log(f"[{index}/{total}] 章节 : {chapter['location']}")
        if jitter > 0:
            lo = max(1, int(round(base_time * (1 - jitter / 100.0))))
            hi = max(lo, int(round(base_time * (1 + jitter / 100.0))))
            self.on_log(f"已学 : {chapter['learntime']} 将学 : {learning_time} 秒"
                        f"(基准 {base_time},随机性 ±{jitter:.0f}% → {lo}~{hi} 秒)")
        else:
            self.on_log(f"已学 : {chapter['learntime']} 将学 : {learning_time} 秒(按真实时间推进)")

    async def _simulate(self, index: int, total: int, base_time: int, learning_time: int,
                        jitter: float, chapter: Dict, errors: List[str]) -> None:
        """按真实时间刷单章:占满目标秒数,期间每约 60 秒续期一次"""
        self._describe_chapter(index, total, base_time, learning_time, jitter, chapter)
        opened = await self._open_chapter(chapter, jitter, errors)
        if opened is None:
            return

        # 真实计时:单调时钟推进到目标秒数,期间每约 60 秒续期一次
        start = time.monotonic()
        deadline = start + learning_time
        next_keepalive = self._keepalive_step(jitter)
        while True:
            if self._stopped():
                self.on_log(f"任务已停止,跳过剩余时长: {chapter['location']}")
                return
            now = time.monotonic()
            if now >= deadline:
                break
            await asyncio.sleep(min(HEARTBEAT_INTERVAL, deadline - now))
            if time.monotonic() - start >= next_keepalive:
                next_keepalive += self._keepalive_step(jitter)
                self.session.post(
                    AJAX_URL,
                    data={**opened['data'], 'action': 'keepsco_with_getticket_with_updatecmitime',
                          'session_time': opened['session_time'],
                          'total_time': opened['total_time']},
                    headers=opened['headers'])

        await asyncio.sleep(self._spread(self.request_interval, jitter))
        self._save_chapter(opened)

    async def _fast_chapter(self, index: int, total: int, chapter: Dict,
                            jitter: float, errors: List[str]) -> None:
        """旧版节奏:请求完立即保存,不等真实时长(所有章节并发)"""
        self.on_log(f"[{index}/{total}] 章节 : {chapter['location']}")
        if self._stopped():
            return
        opened = await self._open_chapter(chapter, jitter, errors)
        if opened is None:
            return
        self._save_chapter(opened)

    async def _heartbeat(self, total_learning_time: int, chapters: int, done) -> None:
        """每 10 秒汇报一次真实进度;章节串行,所以总时长约等于各章目标秒数之和。"""
        start_time = time.monotonic()
        next_report = 10
        while not done.is_set():
            if self._stopped():
                return
            elapsed = time.monotonic() - start_time
            if elapsed >= next_report:
                self.on_log(f"预计学习时长 : {total_learning_time} 秒(章节累计,不含换章间隔)"
                            f"({chapters} 个章节,按顺序逐个刷)"
                            f" 已学习时长 : {int(elapsed)} 秒")
                next_report += 10
            await asyncio.sleep(HEARTBEAT_INTERVAL)


def welearn_time_run():
    user = input("请输入用户名=>")
    pwd = input("请输入密码=>")
    task = TimeTask(user, pwd)
    try:
        task.login()
    except (LoginError, WelearnError) as e:
        print(e)
        exit(0)
    while True:
        try:
            courses = task.fetch_courses()
        except WelearnError as e:
            input(str(e))
            exit(0)
        for index, course in enumerate(courses, start=1):
            print('[id:{:>2d}]  完成度 {:>3d}%  {}'.format(index, course['per'], course['name']))

        index = int(input('\n请输入需要刷时长的课程id(id为上方[]内的序号): '))
        units = task.fetch_units(courses[index - 1]['cid'])

        print("\n\n")
        print('[id: 0]  按顺序刷全部单元学习时长')
        for index, unit in enumerate(units, start=1):
            print(f"[id:{index:>2d}]  {unit['unitname']}  {unit['name']}")
        print("\n\n")
        unit_index = int(input('请选择要刷时长的单元id(id为上方[]内的序号,输入0为刷全部单元): '))

        print("\n\n")
        print(dedent('''\
            模式1:每个练习增加指定学习时长,请直接输入时间
            如:希望每个练习增加30秒,则输入 30

            模式2:每个练习增加随机时长,请输入时间上下限并用英文逗号隔开
            如:希望每个练习增加10~30秒,则输入 10,30

            说明:默认按真实时间刷,章节按顺序逐个进行,总耗时约等于各练习时长之和;
            关闭真实时间则并发立即刷完,不占用实际时长;
            乱序刷会打乱章节执行顺序(时长设置不变),避免每次都从头到尾一模一样。
        '''))
        print("\n\n")

        input_ = input("请严格按照以上格式输入: ")
        try:
            target_time = welearn_common.parse_numeric_param(input_)
        except ValueError:
            print("格式异常")
            exit(0)

        realtime_raw = input(
            "\n是否按真实时间刷?(Y=每章占满目标秒数并逐个进行, n=并发立即刷完)[Y]: ").strip().lower()
        realtime = realtime_raw not in ("n", "no", "off", "0", "false", "否")
        if realtime:
            jitter_raw = input(
                f"随机性百分比(0=完全规律,100=浮动一倍,直接回车={JITTER_DEFAULT:.0f}): ").strip()
            jitter = JITTER_DEFAULT if not jitter_raw else task.normalize_jitter(jitter_raw)
        else:
            jitter = 0.0

        shuffle_raw = input("是否乱序刷?(y=打乱章节执行顺序,直接回车=按单元/小节顺序)[N]: ").strip().lower()
        shuffle = shuffle_raw in ("y", "yes", "on", "1", "true", "是")

        result = task.run(unit_index, target_time, jitter=jitter,
                          realtime=realtime, shuffle=shuffle)

        print('运行结束!!\n错误:', result['failed'], '个')
        for index, error in enumerate(result['errors'], start=1):
            print(f'第{index}个错误:{error}')

        if unit_index == 0:
            break
        print("\n\n")
        print('回到选课处!!')
        print("\n\n")

    print("\n\n")
    print(dedent('''\
        **********  Created By Avenshy & SSmJaE  **********
                        Version : 0.3.0
           基于GPL3.0,完全开源,免费,禁止二次倒卖或商用
           https://www.github.com/Avenshy/WELearnToSleeep
                        仅供学习,勿滥用
        ***************************************************
    '''))
    input("Press any key to exit...")
