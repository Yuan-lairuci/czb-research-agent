"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.graph import router as graph_router
from app.api.pdf_library import router as pdf_library_router
from app.api.qa import router as qa_router
from app.api.routes import router
from app.api.translate import router as translate_router
from app.api.writing import router as writing_router

app = FastAPI(title="科研助手 Agent", version="0.1.0")

# 开发期 CORS：放行 localhost / 127.0.0.1 任意端口
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(pdf_library_router)
app.include_router(qa_router)
app.include_router(translate_router)
app.include_router(writing_router)
app.include_router(graph_router)
