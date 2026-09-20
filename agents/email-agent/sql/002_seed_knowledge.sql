\set ON_ERROR_STOP on

-- Fictional NimbusDesk product data for local development only.
-- Every document is marked demo=true so it cannot be confused with a real policy.

BEGIN;

INSERT INTO knowledge_documents (
    slug, title, category, source_uri, locale, version, status, published_at, metadata
)
VALUES
    ('password-reset', 'Resetting a NimbusDesk password', 'account',
     'kb://demo/account/password-reset', 'en-US', '2026.09', 'published', NOW(),
     '{"demo": true, "product": "NimbusDesk"}'::jsonb),
    ('pdf-export-crash', 'Troubleshooting PDF export crashes', 'bug',
     'kb://demo/export/pdf-crash', 'en-US', '2026.09', 'published', NOW(),
     '{"demo": true, "product": "NimbusDesk", "platforms": ["web", "desktop"]}'::jsonb),
    ('duplicate-subscription-charge', 'Handling duplicate subscription charges', 'billing',
     'kb://demo/billing/duplicate-charge', 'en-US', '2026.09', 'published', NOW(),
     '{"demo": true, "product": "NimbusDesk", "requires_human": true}'::jsonb),
    ('feature-request-process', 'How feature requests are reviewed', 'feature',
     'kb://demo/product/feature-requests', 'en-US', '2026.09', 'published', NOW(),
     '{"demo": true, "product": "NimbusDesk"}'::jsonb),
    ('api-504-troubleshooting', 'Intermittent API 504 troubleshooting', 'api',
     'kb://demo/api/504-troubleshooting', 'en-US', '2026.09', 'published', NOW(),
     '{"demo": true, "product": "NimbusDesk", "requires_human": true}'::jsonb),
    ('account-security-incident', 'Responding to suspected account compromise', 'security',
     'kb://demo/security/account-compromise', 'en-US', '2026.09', 'published', NOW(),
     '{"demo": true, "product": "NimbusDesk", "requires_human": true}'::jsonb)
ON CONFLICT (slug) DO UPDATE SET
    title = EXCLUDED.title,
    category = EXCLUDED.category,
    source_uri = EXCLUDED.source_uri,
    locale = EXCLUDED.locale,
    version = EXCLUDED.version,
    status = EXCLUDED.status,
    published_at = EXCLUDED.published_at,
    metadata = EXCLUDED.metadata,
    updated_at = NOW();

