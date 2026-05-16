# AI 写作平台 — 后端

基于 Python 微服务架构的 AI 写作平台后端。四个 FastAPI 服务共享 PostgreSQL + Redis 基础设施，通过 Docker Compose 统一编排。

---

## 架构简介

```
ai-writing-platform-backend/
├── backend/
│   ├── api_gateway/        # 端口 8000 — 认证、配额、计费代理
│   ├── ai_inference/       # 端口 8001 — LLM 调用、批次缓存、人工审核队列
│   ├── knowledge_retrieval/# 端口 8002 — 向量嵌入、语义搜索
│   └── pipelines/          # 端口 8003 — 文档解析、工作流编排
├── infrastructure/
│   ├── docker-compose.yml  # 编排全部 7 个容器
│   ├── .env.example        # 密钥配置模板
│   └── init.sql            # PostgreSQL 表结构 + pgvector 初始化
└── tests/
    ├── unit/               # 各服务单元测试
    ├── integration/        # 网关路由集成测试
    └── performance/        # Locust 压力测试脚本
```

### 服务拓扑

```
浏览器 / 前端
        │
        ▼  HTTP :8000
┌───────────────┐
│   API 网关    │  JWT 认证 · 每日配额 · Stripe 计费
└───────┬───────┘
        │ 内部 HTTP
   ┌────┴──────────────────────────┐
   ▼                               ▼
┌──────────────┐        ┌──────────────────────┐
│  AI 推理服务  │        │    知识检索服务       │
│    :8001     │        │       :8002           │
│ DeepSeek LLM │        │ fastembed + pgvector  │
│ 批次缓存     │        │ 语义搜索              │
│ 人工审核队列 │        └──────────────────────┘
└──────────────┘
        │
        ▼
┌──────────────┐
│  流水线服务  │
│    :8003     │
│ PDF/DOCX 解析│
│ 工作流管理   │
└──────────────┘
        │
   ┌────┴──────────────────┐
   ▼                       ▼
PostgreSQL 16          Redis 7
+ pgvector             （3 个逻辑库）
```

### 服务说明

| 服务 | 端口 | 语言 | 职责 |
|------|------|------|------|
| `api_gateway` | 8000 | Python 3.12 / FastAPI | 统一入口：JWT 认证、每日配额管理（免费 10 次 / 基础版 100 次 / 专业版无限），Stripe Webhook 处理 |
| `ai_inference` | 8001 | Python 3.12 / FastAPI | DeepSeek LLM 推理、Prompt 批次缓存（Redis DB 0）、人工审核（HITL）队列、基于评分标准的自动评分 |
| `knowledge_retrieval` | 8002 | Python 3.12 / FastAPI | 文档嵌入（fastembed）、HNSW 向量索引（pgvector）、近似最近邻语义搜索 |
| `pipelines` | 8003 | Python 3.12 / FastAPI | PDF（pypdf）和 DOCX（python-docx）解析、工作流状态管理（Redis DB 1） |
| `postgres` | 5432 | PostgreSQL 16 | 用户、订阅、嵌入向量、流水线结果的关系型存储 |
| `redis` | 6379 | Redis 7 | 共享缓存 / 消息队列（DB 0：推理，DB 1：流水线，DB 2：网关） |

### 数据库表结构（init.sql）

| 表名 | 用途 |
|------|------|
| `users` | 用户身份与 JWT 凭证 |
| `subscriptions` | Stripe 订阅套餐跟踪（免费 / 基础版 / 专业版） |
| `document_embeddings` | 包含 HNSW 索引的向量列，支持高速近似搜索 |
| `pipeline_results` | 工作流输出与状态 |

---

## 本地开发

### 前置条件

- Docker Desktop 4.x+（含 Compose V2）
- Git

### 快速启动

```bash
# 1. 进入基础设施目录
cd infrastructure

# 2. 创建环境变量文件
cp .env.example .env

# 3. 填写 .env 中的必要密钥（见下方说明）

# 4. 构建镜像并启动所有服务
docker compose up --build
```

服务启动后访问地址：

| 服务 | 地址 |
|------|------|
| API 网关 | http://localhost:8000 |
| AI 推理服务 | http://localhost:8001 |
| 知识检索服务 | http://localhost:8002 |
| 流水线服务 | http://localhost:8003 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

每个 FastAPI 服务均在 `/docs` 路径提供交互式 Swagger API 文档。

### 初始化知识库

```bash
docker compose exec knowledge_retrieval \
  python scripts/seed_knowledge.py
```

### 停止与清理

```bash
docker compose down       # 停止容器，保留数据卷
docker compose down -v    # 同时删除 postgres_data 和 redis_data
```

---

## 切换至阿里云 RDS

本地 `postgres` 容器可直接替换为阿里云 RDS PostgreSQL 实例，无需修改任何业务代码——只需改动 `infrastructure/.env` 和 `docker-compose.yml`。

### 1. 准备 RDS 实例

