"""日志区简化的界面钩子:限高、收起、行数上限、相邻重复合并。

这些是纯前端行为,后端只负责把页面吐出来,所以断言落在 INDEX_HTML 上:
只要页面里的选择器/常量/函数名对不上,前端脚本就会静默失效。
"""

import web_gui


def _page() -> str:
    app, _state = web_gui.create_app()
    return app.test_client().get("/").get_data(as_text=True)


def test_log_header_has_count_and_toggle():
    body = _page()
    assert 'id="logCount"' in body
    assert 'id="logToggle"' in body
    assert 'id="clearBtn"' in body
    assert ">收起<" in body


def test_console_is_height_capped_and_collapsible():
    body = _page()
    # 限高:日志再多也不会把页面拉长
    assert "max-height: calc(100vh - 250px)" in body
    # flex 子项必须能收缩,否则内容会撑破父级限高
    assert "#log { flex: 1; min-height: 0;" in body
    assert ".console.collapsed" in body
    assert ".console.collapsed #log { display: none; }" in body


def test_log_lines_are_capped_and_duplicates_merged():
    body = _page()
    assert "var LOG_MAX = 600;" in body
    assert "function trimLog(" in body
    assert "function pushLogLine(" in body
    assert "function updateLogCount(" in body
    assert "'  ×' + dupN" in body


def test_clear_resets_merge_state():
    body = _page()
    assert "function resetLog(" in body
    assert "$('clearBtn').addEventListener('click', resetLog);" in body


def test_toggle_flips_collapsed_class():
    body = _page()
    assert "panel.classList.toggle('collapsed');" in body
    assert "collapsed ? '展开' : '收起'" in body
