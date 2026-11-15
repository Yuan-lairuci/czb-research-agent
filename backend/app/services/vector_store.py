"""向量存储：接口 + 纯 Python/Numpy 实现（默认）+ 可选 Chroma 适配。

默认 NumpyVectorStore：numpy 余弦检索 + JSON/npz 持久化到 backend/data/index/。
Chroma 适配仅在 chromadb 可导入时启用（可选依赖，本地装不上不算失败）。
"""
from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np

try:
    import chromadb
except ImportError:  # pragma: no cover - chromadb 为可选依赖
    chromadb = None

META_FILE = "index.json"
VECTORS_FILE = "index.npz"


class VectorStoreError(RuntimeError):
    """向量存储读写失败。"""


def default_index_dir() -> Path:
    """索引目录：env QA_INDEX_DIR 优先，否则 <backend>/data/index。"""
    env = os.environ.get("QA_INDEX_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "index"


class VectorStore(ABC):
    """向量存储接口。"""

    @abstractmethod
    def add(self, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        """写入 chunks 及其向量。"""

    @abstractmethod
    def query(
        self, vector: list[float], top_k: int = 5, paper_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """余弦检索，返回 [{"chunk": dict, "score": float}]，分数降序；paper_ids 过滤。"""

    @abstractmethod
    def remove_paper(self, paper_id: str) -> None:
        """删除某论文的全部块（重索引时替换）。"""

    @abstractmethod
    def papers(self) -> dict[str, int]:
        """已索引论文 → chunk 数。"""

    @abstractmethod
    def save(self) -> None:
        """持久化。"""


class NumpyVectorStore(VectorStore):
    """纯 Python + numpy 余弦检索；JSON 元数据 + npz 向量持久化。"""

    def __init__(self, index_dir: str | Path | None = None) -> None:
        self.index_dir = Path(index_dir) if index_dir else default_index_dir()
        self._chunks: list[dict[str, Any]] = []
        self._matrix: np.ndarray | None = None
        self._lock = threading.RLock()
        self._load()

    def add(self, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise VectorStoreError(f"chunks({len(chunks)}) 与 vectors({len(vectors)}) 数量不一致")
        if not chunks:
            return
        arr = np.asarray(vectors, dtype=np.float64)
        if arr.ndim != 2:
            raise VectorStoreError("vectors 必须是二维向量列表")
        if self._matrix is not None and self._matrix.shape[1] != arr.shape[1]:
            raise VectorStoreError(
                f"向量维度不一致: 已有 {self._matrix.shape[1]}，新传入 {arr.shape[1]}"
            )
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        with self._lock:
            self._chunks.extend(dict(c) for c in chunks)
            self._matrix = arr if self._matrix is None else np.vstack([self._matrix, arr])

    def query(
        self, vector: list[float], top_k: int = 5, paper_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if self._matrix is None or not self._chunks or top_k <= 0:
            return []
        q = np.asarray(vector, dtype=np.float64)
        if q.ndim != 1 or q.shape[0] != self._matrix.shape[1]:
            raise VectorStoreError("查询向量维度与索引不一致")
        norm = float(np.linalg.norm(q))
        if norm == 0:
            return []
        scores = self._matrix @ (q / norm)
        if paper_ids:
            wanted = set(paper_ids)
            idxs = [i for i, c in enumerate(self._chunks) if c["paper_id"] in wanted]
        else:
            idxs = list(range(len(self._chunks)))
        ranked = sorted(idxs, key=lambda i: (-float(scores[i]), str(self._chunks[i]["chunk_id"])))
        return [
            {"chunk": self._chunks[i], "score": float(scores[i])} for i in ranked[:top_k]
        ]

    def remove_paper(self, paper_id: str) -> None:
        keep = [i for i, c in enumerate(self._chunks) if c["paper_id"] != paper_id]
        if len(keep) == len(self._chunks):
            return
        with self._lock:
            self._chunks = [self._chunks[i] for i in keep]
            self._matrix = self._matrix[keep] if (keep and self._matrix is not None) else None

    def papers(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self._chunks:
            counts[c["paper_id"]] = counts.get(c["paper_id"], 0) + 1
        return counts

    def save(self) -> None:
        with self._lock:
            if self._matrix is not None and self._matrix.shape[0] != len(self._chunks):
                raise VectorStoreError(
                    f"chunks({len(self._chunks)}) 与 vectors({self._matrix.shape[0]}) 数量不一致，拒绝写盘"
                )
            self.index_dir.mkdir(parents=True, exist_ok=True)
            meta_path = self.index_dir / META_FILE
            vec_path = self.index_dir / VECTORS_FILE
            # 唯一临时名：并发保存互不踩踏（旧版固定名 index.json.tmp 是竞态根源）
            token = f"{os.getpid()}-{threading.get_ident()}"
            meta_tmp = self.index_dir / f"index_{token}.json.tmp"
            # numpy 会给不以 .npz 结尾的文件名自动追加 .npz，临时名必须以 .npz 结尾
            vec_tmp = self.index_dir / f"index_vectors_{token}.npz"
            try:
                meta_tmp.write_text(
                    json.dumps({"chunks": self._chunks}, ensure_ascii=False), encoding="utf-8"
                )
                vectors = (
                    self._matrix if self._matrix is not None else np.empty((0, 0), dtype=np.float64)
                )
                np.savez_compressed(vec_tmp, vectors=vectors)
                os.replace(meta_tmp, meta_path)
                os.replace(vec_tmp, vec_path)
            finally:
                meta_tmp.unlink(missing_ok=True)
                vec_tmp.unlink(missing_ok=True)

    def _load(self) -> None:
        meta_path = self.index_dir / META_FILE
        vec_path = self.index_dir / VECTORS_FILE
        if not meta_path.is_file() and not vec_path.is_file():
            return
        if not meta_path.is_file() or not vec_path.is_file():
            raise VectorStoreError(f"索引文件不完整: {self.index_dir}")
        chunks = json.loads(meta_path.read_text(encoding="utf-8"))["chunks"]
        vectors = np.load(vec_path)["vectors"]
        if len(chunks) != len(vectors):
            raise VectorStoreError(
                f"索引损坏: chunks({len(chunks)}) 与 vectors({len(vectors)}) 数量不一致"
            )
        self._chunks = chunks
        self._matrix = vectors if len(chunks) else None


class ChromaVectorStore(VectorStore):
    """可选 Chroma 适配：仅当 chromadb 可导入时可用。"""

    def __init__(
        self, collection_name: str = "qa_chunks", persist_dir: str | Path | None = None
    ) -> None:
        if chromadb is None:
            raise VectorStoreError("chromadb 未安装；默认请用 NumpyVectorStore")
        path = Path(persist_dir) if persist_dir else default_index_dir() / "chroma"
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add(self, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise VectorStoreError(f"chunks({len(chunks)}) 与 vectors({len(vectors)}) 数量不一致")
        if not chunks:
            return
        self._collection.upsert(
            ids=[c["chunk_id"] for c in chunks],
            embeddings=[list(map(float, v)) for v in vectors],
            documents=[c["text"] for c in chunks],
            metadatas=[
                {
                    "paper_id": c["paper_id"],
                    "page_no": int(c["page_no"]),
                    "section": c.get("section", ""),
                }
                for c in chunks
            ],
        )

    def query(
        self, vector: list[float], top_k: int = 5, paper_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        where = {"paper_id": {"$in": list(paper_ids)}} if paper_ids else None
        res = self._collection.query(
            query_embeddings=[list(map(float, vector))],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        out: list[dict[str, Any]] = []
        for i, chunk_id in enumerate((res.get("ids") or [[]])[0]):
            meta = res["metadatas"][0][i]
            doc = res["documents"][0][i]
            dist = res["distances"][0][i]
            chunk: dict[str, Any] = {
                "chunk_id": chunk_id,
                "paper_id": meta["paper_id"],
                "page_no": meta["page_no"],
                "text": doc,
            }
            if meta.get("section"):
                chunk["section"] = meta["section"]
            out.append({"chunk": chunk, "score": float(1.0 / (1.0 + dist))})
        return out

    def remove_paper(self, paper_id: str) -> None:
        self._collection.delete(where={"paper_id": paper_id})

    def papers(self) -> dict[str, int]:
        got = self._collection.get(include=["metadatas"])
        counts: dict[str, int] = {}
        for meta in got.get("metadatas") or []:
            pid = meta["paper_id"]
            counts[pid] = counts.get(pid, 0) + 1
        return counts

    def save(self) -> None:
        """Chroma PersistentClient 自动持久化，此处无需额外动作。"""
