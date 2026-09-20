# Email Agent

这是一个本地学习与联调用的邮件 Agent。当前接入：

- Gmail OAuth：只同步 `email-agent` 标签下的未读邮件。
- DeepSeek：分类邮件并生成回复草稿。
- 阿里云百炼 `text-embedding-v4`：生成 1024 维查询与知识块向量。
- PostgreSQL + pgvector：保存知识库、邮件、任务、审核和发送记录。
- GitHub Issues：仅在人工批准 bug 回复时，将脱敏摘要写入私有仓库。
- FastAPI：提供仅绑定 `127.0.0.1` 的本地人工审核页面。

知识库中的 NimbusDesk 资料是虚构的演示数据，检索结果会明确标记 `DEMO / fictional policy`，不能当作真实产品政策。

## 本地启动

确保 PostgreSQL 正在运行，且 `.env` 已配置。首次初始化：

```bash
psql -U postgres -d postgres -f agents/email-agent/sql/000_create_database.sql
psql -U postgres -d email_agent -f agents/email-agent/sql/001_schema.sql
psql -U postgres -d email_agent -f agents/email-agent/sql/002_seed_knowledge.sql
.venv/bin/python agents/email-agent/scripts/embed_knowledge.py
```

首次或权限变更后授权 Gmail：

```bash
.venv/bin/python agents/email-agent/scripts/authorize_gmail.py --force-consent
```

启动本地审核页：

```bash
.venv/bin/email-agent-review
```

打开：

```text
http://127.0.0.1:8080/
```

## 联调流程

1. 给测试邮件添加 Gmail 标签 `email-agent`，并保持为未读。
2. 在审核页点击 `Sync labeled unread Gmail`。
3. Agent 将邮件持久化、分类、搜索知识库并生成草稿。
4. 草稿进入 `waiting_for_review`，不会自动发送。
5. 审核人编辑后点击 `Approve and send` 才会调用 Gmail 发送。
6. 若分类为 `bug`，批准动作会先在私有 GitHub 仓库创建脱敏 Issue，并把 Issue 链接加入回复。
7. 点击 `Reject` 会记录拒绝结果，不发送邮件。

同步采用 Gmail message ID 去重。只有邮件成功入库并生成待审核草稿后，才会移除 `UNREAD` 标签。发送记录和 GitHub 工具调用均有幂等记录，避免重复执行。

## 安全边界

- Web 页面没有登录系统，只能绑定 `127.0.0.1`；不要将它直接暴露到局域网或公网。
- OAuth 凭据、Token 和 `.env` 不得提交到 Git。
- 当前使用 `MemorySaver`，图检查点不会跨进程保存；业务邮件和审核状态会保存到 PostgreSQL。
- 当前只适合本地联调，不应自动回复真实客户。
- GitHub Issue 仅发送分类摘要，并对常见邮箱和密钥格式脱敏；不要把原始邮件正文或附件写入 Issue。
- 生产化仍需身份认证、CSRF 防护、持久化 Checkpointer、任务队列、监控、密钥管理和正式业务知识库。
