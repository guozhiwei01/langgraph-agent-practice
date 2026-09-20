# 邮件多意图分类与处理策略

## 1. 问题背景

当前学习版使用下面的结构对整封邮件分类：

```python
class EmailClassification(TypedDict):
    intent: Literal["question", "bug", "billing", "feature", "complex"]
    urgency: Literal["low", "medium", "high", "critical"]
    topic: str
    summary: str
```

这里的 `intent` 是单个 `Literal` 值，因此一封邮件只能选择一个意图。例如：

```python
{"intent": "billing", ...}  # 合法
{"intent": ["billing", "bug"], ...}  # 不符合当前类型定义
```

这种设计适合学习基本的分类和条件路由，但现实中的一封邮件可能同时包含多个独立问题：

> 我的订阅被重复扣费了，而且选择 PDF 格式导出时应用会崩溃。

这封邮件同时包含 `billing` 和 `bug`。二者都很重要时，强制选择一个“主要意图”会遗漏另一个问题。

## 2. 学习阶段的处理策略

学习阶段建议暂时保留“一封邮件对应一个主要意图”的模型：

```python
intent: Literal["question", "bug", "billing", "feature", "complex"]
```

分类器选择对当前处理最重要的意图，其他信息保留在 `topic` 和 `summary` 中。例如涉及重复扣费和导出崩溃时，可以优先处理资金问题：

```python
{
    "intent": "billing",
    "urgency": "high",
    "topic": "重复扣费和 PDF 导出崩溃",
    "summary": "用户报告订阅被重复扣费，同时反馈 PDF 导出时应用崩溃。",
}
```

这样做的目的不是模拟完整生产系统，而是先掌握以下 LangGraph 基础：

1. 使用结构化输出对邮件分类。
2. 将分类结果写入图状态。
3. 根据一个确定值进行条件路由。
4. 搜索文档、生成草稿并完成人工确认。

学习版可以使用简单的单分支路由：

```python
def route_by_intent(state: EmailAgentState) -> str:
    return state["classification"]["intent"]
```

这个阶段应明确记录其限制：它只能保证主要问题进入正确分支，不能保证一封多问题邮件中的所有问题都得到独立处理。

## 3. 生产环境的建模原则

生产环境不应强迫整封邮件只能属于一个意图。更合理的原则是：

> `intent` 属于具体问题（issue），而不是整封邮件；一封邮件可以包含多个 issue，每个 issue 有自己的意图、紧急程度和处理状态。

“复杂”也不是真正的业务意图。`question`、`bug`、`billing`、`feature_request` 描述用户在谈什么，而 `complex` 描述问题有多难处理。生产设计中应把它们拆开。

可采用下面的数据结构：

```python
from typing import Literal, TypedDict


Intent = Literal["question", "bug", "billing", "feature_request"]
Urgency = Literal["low", "medium", "high", "critical"]


class EmailIssue(TypedDict):
    intent: Intent
    topic: str
    summary: str
    urgency: Urgency
    importance: Literal["primary", "secondary"]
    complexity: Literal["simple", "complex"]
    needs_human: bool


class EmailClassification(TypedDict):
    overall_urgency: Urgency
    issues: list[EmailIssue]
    handling_mode: Literal["single", "multiple", "human_escalation"]
```

注意，`primary` 不必唯一。多个问题可以同时是主要问题：

```python
{
    "overall_urgency": "high",
    "handling_mode": "multiple",
    "issues": [
        {
            "intent": "billing",
            "topic": "重复扣费",
            "summary": "用户的订阅费用被扣除两次。",
            "urgency": "high",
            "importance": "primary",
            "complexity": "simple",
            "needs_human": False,
        },
        {
            "intent": "bug",
            "topic": "PDF 导出崩溃",
            "summary": "选择 PDF 格式导出时应用崩溃。",
            "urgency": "high",
            "importance": "primary",
            "complexity": "complex",
            "needs_human": True,
        },
    ],
}
```

## 4. 两个主要意图时如何处理

两个同等重要的意图不应该硬选出唯一主意图，也不应该让整封邮件只走一个业务分支。

但“不走单一分支”不等于完全取消路由。生产环境仍然需要路由，只是路由对象从“整封邮件”变成了拆分后的每个 `issue`：

```text
收到邮件
  -> 提取一个或多个 issue
  -> 对每个 issue 独立分发
       billing issue -> 账单处理器
       bug issue     -> 故障处理器
  -> 汇总各处理器的结果
  -> 生成一封完整、一致的回复
```

对应的处理思路可以写成：

```python
def dispatch_issue(issue: EmailIssue) -> str:
    if issue["needs_human"]:
        return "human_escalation"
    return issue["intent"]
```

图的编排过程应负责：

1. 将邮件拆成多个可独立处理的 issue。
2. 分别调用问答、故障、账单或功能请求处理器。
3. 保存每个 issue 的处理结果，避免遗漏或重复处理。
4. 等所有自动处理分支结束后统一生成回复。
5. 任一问题需要人工处理时，在草稿中说明当前状态并创建升级任务。

最终回复仍应是一封邮件，而不是让多个处理器分别给客户发送邮件。这样能避免内容重复、语气不一致或多次发送。

## 5. 主要与次要问题

`primary` 和 `secondary` 表示业务重要性，不应该决定“是否处理”：

- `primary`：必须在本次处理流程中明确解决或升级，可以有多个。
- `secondary`：仍需回应，但可以给出简短说明、文档链接或安排后续跟进。
- 无法可靠判断重要性时，不应让模型随意丢弃问题；应保留该 issue，必要时交由人工确认。

整封邮件的 `overall_urgency` 通常取所有 issue 中最高的紧急程度，但实际系统还应叠加确定性规则。例如重复扣费、账户安全和服务完全不可用可以直接提高紧急程度，不能只依赖 LLM 判断。

## 6. 何时升级人工

多意图本身不代表一定要转人工。可以自动拆分并分别处理的问题，仍然能够自动回复。以下情况更适合升级人工：

- 问题之间相互依赖，无法独立回答。
- 涉及退款、赔付、合同或其他需要人工授权的操作。
- 技术问题需要查看客户环境、日志或敏感数据。
- 分类或答案置信度不足。
- 多个处理器给出的建议相互冲突。
- 客户明确要求人工介入。

升级应以 issue 为粒度记录原因。即使其中一个问题需要人工处理，其他能够安全回答的问题也可以先生成草稿，但最终是否立即发送应由业务规则决定。

## 7. 从学习版演进到生产版

建议按以下顺序逐步演进：

1. **单意图分类**：保留当前 `EmailClassification`，完成基础图和条件路由。
2. **多问题提取**：增加 `EmailIssue`，将结果改为 `issues: list[EmailIssue]`。
3. **多 issue 分发**：每个 issue 独立处理，并在状态中保存对应结果。
4. **结果汇总**：增加聚合节点，生成一封覆盖所有问题的回复草稿。
5. **人工升级和跟进**：加入授权规则、置信度、升级原因、跟进时间与持久化状态。
6. **生产保障**：补充幂等、重试、超时、审计、权限控制、敏感信息保护和可观测性。

因此，当前单 `intent` 设计并不是错误，而是刻意简化的学习模型；当目标变成真实生产处理时，应从“整封邮件单标签分类”升级为“邮件内多个 issue 的提取、分发、汇总和升级”。
