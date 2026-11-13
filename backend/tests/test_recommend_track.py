"""模块四测试：arXiv 版本解析与追踪（v_n -> v_n+1 检测）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.arxiv_client import parse_version
from app.services.track import TrackService, TrackStore


class StubArxiv:
    """可编排的 arXiv 桩：按调用序号返回不同版本。"""

    def __init__(self, metas):
        self.metas = list(metas)
        self.calls = []

    def get_paper(self, arxiv_id):
        self.calls.append(arxiv_id)
        if len(self.metas) > 1:
            return self.metas.pop(0)
        return self.metas[0]


def test_parse_version_modern_and_old():
    """新旧式 arXiv id 均可解析出 base 与版本号。"""
    assert parse_version("1706.03762v7") == ("1706.03762", 7)
    assert parse_version("1706.03762") == ("1706.03762", None)
    assert parse_version("2301.00001v1") == ("2301.00001", 1)
    base, ver = parse_version("cs/0112017v2")
    assert base == "cs/0112017" and ver == 2


def test_track_add_creates_item(tmp_path):
    """添加追踪：记录 base_id 与基线版本，列表可读回。"""
    store = TrackStore(tmp_path / "track.db")
    arxiv = StubArxiv([
        {"arxiv_id": "1706.03762v1", "version": 1, "title": "Attention Is All You Need"},
    ])
    svc = TrackService(store, arxiv=arxiv)

    item = svc.add("1706.03762v1")
    assert item.base_id == "1706.03762"
    assert item.tracked_version == 1
    # 带版本号添加不联网，title 由首次 run() 补全
    assert item.title is None
    svc.run()
    assert svc.list_items()[0].title == "Attention Is All You Need"

    listed = svc.list_items()
    assert len(listed) == 1
    assert listed[0].arxiv_id == item.arxiv_id


def test_track_add_idempotent(tmp_path):
    """同一论文重复添加不产生第二条记录。"""
    store = TrackStore(tmp_path / "track.db")
    svc = TrackService(store, arxiv=StubArxiv([{"arxiv_id": "1706.03762v1", "title": "T"}]))
    first = svc.add("1706.03762v1")
    second = svc.add("1706.03762v2")
    assert first.arxiv_id == second.arxiv_id
    assert len(svc.list_items()) == 1


def test_track_run_detects_new_version(tmp_path):
    """run() 比对 v_n -> v_n+1 并推进 tracked_version（ack 语义）。"""
    store = TrackStore(tmp_path / "track.db")
    arxiv = StubArxiv([
        {"arxiv_id": "1706.03762v3", "title": "Attention Is All You Need"},  # 第一次 run
        {"arxiv_id": "1706.03762v3", "title": "Attention Is All You Need"},  # 第二次 run
    ])
    svc = TrackService(store, arxiv=arxiv)
    svc.add("1706.03762v1")

    first = svc.run()
    assert first.checked == 1
    assert len(first.updates) == 1
    upd = first.updates[0]
    assert (upd.old_version, upd.new_version) == (1, 3)
    assert upd.base_id == "1706.03762"

    second = svc.run()
    assert second.checked == 1
    assert second.updates == []  # 已确认过的版本不再重复报告


def test_track_run_no_update_when_same_version(tmp_path):
    """版本未变：无更新条目，只刷新 last_checked。"""
    store = TrackStore(tmp_path / "track.db")
    arxiv = StubArxiv([{"arxiv_id": "2301.00001v2", "title": "T"}])
    svc = TrackService(store, arxiv=arxiv)
    svc.add("2301.00001v2")

    resp = svc.run()
    assert resp.checked == 1
    assert resp.updates == []
    assert resp.errors == []


def test_track_add_bare_id_uses_online_baseline(tmp_path):
    """裸 arXiv id：add 时联网取基线版本；run 不报升级。"""
    store = TrackStore(tmp_path / "track.db")
    arxiv = StubArxiv([
        {"arxiv_id": "1706.03762v4", "version": 4, "title": "Attention Is All You Need"},
        {"arxiv_id": "1706.03762v4", "version": 4, "title": "Attention Is All You Need"},
    ])
    svc = TrackService(store, arxiv=arxiv)

    item = svc.add("1706.03762")
    assert item.tracked_version == 4
    resp = svc.run()
    assert resp.updates == []


def test_track_run_collects_errors(tmp_path):
    """取元数据失败：记录到 errors，不影响其它条目。"""
    class FailingArxiv:
        def get_paper(self, arxiv_id):
            from app.services.arxiv_client import ArxivError

            raise ArxivError("boom")

    store = TrackStore(tmp_path / "track.db")
    svc = TrackService(store, arxiv=FailingArxiv())
    svc.add("1706.03762v1")  # 带版本添加不联网

    resp = svc.run()
    assert resp.checked == 1
    assert len(resp.errors) == 1
    assert "1706.03762" in resp.errors[0]
