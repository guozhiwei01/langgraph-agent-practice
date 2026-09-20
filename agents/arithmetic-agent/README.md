# Arithmetic Agent

一个最小可运行的 LangGraph 工具调用示例。模型收到算术问题后，可选择调用 `add`、`multiply` 或 `divide`，然后根据工具结果生成回答。

## 配置与运行

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
uv run --package arithmetic-agent arithmetic-agent
```

从仓库根目录运行测试：

```bash
uv run --package arithmetic-agent python -m unittest discover -s agents/arithmetic-agent/tests -v
```

`agent_graph.png` 是此前生成的图结构快照；当前编排实现在 `src/arithmetic_agent/graph.py`。
