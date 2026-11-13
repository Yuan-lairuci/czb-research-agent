# 科研助手 Agent

> 2026 传智杯 AI 大模型创新应用挑战赛参赛作品 · 工程骨架
> 当前版本：0.1.0（骨架阶段）

## 项目简介

（占位）面向科研全流程的智能助手 Agent：围绕文献检索、论文解析、知识问答、综述生成、
图表辅助与写作润色六大方向，构建可扩展的 AI 大模型创新应用。具体产品定位待模块开发阶段细化。

## 技术架构

- **后端**：Python 3.11+ / FastAPI / ChromaDB（嵌入式向量库）/ PyMuPDF（PDF 解析）
- **前端**：React 18 + Vite + TypeScript；PDF 渲染预留 pdfjs-dist / react-pdf，图表预留 ECharts
- **LLM 接入**：仅走 OpenAI 兼容 HTTP 接口（`/chat/completions`，支持流式 SSE），配置全部来自环境变量
- **代码规范**：Ruff（后端）+ ESLint（前端）
- **部署**：Docker Compose（backend + frontend 两个服务）

```
浏览器 ──> frontend (React + Vite) ──> backend (FastAPI) ──> OpenAI 兼容 LLM 服务
                                        ├──> ChromaDB 向量库（RAG）
                                        └──> 本地文献库（PyMuPDF 解析）
```

## 快速开始

### 方式一：Docker Compose 一键启动

```bash
cp .env.example .env   # 按需填写真实配置（严禁提交 .env）
docker compose up --build
# 前端：http://localhost:5173
# 后端：http://localhost:8000
```

### 方式二：本地开发

后端：

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate    Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

前端（另开终端）：

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

健康检查验证：`curl http://localhost:8000/health` 应返回 `{"status":"ok","version":"0.1.0"}`。

## 环境变量

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `LLM_BASE_URL` | LLM 服务 Base URL（OpenAI 兼容网关） | `https://api.openai.com/v1` |
| `LLM_API_KEY` | LLM 服务 API Key（**严禁提交真实密钥**） | `your-api-key-here` |
| `LLM_MODEL` | 对话模型名称 | `your-model-name` |
| `EMBEDDING_MODEL` | 嵌入模型名称 | `your-embedding-model` |
| `EMBEDDING_BASE_URL` | 嵌入服务 Base URL（留空则回退 `LLM_BASE_URL`） | `https://api.openai.com/v1` |

配置读取入口：`backend/app/core/config.py`（全部从环境变量读取）；
`.env.example` 为变量模板，真实值写入 `.env`（已被 `.gitignore` 忽略）。

## 目录结构

```
czb_research_agent/
├── backend/                  # 后端（FastAPI）
│   ├── app/
│   │   ├── main.py           # 应用入口（含 CORS 配置）
│   │   ├── api/              # API 路由（/health 等）
│   │   ├── services/         # 业务服务（chat_service：OpenAI 兼容流式封装）
│   │   ├── models/           # 数据模型（Pydantic schemas）
│   │   └── core/             # 核心配置（config.py：环境变量读取）
│   ├── tests/                # 后端测试
│   ├── requirements.txt      # 运行时依赖
│   ├── requirements-dev.txt  # 开发/测试依赖
│   └── ruff.toml             # Ruff 配置
├── frontend/                 # 前端（React 18 + Vite + TS）
│   ├── src/
│   │   ├── App.tsx           # 首页（标题：科研助手 Agent）
│   │   ├── components/       # 组件（HealthStatus：调用 /health 显示状态）
│   │   └── integrations/     # 预留集成（PDF / ECharts）
│   ├── package.json
│   └── vite.config.ts
├── docker/                   # Dockerfile（backend / frontend）
├── docker-compose.yml        # 一键编排
├── .env.example              # 环境变量模板（无真实密钥）
└── README.md
```

## 六大功能模块（占位章节）

1. **文献智能检索**（占位）——多源学术检索与筛选，待细化接口设计。
2. **PDF 文献解析与精读**（占位）——PyMuPDF 解析 + 前端渲染（预留 pdfjs-dist / react-pdf）。
3. **知识库管理与 RAG 问答**（占位）——ChromaDB 向量库 + 检索增强问答。
4. **研究综述自动生成**（占位）——基于文献集合的综述/对比分析生成。
5. **数据图表辅助**（占位）——实验数据可视化（预留 ECharts）。
6. **论文写作润色助手**（占位）——中英文写作润色与格式规范。

> 注：模块名称为骨架占位，随开发推进可能调整；各模块并行开发，代码落位于
> `backend/app/services/` 与 `frontend/src/` 对应子目录。
