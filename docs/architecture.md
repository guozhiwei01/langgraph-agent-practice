# 学习 Agent 的架构约定

## 目标

每个 Agent 是一个独立、可运行、可测试的 uv workspace member。它既可以通过本地 CLI 演示，也可以在未来被 HTTP 或 A2A 调用；协议变化不应迫使业务节点重写。

## 分层

```text
CLI / HTTP / A2A transport
            ↓
        service.py
            ↓
      graph.py (编排)
            ↓
 nodes/ + tools/ + prompts.py
            ↓
 外部模型、邮件、数据库等基础设施
```

- `schemas.py`：对外稳定的请求、响应和 artifact 契约，必须可 JSON 序列化。
- `state.py`：仅供 LangGraph 运行的内部状态，可随图的演化调整。
- `nodes/`：一个节点完成一个清晰业务步骤，输入 State，返回 State 补丁。
- `tools/`：外部系统调用与其短暂失败重试；节点不直接嵌入 SDK 细节。
- `graph.py`：只负责节点注册、边、条件路由与 Checkpointer 编译。
- `service.py`：CLI、Web 和 A2A 共同调用的应用入口，负责契约与图状态间的转换。
- `transport/`：协议适配层。A2A task、message、artifact 的映射只能放在这里。

## 演进规则

1. 新 Agent 从 `agents/<agent-name>/` 开始，拥有自己的 `pyproject.toml`、`.env.example`、`README.md` 和测试。
2. 两个 Agent 以上确认存在同一语义的重复代码后，才抽到 `packages/agent_core`；不要预先建立万能 `common` 包。
3. 禁止跨 Agent import 对方的 `nodes.py` 或 `state.py`。协作只能依赖公开 schema 或 A2A 契约。
4. 需要人工确认、长任务或恢复时，统一传递 `thread_id`，由 `graph.py` 的 Checkpointer 维持状态。
5. 每个 Agent 的 `evals/` 保存可重复的输入输出案例，作为提示词、模型和图改动后的回归检查。

## A2A 预留

未来每个 Agent 至少应明确：能力描述、JSON 输入输出、`task_id`/`thread_id`、进度事件和结构化 artifact。A2A 仅调用 `service.py`，不直接访问图的节点或内部状态。