- **版本**：PostgreSQL 14 或 16（与当前 `pgvector/pgvector:pg16` 镜像一致）。
- **pgvector 插件**：进入 **RDS 控制台 → 实例 → 插件管理**，搜索 `vector` 并安装。若插件缺失，`knowledge_retrieval` 服务将无法启动。
- **网络**：将服务器 IP 加入 RDS 白名单，或配置为 VPC 内网访问。
- **表结构初始化**：Docker 容器会自动挂载 `init.sql`，但 RDS 不会自动执行。需手动连接实例执行一次：

  ```bash
  psql "postgresql://<用户名>:<密码>@rm-xxxx.pg.rds.aliyuncs.com:5432/platform" \
    -f infrastructure/init.sql
  ```

  > **注意**：`init.sql` 会创建 HNSW 向量索引，要求 `pgvector ≥ 0.5.0`。执行前请在 RDS 控制台确认已安装的插件版本。

### 2. 配置 SSL 证书

阿里云 RDS 默认强制 SSL 加密连接。需下载 CA 证书并放置到 Docker 可挂载的路径。

1. 在 **RDS 控制台 → 实例 → 数据安全性 → SSL** 页面下载证书压缩包。
2. 解压后取 `.pem` 文件：
   ```
   ApsaraDB-CA-Chain.zip
   ├── ApsaraDB-CA-Chain.pem   ← 使用此文件
   ├── ApsaraDB-CA-Chain.jks
   └── ApsaraDB-CA-Chain.p7b
   ```
3. 将 `.pem` 文件复制到 `infrastructure/certs/` 目录：
   ```bash
   mkdir -p infrastructure/certs
   cp ApsaraDB-CA-Chain.pem infrastructure/certs/
   ```

> **安全提示**：`infrastructure/certs/` 已加入 `.gitignore`，请勿将证书文件提交至代码仓库。

### 3. 修改 `infrastructure/.env`

将 `POSTGRES_DSN` 设置为阿里云 RDS 地址，并启用 SSL 证书验证：

```env
POSTGRES_DSN=postgresql://<用户名>:<密码>@rm-xxxx.pg.rds.aliyuncs.com:5432/platform?sslmode=verify-ca&sslrootcert=/certs/ApsaraDB-CA-Chain.pem
```

其中 `/certs/` 为下一步挂载到各容器内的路径。

> 若连接时报 `certificate verify failed`，可先将 `sslmode=verify-ca` 改为 `sslmode=require`（仅加密，不验证证书链）确认连通性，再切回 `verify-ca`。

### 4. 修改 `docker-compose.yml`

按文件中已有的 `# 阿里云 RDS` 行内注释操作，共四类改动：

| 位置 | 操作 |
|------|------|
| 为 `api_gateway`、`ai_inference`、`knowledge_retrieval`、`pipelines` 添加 `volumes: - ./certs:/certs:ro` | 将证书目录只读挂载到各容器 |
| 各服务 `environment:` 中的硬编码 DSN | 替换为 `${POSTGRES_DSN}`（`ai_inference` 用 `DATABASE_URL=${POSTGRES_DSN}`） |
| 各服务 `depends_on:` 中的 `postgres` 条目 | 删除 |
| 顶层 `volumes: postgres_data:` 及整个 `postgres:` 服务块 | 全部删除 |

改完后执行 `docker compose up --build`，所有服务将指向 RDS 实例，本地 `postgres` 容器不再启动。

---

## 切换至 Railway Redis

