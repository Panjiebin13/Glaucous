"""Spec 存储层单测（v1.1-M5 任务 5.7，spec §七 1~5）。

覆盖：create/load/save_body/transition 合法与非法迁移、check_task 勾选
写回、tasks()/acceptance() 解析、list_all 倒序与损坏告警、active()、
frontmatter 损坏容错。
"""

from __future__ import annotations

import pytest

from glaucous.spec.store import SpecDoc, SpecStateError, SpecStore

BODY = """\
## 需求与边界
目标：修 add。

## 澄清记录
- 问：范围？答：仅 calc。

## 约束
- 不改目录结构

## 设计
改一行。

## 任务清单
- [ ] 修正 add 逻辑
- [ ] 补测试

## 验收标准
- add(1,2)==3（验证方式：单测）
- 测试全绿（验证方式：pytest）

## 风险与回退
- 无
"""


@pytest.fixture()
def store(tmp_path) -> SpecStore:
    return SpecStore(tmp_path)


class TestCreateLoad:
    def test_create_defaults_to_draft(self, store: SpecStore) -> None:
        doc = store.create("修复 add 函数", BODY)
        assert doc.status == "draft"
        assert doc.spec_id.startswith("spec-")
        assert doc.path.is_file()
        loaded = store.load(doc.spec_id)
        assert loaded.meta["name"] == "修复 add 函数"
        assert loaded.body.strip() == BODY.strip()

    def test_load_missing_raises(self, store: SpecStore) -> None:
        with pytest.raises(SpecStateError):
            store.load("no-such-spec")

    def test_frontmatter_tolerant(self, tmp_path) -> None:
        store = SpecStore(tmp_path)
        doc = store.create("t", BODY)
        # 损坏 frontmatter 行 + 缺 round：容错不抛
        raw = doc.path.read_text(encoding="utf-8")
        raw = raw.replace("round: 0", "round: 坏值\nbroken line no colon x")
        doc.path.write_text(raw, encoding="utf-8")
        loaded = store.load(doc.spec_id)
        assert loaded.meta["round"] == 0  # 错型归一化
        assert loaded.status == "draft"


class TestTransitions:
    def test_legal_chain(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        store.transition(doc, "reviewing")
        store.transition(doc, "approved", approved_at="2026-08-31T10:00:00")
        store.transition(doc, "executing", entry_checkpoint=7)
        store.transition(doc, "code_review")
        store.transition(doc, "verified")
        assert doc.status == "verified"
        assert doc.meta["approved_at"] == "2026-08-31T10:00:00"
        assert doc.meta["entry_checkpoint"] == 7

    def test_illegal_raises(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        with pytest.raises(SpecStateError):
            store.transition(doc, "executing")  # draft → executing 非法
        store.transition(doc, "reviewing")
        store.transition(doc, "archived")
        with pytest.raises(SpecStateError):
            store.transition(doc, "draft")  # 终态无出边

    def test_archive_from_any_active_state(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        store.transition(doc, "archived")
        assert doc.status == "archived"


class TestCheckTask:
    def test_check_second_task(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        store.check_task(doc, 2)
        tasks = doc.tasks()
        assert tasks[0][1] is False and tasks[1][1] is True
        # 落盘持久
        assert store.load(doc.spec_id).tasks()[1][1] is True

    def test_idempotent(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        store.check_task(doc, 1)
        store.check_task(doc, 1)
        assert [t[1] for t in doc.tasks()] == [True, False]

    def test_out_of_range(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        with pytest.raises(SpecStateError):
            store.check_task(doc, 99)


class TestParsing:
    def test_tasks_and_acceptance(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        assert [(no, done) for no, done, _ in doc.tasks()] == [(1, False), (2, False)]
        assert doc.acceptance() == [
            "add(1,2)==3（验证方式：单测）",
            "测试全绿（验证方式：pytest）",
        ]

    def test_checked_mix_parsed(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        store.check_task(doc, 1)
        assert [done for _no, done, _t in doc.tasks()] == [True, False]


class TestListing:
    def test_list_sorted_and_active(self, store: SpecStore) -> None:
        d1 = store.create("第一个", BODY)
        d2 = store.create("第二个", BODY)
        docs = store.list_all()
        assert [d.spec_id for d in docs][:2] == [d2.spec_id, d1.spec_id]
        assert store.active().spec_id == d2.spec_id
        store.transition(d2, "archived")
        assert store.active().spec_id == d1.spec_id

    def test_corrupt_file_skipped_with_warning(self, tmp_path) -> None:
        store = SpecStore(tmp_path)
        doc = store.create("好文档", BODY)
        (tmp_path / ".glaucous" / "specs" / "坏文档.md").write_text("", encoding="utf-8")
        docs = store.list_all()
        assert [d.spec_id for d in docs] == [doc.spec_id]
        assert any("坏文档" in w for w in store.warnings)

    def test_empty_dir(self, store: SpecStore) -> None:
        assert store.list_all() == []
        assert store.active() is None


class TestAppendNote:
    def test_append_into_risk_section(self, store: SpecStore) -> None:
        doc = store.create("t", BODY)
        store.append_note(doc, "任务 2 跳过：补测试（执行失败）")
        body = store.load(doc.spec_id).body
        assert "任务 2 跳过" in body
        assert body.index("## 风险与回退") < body.index("任务 2 跳过")
