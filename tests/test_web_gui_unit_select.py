"""单元勾选 = 自动全选其小节 + 「全选小节/取消全选」按钮。

纯前端逻辑,断言落在页面脚本的钩子上:选择器、函数名、自动全选那一行、
以及"恢复勾选时不自动全选"的参数。任何一处被改坏,单元与小节就会不一致。
"""

import web_gui


def _page() -> str:
    app, _state = web_gui.create_app()
    return app.test_client().get("/").get_data(as_text=True)


def test_unit_row_has_select_all_button():
    body = _page()
    assert "secAllBtn.className = 'sec-all-btn'" in body
    assert "secAllBtn.textContent = '全选小节'" in body
    # 按钮在 label 内部,必须掐掉默认动作,否则点按钮会连单元勾选一起翻面
    assert 'ev.preventDefault();' in body
    assert 'ev.stopPropagation();' in body


def test_helpers_exist():
    body = _page()
    for fn in ('function unitSecs(', 'function syncUnitCb(', 'function syncSecAllLabel(',
               'function setUnitSecs(', 'function onSecToggled('):
        assert fn in body, fn


def test_unit_check_autoselects_sections():
    body = _page()
    assert 'function onUnitToggled(cb, autoSelect)' in body
    assert "if (auto) unitSecs(no).forEach(function (sc) { sc.checked = true; });" in body


def test_restore_keeps_partial_selection():
    body = _page()
    # 有小节记录:严格按记录恢复,不得自动全选覆盖用户原来的部分选择
    assert 'sc.checked = list.indexOf(Number(sc.dataset.sec)) >= 0;' in body
    # 无小节记录的旧数据按整单元处理
    assert '无小节记录 = 整单元,自动全选' in body
    assert 'onUnitToggled(cb, true);' in body


def test_uncheck_unit_clears_sections():
    body = _page()
    assert "unitSecs(no).forEach(function (sc) { sc.checked = false; });" in body
    assert "btn.textContent = allOn ? '取消全选' : '全选小节';" in body


def test_tip_text_matches_new_semantics():
    body = _page()
    assert '勾选单元会自动全选它下面的小节' in body
    assert '一个都不勾 = 不刷这个单元' in body


def test_sections_wired_to_parent_sync():
    body = _page()
    assert "cb.addEventListener('change', function () { onSecToggled(this); });" in body
    assert "document.querySelectorAll('.sec-all-btn').forEach(function (b) { b.disabled = on; });" in body
