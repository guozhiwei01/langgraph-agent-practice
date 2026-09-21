"""Prompt templates formatted on-demand within nodes."""

DRAFT_RESPONSE_TEMPLATE = """Draft a polite and professional customer support response.

Customer email:
{email_content}

Approved evidence:
{docs}

Guidelines:
- Maintain a helpful and empathetic tone
- Directly address customer concerns
- Use only the approved evidence for product behavior, policy, timing, and procedures
- Do not promise refunds, compensation, deadlines, or account changes
- Do not request passwords, authentication tokens, secret keys, or full payment credentials
- If the evidence is incomplete, clearly say what cannot yet be confirmed"""


CLARIFICATION_RESPONSE_TEMPLATE = """Draft a short, polite response asking for the minimum
additional information needed to handle this customer request safely.

Customer email:
{email_content}

Guidelines:
- Do not invent product behavior or policy
- Do not promise refunds, compensation, deadlines, or account changes
- Do not request passwords, authentication tokens, secret keys, or full payment credentials
- Ask only for information relevant to diagnosing or routing the request"""
