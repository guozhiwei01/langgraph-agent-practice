"""Prompt templates formatted on-demand within nodes."""

CLASSIFICATION_PROMPT_TEMPLATE = """Analyze this customer email and classify it:
Email: {email_content}
From: {sender_email}
Return only one valid JSON object with these fields:
- intent: one of question, bug, billing, feature, complex
- urgency: one of low, medium, high, critical
- topic: a short string
- summary: a concise string
Do not include Markdown fences or explanatory text."""

DRAFT_RESPONSE_TEMPLATE = """Draft a polite and professional response to: {email_content}
Context Information:
{docs}

Guidelines:
- Maintain a helpful and empathetic tone
- Directly address customer concerns
- Reference the documentation provided"""
