# LangGraph Agent Practice

这是一个用于学习 LangGraph、工具调用和多 Agent 协作的 uv workspace。每个目录下的 Agent 都是独立包：可以单独运行、测试和演进，而不会把示例代码堆到同一个 `main.py` 中。

## 当前 Agent

- `agents/arithmetic-agent`：算术工具调用与 LangGraph 循环的最小可运行示例。
- `agents/email-agent`：邮件 Agent 的分层骨架，预留了人工确认、Checkpointer 和 A2A 适配位置。

## 运行

先为某个 Agent 配置其自己的环境变量文件，再从仓库根目录运行：

```bash
uv run --package arithmetic-agent arithmetic-agent
```

测试算术 Agent：

```bash
uv run --package arithmetic-agent python -m unittest discover -s agents/arithmetic-agent/tests -v
```

架构约定见 [docs/architecture.md](docs/architecture.md)。