本地 `redis` 容器可直接替换为 [Railway](https://railway.app) 托管的 Redis 实例，无需修改任何业务代码——只需改动 `infrastructure/.env` 和 `docker-compose.yml`。

### 1. 在 Railway 创建 Redis 服务

1. 打开 Railway 项目 → **New Service → Database → Redis**。
2. 部署完成后，进入该服务 → **Connect** 标签页 → 复制 **Redis URL**（格式：`redis://default:<密码>@<host>.railway.app:<端口>`）。

### 2. 修改 `infrastructure/.env`

取消注释并填写三条 `REDIS_*_URL`（各对应一个逻辑库）：

```env
REDIS_INFERENCE_URL=redis://default:<密码>@<host>.railway.app:<端口>/0
REDIS_PIPELINES_URL=redis://default:<密码>@<host>.railway.app:<端口>/1
REDIS_GATEWAY_URL=redis://default:<密码>@<host>.railway.app:<端口>/2
```

> **说明**：三条连接串的主机和密码完全相同，仅末尾的 `/0`、`/1`、`/2` 不同。Railway Redis 使用标准（非集群）模式，支持逻辑库 0–15。

### 3. 修改 `docker-compose.yml`

按文件中已有的 `# Railway Redis` 行内注释操作，共三类改动：

| 位置 | 操作 |
|------|------|
| 各服务 `environment:` 中的 `REDIS_URL=redis://redis:6379/X` | 替换为对应的 `${REDIS_*_URL}` 变量 |
| 各服务 `depends_on:` 中的 `- redis` 条目 | 删除 |
| 顶层 `volumes: redis_data:` 及整个 `redis:` 服务块 | 全部删除 |

改完后执行 `docker compose up --build`，所有服务将连接 Railway Redis，本地 `redis` 容器不再启动。

---

## 独立服务开发

在不启动完整 Docker Compose 的情况下，单独运行某个微服务——适合只修改某一服务时快速迭代。

### 仅启动基础设施

用 Docker 运行数据库，服务直接在宿主机上运行：

```bash
cd infrastructure
docker compose up postgres redis -d
```

PostgreSQL 宿主机端口为 **5458**，Redis 为 **6379**。

### 各服务启动步骤

将 `<service>` 替换为 `api_gateway`、`ai_inference`、`knowledge_retrieval` 或 `pipelines`：

```bash
cd backend/<service>

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

按下方说明设置环境变量后，启动服务：

```bash
uvicorn main:app --reload --port <端口号>
```

### 各服务环境变量

**`api_gateway`** — 端口 8000
```bash
JWT_SECRET=dev-secret-change-in-prod
POSTGRES_DSN=postgresql://platform:platform@localhost:5458/platform
REDIS_URL=redis://localhost:6379/2
CORS_ORIGINS=http://localhost:5173
AI_INFERENCE_URL=http://localhost:8001
KNOWLEDGE_RETRIEVAL_URL=http://localhost:8002
PIPELINES_URL=http://localhost:8003
```

**`ai_inference`** — 端口 8001
```bash
DEEPSEEK_API_KEY=<你的密钥>
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://platform:platform@localhost:5458/platform
```

**`knowledge_retrieval`** — 端口 8002
```bash
POSTGRES_DSN=postgresql://platform:platform@localhost:5458/platform
```

**`pipelines`** — 端口 8003
```bash
REDIS_URL=redis://localhost:6379/1
POSTGRES_DSN=postgresql://platform:platform@localhost:5458/platform
AI_INFERENCE_URL=http://localhost:8001
KNOWLEDGE_RETRIEVAL_URL=http://localhost:8002
```

### 启动顺序

在本地同时运行所有服务时，需遵守如下依赖顺序：

1. `postgres` + `redis` — 基础设施，无依赖
2. `ai_inference`（8001）和 `knowledge_retrieval`（8002）— 相互独立，可并行启动
3. `pipelines`（8003）— 依赖 `ai_inference` 和 `knowledge_retrieval`
4. `api_gateway`（8000）— 依赖以上全部三个服务
5. 前端开发服务器 — 依赖 `api_gateway`

---

## 环境变量

将 `infrastructure/` 目录下的 `.env.example` 复制为 `.env` 并填写以下内容：

| 变量名 | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek LLM API 密钥 |
| `POSTGRES_USER` | PostgreSQL 用户名（默认：`platform`） |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 |
| `POSTGRES_DB` | 数据库名称（默认：`platform`） |
| `REDIS_URL` | Redis 连接字符串（默认：`redis://redis:6379/0`） |
| `STRIPE_SECRET_KEY` | Stripe 密钥（`sk_live_…` 或 `sk_test_…`） |
| `STRIPE_WEBHOOK_SECRET` | Stripe Webhook 签名密钥（`whsec_…`） |
| `STRIPE_PRICE_BASIC` | 基础套餐对应的 Stripe Price ID |
| `STRIPE_PRICE_PRO` | 专业套餐对应的 Stripe Price ID |

---

## 运行测试

测试文件位于顶级 `tests/` 目录，使用 **pytest** 运行。

```bash
# 运行所有单元测试
pytest tests/unit/

# 运行指定服务的测试
pytest tests/unit/test_api_gateway.py

# 运行集成测试（需先启动 Docker Compose 全栈）
pytest tests/integration/

# 运行压力测试（需安装 Locust）
locust -f tests/performance/locustfile.py --host http://localhost:8000
```

在 CI 环境中，完整测试套件通过 GitHub Actions 在每次推送到 `main` 或 `develop` 分支时自动触发。

---

## CI/CD

`.github/workflows/ci.yml` 工作流包含四个作业：

| 作业名 | 触发条件 | 执行步骤 |
|--------|----------|----------|
| `test-backend` | 所有推送 | 对全部 4 个服务运行 pytest 单元测试 |
| `build-frontend` | 所有推送 | ESLint 检查 + Vite 生产构建 |
| `build-docker` | 所有推送 | 对所有服务执行 Docker 镜像构建冒烟测试 |
| `integration-tests` | 仅推送至 `main` | 完整 `docker compose up` + 集成测试套件 |

---

## 各服务目录结构

每个服务遵循统一的目录规范：

```
<服务名>/
├── main.py            # FastAPI 应用工厂 + 中间件配置
├── requirements.txt   # 固定版本依赖
├── Dockerfile         # python:3.12-slim，暴露对应端口
├── routers/           # APIRouter 模块（每个业务域一个文件）
├── core/              # 核心业务逻辑（仅 ai_inference）
├── db/                # ORM 模型与数据库会话管理
└── scripts/           # 一次性管理脚本（仅 knowledge_retrieval）
```
