"""Compare DeepSeek general-purpose classification with TypeSafe decisions.

The benchmark uses synthetic, manually labeled messages so it is safe to run
against external APIs. It measures intent accuracy, response validity, latency,
token usage, and estimated API cost.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import statistics
import time
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel

from email_agent.classification import triage_questions
from email_agent.config import get_settings
from email_agent.decision_engine import TypeSafeDecisionEngine


Intent = Literal["question", "bug", "billing", "feature", "other"]


@dataclass(frozen=True)
class Case:
    kind: Literal["clean", "edge"]
    language: Literal["en", "zh"]
    expected: Intent
    subject: str
    content: str


class LLMAnswer(BaseModel):
    intent: Intent
    urgency: Literal["low", "medium", "high", "critical"]
    complexity: Literal["standard", "investigation", "specialist"]
    refund_requested: bool
    security_incident: bool
    human_requested: bool


CASES = [
    Case("clean", "en", "question", "Reset password", "How do I reset my password? Please send the steps."),
    Case("clean", "zh", "question", "导出数据", "请问在哪里可以把项目数据导出成 CSV？"),
    Case("clean", "en", "question", "Account permissions", "What can an editor do compared with an administrator?"),
    Case("clean", "zh", "question", "修改通知设置", "请问如何关闭每周邮件通知？"),
    Case("clean", "en", "bug", "Mobile app crashes", "The iOS app closes whenever I open notifications."),
    Case("clean", "zh", "bug", "登录返回 500", "所有用户登录时都收到 500 错误，无法进入系统。"),
    Case("clean", "en", "bug", "Wrong search results", "Exact-name search omits the matching customer and shows unrelated records."),
    Case("clean", "zh", "bug", "文件上传失败", "上传任何 PDF 都停在 99%，更换浏览器也没有用。"),
    Case("clean", "en", "billing", "Duplicate charge", "My card was charged twice for one monthly subscription."),
    Case("clean", "zh", "billing", "取消后仍扣费", "我已经取消订阅，但本月仍然被扣费，请退款。"),
    Case("clean", "en", "billing", "Invoice address", "Please update the billing address on our next invoice."),
    Case("clean", "zh", "billing", "套餐价格", "我们的年度订阅续费金额为什么比去年高？"),
    Case("clean", "en", "feature", "Dark mode", "Please add a dark mode option to the dashboard."),
    Case("clean", "zh", "feature", "企业单点登录", "希望产品增加 SAML SSO 功能。"),
    Case("clean", "en", "feature", "Bulk edit", "Please let us edit the owner of several records in one action."),
    Case("clean", "zh", "feature", "增加审计日志", "希望增加管理员审计日志和导出功能。"),
    Case("edge", "en", "billing", "Charge and export problem", "I was charged twice, and PDF export also crashes. Please resolve both."),
    Case("edge", "zh", "billing", "扣费和登录问题", "账户被重复扣费，而且今天无法登录，请先处理扣费。"),
    Case("edge", "en", "bug", "Broken workaround", "PDF export crashes. Add Word export only if this cannot be fixed."),
    Case("edge", "zh", "bug", "通知还是配置", "通知一直收不到，不确定是配置错误还是系统故障。"),
    Case("edge", "en", "question", "Unexpected behavior or setup", "Notifications are not arriving. Is there a setting I need to enable?"),
    Case("edge", "zh", "question", "退款规则咨询", "我还没有购买，想先了解购买后七天内是否可以退款。"),
    Case("edge", "en", "feature", "API availability", "Do you have an API? If not, please add one for automated exports."),
    Case("edge", "zh", "feature", "现有功能限制", "目前只能导出 CSV，希望以后可以直接导出 PDF。"),
]


SYSTEM_PROMPT = """You are a customer-email routing classifier.
Return exactly one JSON object with intent, urgency, complexity,
refund_requested, security_incident, and human_requested.

intent must be one of:
- question: general product, account, policy, or usage question
- bug: software malfunction, outage, or incorrect behavior
- billing: payment, subscription, invoice, charge, or refund issue
- feature: request for new or changed product functionality
- other: the primary request does not fit any listed category

