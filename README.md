# 科研助手 Agent（CiteScout）

> 2026 传智杯「AI 大模型创新应用挑战赛」参赛作品 · 智领科研·意图驱动
> 当前版本：1.0.0

面向科研全流程的智能助手 Agent：PDF 文献库与细粒度解析、抗幻觉 RAG 问答（多轮对话 + 出处溯源）、
版式感知阅读与中英双向对照翻译、文献检索追踪推荐、引用图谱挖掘、写作 Copilot —— 六大模块一体。

## 功能总览

| 模块 | 能力 |
| --- | --- |
| 文献库 | PDF 上传解析（标题/页块/参考文献）、OCR 扫描件、语义去重与版本管理（LLM 版本差异总结） |
| 阅读器 + 翻译 | pdf.js 版式渲染、逐页/整篇中英双向翻译、双栏对照、放大全屏、引用标记跳转 |
| AI 问答 | RAG 检索增强问答、Token 级流式输出（SSE）、多轮对话记忆、逐条出处溯源（页码+原文引文+置信度）、导出 Markdown |
| 检索推荐 | 多源文献检索、引用含金量筛选、一键入库、arXiv 追踪、GitHub 代码推荐 |
| 引用图谱 | PageRank/中心性挖掘基石节点、引用关系图谱、Future Work 去重归纳 |
| 写作 Copilot | 语言润色、TikZ/Graphviz/Mermaid/Matplotlib 图表生成、CSV→图表、Figure Caption 生成 |

## 架构

```
浏览器 ──> frontend (React 18 + Vite + TS + antd + pdf.js)
              │  fetch /api/*（同源或 VITE_API_BASE）
              ▼
          backend (FastAPI) ──> OpenAI 兼容 LLM（DeepSeek / GPT 等，SSE 流式）
              ├──> 向量检索（OpenAI 兼容 Embedding，支持 Ollama bge-m3；未配置自动回退内置 Hash 嵌入）
              ├──> 文献库（PyMuPDF 解析 + SQLite 元数据 + 本地文件存储）
              └──> 翻译/图谱/推荐/写作 各服务
```

## 快速开始

### 方式一：Docker Compose 一键启动（推荐，评分复现路径）

```bash
cp .env.example backend/.env      # 填入 LLM 密钥（严禁提交 .env）
docker compose up --build
# 打开 http://localhost:8080
```

- 需要 Docker Desktop / Docker Engine + Compose v2。
- **国内网络加速**（镜像拉不动时）：

```bash
REGISTRY=docker.1ms.run/ \
PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
NPM_REGISTRY=https://registry.npmmirror.com \
docker compose up --build
```

- 未配置 EMBEDDING_MODEL 时后端自动使用内置 Hash 嵌入，**零外部依赖即可完整运行**；
  配置 bge-m3（Ollama）可获得最佳中英文检索效果，容器内已映射宿主 host.docker.internal:11434。
- 数据持久化：backend/data/ 挂载进容器，上传的文献与索引保留在宿主机。

### 方式二：本地开发

后端：

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate    Linux/macOS: source venv/bin/activate
pip install -r requirements.txt -r requirements-qa.txt -r requirements-pdf.txt
uvicorn app.main:app --reload --port 8000
```

前端（另开终端）：

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

健康检查：curl http://localhost:8000/health 应返回 {"status":"ok","version":"0.1.0"}。

## 环境变量（backend/.env）

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| LLM_BASE_URL | LLM 服务 Base URL（OpenAI 兼容网关） | https://api.deepseek.com/v1 |
| LLM_API_KEY | LLM 服务 API Key（**严禁提交真实密钥**） | sk-... |
| LLM_MODEL | 对话模型名称 | deepseek-chat |
| EMBEDDING_MODEL | 嵌入模型名；**留空回退内置 Hash 嵌入** | bge-m3 |
| EMBEDDING_BASE_URL | 嵌入服务 Base URL（本地 Ollama 为 http://127.0.0.1:11434/v1） | http://127.0.0.1:11434/v1 |
| EMBEDDING_API_KEY | 嵌入服务 API Key（如需） | 可留空 |

配置读取入口：backend/app/core/config.py（环境变量优先，其次 backend/.env 与仓库根 .env）；
.env.example 为变量模板。**Docker 下向量服务地址固定为 host.docker.internal:11434**（compose environment 覆盖）。

## 目录结构

```
czb_research_agent/
├── backend/
│   ├── app/
│   │   ├── main.py           # 应用入口（CORS、路由挂载）
│   │   ├── api/              # pdf_library / qa / translate / writing / graph / recommend
│   │   ├── services/         # rag_qa / translate_llm / pdf_dedup / graph_build / recommend / writing / chat_service / embedding / ocr
│   │   ├── models/           # Pydantic 契约（ParsedPaper / QA / 翻译）
│   │   └── core/config.py    # 环境配置
│   ├── data/                 # 文献库数据（解析结果/索引/翻译缓存，已 gitignore）
│   ├── tests/                # pytest 测试
│   └── requirements*.txt     # 运行依赖（分模块）
├── frontend/
│   ├── src/
│   │   ├── App.tsx           # 六视图切换（文献库/阅读器+翻译/AI问答/检索推荐/引用图谱/写作）
│   │   ├── components/       # LibraryPanel / ReaderPanel / TranslationPanel / QaPanel / ...
│   │   ├── pdf.ts            # pdf.js 封装（渲染/文本层）
│   │   └── api.ts            # 后端 API 客户端（含 SSE 流式问答）
│   └── vite.config.ts
├── docker/                   # backend.Dockerfile / frontend.Dockerfile / nginx.conf
├── docker-compose.yml        # 一键编排（:8080）
├── .env.example              # 环境变量模板（无真实密钥）
└── README.md
```

## 测试

```bash
cd backend && python -m pytest tests/ -q
```

核心契约测试：RAG 问答溯源、翻译引擎（空块跳 LLM/缓存/补译）、PDF 语义去重、版本差异。