WITH chunk_data(document_slug, chunk_key, heading, content, metadata) AS (
    VALUES
    ('password-reset', 'self-service', 'Self-service reset',
     'On the NimbusDesk sign-in page, select Forgot password, enter the account email address, and use the single-use link sent by email. The link expires after 30 minutes. If the message does not arrive, check spam and confirm that the entered address matches the account.',
     '{"demo": true, "risk": "low"}'::jsonb),
    ('password-reset', 'support-boundary', 'When support must help',
     'Support may guide the customer through the reset flow but must never ask for the current password or a one-time code. If the customer no longer controls the registered mailbox, escalate to account recovery and do not change the email address through chat.',
     '{"demo": true, "risk": "high"}'::jsonb),

    ('pdf-export-crash', 'first-response', 'First response checklist',
     'Ask for the NimbusDesk app version, operating system, approximate export time, report size, and whether CSV export succeeds. Recommend retrying with a report under 100 pages and disabling custom fonts. Do not claim the data is corrupted solely because the PDF renderer crashed.',
     '{"demo": true, "risk": "medium"}'::jsonb),
    ('pdf-export-crash', 'known-workaround', 'Temporary workaround',
     'For local testing, the documented workaround is to export the report as CSV or split the date range into smaller periods. A bug ticket should include reproducible steps and sanitized logs. Never request customer records or access tokens by email.',
     '{"demo": true, "risk": "medium"}'::jsonb),

    ('duplicate-subscription-charge', 'verification', 'Verification steps',
     'A duplicate-charge report requires the invoice identifiers, charge dates, amounts, currency, and the last four digits of the payment method. Agents must not request a full card number or security code. Similar-looking pending authorizations are not proof that two settled charges occurred.',
     '{"demo": true, "risk": "high"}'::jsonb),
    ('duplicate-subscription-charge', 'refund-authority', 'Refund authority',
     'The Email Agent may acknowledge the report and create a billing review task, but it may not promise or issue a refund. Only an authorized billing specialist may confirm duplicate settlement and approve a refund. The customer response must distinguish review started from refund completed.',
     '{"demo": true, "risk": "high"}'::jsonb),

    ('feature-request-process', 'submission', 'Recording a feature request',
     'Record the customer problem, desired outcome, affected platform, and frequency of need. For a dark-mode request, note whether the request concerns the mobile app, web app, or both. Avoid promising delivery dates or saying that a request is on the roadmap unless product management has confirmed it.',
     '{"demo": true, "risk": "low"}'::jsonb),
    ('feature-request-process', 'response', 'Customer response',
     'Thank the customer, restate the need, and explain that the request will be reviewed. A GitHub issue identifier may be included only after the create-issue API returns success. Public repositories must not receive customer email addresses, account identifiers, or private logs.',
     '{"demo": true, "risk": "medium"}'::jsonb),

    ('api-504-troubleshooting', 'triage', '504 triage information',
     'Collect the UTC timestamps, endpoint path without secrets, request or correlation IDs, client region, retry behavior, and observed frequency. Ask the customer to remove API keys, authorization headers, and personal data before sharing logs. Intermittent 504 responses may originate from an upstream timeout and require engineering review.',
     '{"demo": true, "risk": "high"}'::jsonb),
    ('api-504-troubleshooting', 'safe-guidance', 'Safe temporary guidance',
     'Where the operation is idempotent, clients may retry with exponential backoff and jitter. Do not recommend blind retries for non-idempotent write operations. Do not claim that the incident is resolved until monitoring and an engineering owner confirm recovery.',
     '{"demo": true, "risk": "high"}'::jsonb),

    ('account-security-incident', 'containment', 'Immediate containment',
     'If a customer reports an unexpected login or suspected compromise, classify the case as critical, advise changing the password through the official sign-in page, revoke active sessions when the product supports it, and escalate to the security queue. Never ask the customer to email a password or one-time code.',
     '{"demo": true, "risk": "critical"}'::jsonb),
    ('account-security-incident', 'communication', 'Communication limits',
     'Acknowledge the report without confirming an attacker, breach scope, or data exposure before investigation. Preserve the message and relevant audit identifiers. Security incident details must not be copied into a public GitHub issue.',
     '{"demo": true, "risk": "critical"}'::jsonb)
)
INSERT INTO knowledge_chunks (document_id, chunk_key, heading, content, metadata)
SELECT document.id, data.chunk_key, data.heading, data.content, data.metadata
FROM chunk_data AS data
JOIN knowledge_documents AS document ON document.slug = data.document_slug
ON CONFLICT (document_id, chunk_key) DO UPDATE SET
    heading = EXCLUDED.heading,
    content = EXCLUDED.content,
    metadata = EXCLUDED.metadata,
    embedding = CASE
        WHEN knowledge_chunks.content IS DISTINCT FROM EXCLUDED.content THEN NULL
        ELSE knowledge_chunks.embedding
    END,
    embedding_model = CASE
        WHEN knowledge_chunks.content IS DISTINCT FROM EXCLUDED.content THEN NULL
        ELSE knowledge_chunks.embedding_model
    END,
    embedded_at = CASE
        WHEN knowledge_chunks.content IS DISTINCT FROM EXCLUDED.content THEN NULL
        ELSE knowledge_chunks.embedded_at
    END,
    updated_at = NOW();

COMMIT;