urgency must be one of low, medium, high, critical.
complexity must be one of standard, investigation, specialist.
refund_requested, security_incident, and human_requested must be booleans.
Do not add explanation or additional fields."""


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    position = max(0, math.ceil(percent * len(ordered)) - 1)
    return ordered[position]


def main() -> None:
    settings = get_settings()
    if not settings.typesafe_api_key:
        raise RuntimeError("TYPESAFE_API_KEY is required for this benchmark.")

    deepseek = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.base_url,
        timeout=30,
    )
    typesafe = TypeSafeDecisionEngine.from_settings(settings)
    results = {"typesafe": [], "deepseek": []}

    for index, case in enumerate(CASES, start=1):
        providers = ("typesafe", "deepseek") if index % 2 else ("deepseek", "typesafe")
        for provider in providers:
            started = time.perf_counter()
            valid = True
            predicted = None
            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0
            confidence = None
            error = None
            try:
                if provider == "typesafe":
                    response = typesafe.evaluate(
                        state={"subject": case.subject, "content": case.content},
                        questions=triage_questions(),
                    )
                    answer = response["answers"]["intent"]
                    predicted = answer["choice"]
                    confidence = float(answer["confidence"])
                    usage = response.get("usage") or {}
                    input_tokens = int(usage.get("input_tokens") or 0)
                    output_tokens = int(usage.get("output_tokens") or 0)
                else:
                    response = deepseek.chat.completions.create(
                        model=settings.model_name,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": f"Subject: {case.subject}\nContent: {case.content}",
                            },
                        ],
                        response_format={"type": "json_object"},
                        max_tokens=100,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                    content = response.choices[0].message.content or ""
                    predicted = LLMAnswer.model_validate_json(content).intent
                    if response.usage:
                        input_tokens = response.usage.prompt_tokens
                        output_tokens = response.usage.completion_tokens
                        details = response.usage.prompt_tokens_details
                        cached_tokens = int(
                            (details.cached_tokens if details else 0) or 0
                        )
            except Exception as exception:  # keep the comparison running
                valid = False
                error = f"{type(exception).__name__}: {exception}"

            elapsed_ms = (time.perf_counter() - started) * 1000
            result = {
                "case": index,
                "kind": case.kind,
                "language": case.language,
                "expected": case.expected,
                "predicted": predicted,
                "correct": predicted == case.expected,
                "valid": valid,
                "latency_ms": elapsed_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "confidence": confidence,
                "error": error,
            }
            results[provider].append(result)
            mark = "OK" if result["correct"] else "MISS"
            print(
                f"[{index:02}/{len(CASES)}] {provider:8} {mark:4} "
                f"{elapsed_ms:7.1f} ms expected={case.expected:8} "
                f"predicted={str(predicted):8}",
                flush=True,
            )

    print("\nSUMMARY")
    for provider, rows in results.items():
        valid_rows = [row for row in rows if row["valid"]]
        latencies = [row["latency_ms"] for row in rows]
        correct = sum(row["correct"] for row in rows)
        clean = [row for row in rows if row["kind"] == "clean"]
        edge = [row for row in rows if row["kind"] == "edge"]
        english = [row for row in rows if row["language"] == "en"]
        chinese = [row for row in rows if row["language"] == "zh"]
        input_tokens = sum(row["input_tokens"] for row in rows)
        output_tokens = sum(row["output_tokens"] for row in rows)
        cached_tokens = sum(row["cached_tokens"] for row in rows)
        cache_miss_tokens = max(0, input_tokens - cached_tokens)

        if provider == "typesafe":
            low_cost = high_cost = input_tokens * 0.042 / 1_000_000
        else:
            low_cost = (
                cached_tokens * 0.003
                + cache_miss_tokens * 0.15
                + output_tokens * 0.6
            ) / 1_000_000
            high_cost = (
                cached_tokens * 0.006
                + cache_miss_tokens * 0.3
                + output_tokens * 1.2
            ) / 1_000_000

        summary = {
            "provider": provider,
            "accuracy": correct / len(rows),
            "clean_accuracy": sum(row["correct"] for row in clean) / len(clean),
            "edge_accuracy": sum(row["correct"] for row in edge) / len(edge),
            "english_accuracy": sum(row["correct"] for row in english) / len(english),
            "chinese_accuracy": sum(row["correct"] for row in chinese) / len(chinese),
            "valid_rate": len(valid_rows) / len(rows),
            "latency_mean_ms": statistics.mean(latencies),
            "latency_p50_ms": statistics.median(latencies),
            "latency_p95_ms": percentile(latencies, 0.95),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_tokens,
            "estimated_cost_usd_off_peak": low_cost,
            "estimated_cost_usd_peak": high_cost,
        }
        if provider == "typesafe":
            for threshold in (0.6, 0.7):
                accepted = [
                    row for row in rows if (row["confidence"] or 0) >= threshold
                ]
                summary[f"coverage_at_{threshold}"] = len(accepted) / len(rows)
                summary[f"accepted_accuracy_at_{threshold}"] = (
                    sum(row["correct"] for row in accepted) / len(accepted)
                    if accepted
                    else None
                )
        print(json.dumps(summary, ensure_ascii=False))

    print("\nMISSES")
    for provider, rows in results.items():
        for row in rows:
            if not row["correct"]:
                print(json.dumps({"provider": provider, **row}, ensure_ascii=False))


if __name__ == "__main__":
    main()
