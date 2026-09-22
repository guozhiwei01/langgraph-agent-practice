# Email Agent

这是一个本地学习与联调用的邮件 Agent。当前接入：

- Gmail OAuth：只同步 `email-agent` 标签下的未读邮件。
- TypeSafe：通过 HTTP API 判断主要意图、紧急程度和处理复杂度。
- DeepSeek：根据检索结果生成回复草稿。
- 阿里云百炼 `text-embedding-v4`：生成 1024 维查询与知识块向量。
- PostgreSQL + pgvector：保存知识库、邮件、任务、审核和发送记录。
- GitHub Issues：仅在人工批准 bug 回复时，将脱敏摘要写入私有仓库。
- FastAPI：提供仅绑定 `127.0.0.1` 的本地人工审核页面。
- 审核页：使用 `.env` 中配置的手机号和密码登录，登录状态使用签名 HttpOnly Cookie 保存。
- PostgreSQL Job Queue：Web 只入队；独立 Worker 执行同步、Graph 推理、批准发送和拒绝。
- Outbox：发送前持久化稳定的幂等键与 RFC Message-ID，失败重试前先与 Gmail 已发送邮件对账。
- 可观测性：JSON 日志、审计事件、健康检查和 Prometheus 文本指标。

知识库中的 NimbusDesk 资料是虚构的演示数据，检索结果会明确标记 `DEMO / fictional policy`，不能当作真实产品政策。

## 决策架构

LangGraph 节点按业务阶段命名，底层决策模型通过统一接口调用：

```text
read_email
  -> triage_email          # 意图、风险、复杂度和人工升级
  -> search_documentation # pgvector 召回候选知识
  -> evaluate_evidence    # 相关性、答案支持、提示注入检查
  -> draft_response       # DeepSeek 只负责生成
  -> validate_response    # 完整性、依据、越权承诺和敏感信息检查
  -> human_review         # interrupt 暂停，当前阶段始终需要人工批准
     -> send_reply        # 批准：恢复同一 Graph，创建 Issue/发送 Gmail
     -> record_rejection  # 拒绝：恢复同一 Graph，记录审核结果
```

TypeSafe 不直接执行发送、退款或创建 Issue；它只提供决策信号，最终权限和副作用仍由 Python 规则与人工审核控制。无法归入已知意图的邮件会标记为 `other` 并直接进入人工分诊。

## 本地启动

确保 PostgreSQL 正在运行，且 `.env` 已配置 `TYPESAFE_API_KEY`、
`DEEPSEEK_API_KEY` 及所需集成凭据。审核登录还必须配置：

```dotenv
REVIEW_PHONE=13800000000
REVIEW_PASSWORD=change-this-password
SESSION_SECRET=replace-with-a-long-random-secret
```

手机号和密码只做精确匹配，不限制格式；登录会话有效期为 8 小时。首次初始化：

```bash
psql -U postgres -d postgres -f agents/email-agent/sql/000_create_database.sql
psql -U postgres -d email_agent -f agents/email-agent/sql/001_schema.sql
psql -U postgres -d email_agent -f agents/email-agent/sql/002_seed_knowledge.sql
psql -U postgres -d email_agent -f agents/email-agent/sql/003_add_decision_records.sql
psql -U postgres -d email_agent -f agents/email-agent/sql/004_production_runtime.sql
.venv/bin/python agents/email-agent/scripts/embed_knowledge.py
```

首次或权限变更后授权 Gmail：

```bash
.venv/bin/python agents/email-agent/scripts/authorize_gmail.py --force-consent
```

分别启动审核页与 Worker（两个终端或由进程管理器托管）：

```bash
.venv/bin/email-agent-review
.venv/bin/email-agent-worker
```

应用启动时会通过 `PostgresSaver.setup()` 自动创建或升级 LangGraph 的
`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 和迁移表；不需要手动把这些表
加入业务 schema。

打开：

```text
http://127.0.0.1:8080/
```

## 联调流程

1. 给测试邮件添加 Gmail 标签 `email-agent`，并保持为未读。
2. 在审核页点击 `Queue Gmail sync`，Web 立即写入持久化任务队列。
3. Worker 抢占任务，Agent 依次完成邮件分诊、知识召回、证据精排、草稿生成和草稿验证。
4. 草稿进入 `waiting_for_review`，不会自动发送。
5. 审核人编辑后点击 `Approve and send`，Web 将审核决定入队；Worker 使用同一个
   `langgraph_thread_id` 和 `Command(resume=...)` 恢复暂停的 Graph，再由
   `send_reply` 节点调用 Gmail。
6. 若分类为 `bug`，批准动作会先在私有 GitHub 仓库创建脱敏 Issue，并把 Issue 链接加入回复。
7. 点击 `Reject` 会记录拒绝结果，不发送邮件。

同步采用 Gmail message ID 去重。只有邮件成功入库并生成待审核草稿后，才会移除
`UNREAD` 标签。队列使用 `FOR UPDATE SKIP LOCKED` 支持多个 Worker，并通过运行中租约心跳
和周期性失联任务回收处理 Worker 崩溃。失败后按
5、10、20、40 秒等指数退避，最多 15 分钟；超过任务重试预算后进入 `dead`，审核页可手动重放。

Gmail 发送前会创建 Outbox 记录和稳定的 `Message-ID`。Worker 重放 `send_reply` 时会先查询
Gmail 已发送邮件；若此前已发送但本地未落库，则只补齐数据库状态，不再次发送。

## 运维接口

```text
GET /health/live   # Web 进程存活
GET /health/ready  # PostgreSQL 可用
GET /metrics       # 队列深度、任务状态、最老待处理任务年龄
```

Web 与 Worker 输出一行一个 JSON 的结构化日志，包含 `request_id`、`job_id`、`task_id`、
执行时长和重试次数；登录、审核决定、任务执行及人工重放会写入 `audit_events`。

## 安全边界

- Web 页面使用单个审核账号、8 小时签名会话、HttpOnly Cookie、SameSite 和 CSRF 防护，
  但仍默认只绑定 `127.0.0.1`；公网部署还需要 HTTPS、反向代理和集中身份系统。
- `REVIEW_PHONE` 和 `REVIEW_PASSWORD` 不做格式限制；`SESSION_SECRET` 应设置为随机长字符串，三者都不得提交到 Git。
- OAuth 凭据、Token 和 `.env` 不得提交到 Git。
- 邮件标题和正文会发送给 TypeSafe 完成分类，不应接入未经批准的敏感数据源。
- 当前使用 `PostgresSaver` 保存 LangGraph 检查点，可在应用重启后通过相同
  `langgraph_thread_id` 恢复 `interrupt()`；业务邮件和审核投影也保存在 PostgreSQL。
- 切换到 `PostgresSaver` 前已经进入 `waiting_for_review` 的旧任务没有持久化图检查点，
  不能恢复；本地测试数据需要清理对应旧任务和入站邮件后重新同步。当前数据库没有这类遗留任务。
- 当前只适合本地联调，不应自动回复真实客户。
- GitHub Issue 仅发送分类摘要，并对常见邮箱和密钥格式脱敏；不要把原始邮件正文或附件写入 Issue。
- Gmail API 不提供原生幂等键；当前 Outbox + RFC Message-ID 对账将重复窗口显著缩小，
  但极端情况下 Gmail 搜索索引尚未可见，仍不能数学上保证 exactly-once。
- 面向公网和正式 SLA 仍需集中身份认证、HTTPS、托管密钥、告警平台、备份演练、
  正式业务知识库和 Gmail 对账巡检。
