# -*- coding: utf-8 -*-
"""演示用启动垫片（仅前端仓内，后端零改动）。

backend/app/main.py 尚未注册 recommend_router（模块四 docstring 明示
「main.py 注册（由调度员集成）」）。此垫片在 app.main:app 之上补挂
模块四路由后启动 uvicorn；调度员完成 main.py 集成后可删除本文件并改用
标准启动命令：python -m uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.main import app  # noqa: E402

already = any(getattr(r, 'path', '').startswith('/api/recommend') for r in app.routes)
if not already:
    from app.api.recommend import router as recommend_router
    app.include_router(recommend_router)

import uvicorn  # noqa: E402

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8000)
