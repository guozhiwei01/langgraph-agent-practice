"""Local-only FastAPI review UI for Gmail drafts."""

from __future__ import annotations

from html import escape

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from email_agent.service import EmailAgentService
from email_agent.storage import list_review_tasks


app = FastAPI(title="Email Agent Review", docs_url=None, redoc_url=None)
service = EmailAgentService()


def page(message: str = "") -> str:
    cards = []
    for task in list_review_tasks():
        classification = task.get("classification") or {}
        disabled = task["status"] != "waiting_for_review"
        disabled_attr = "disabled" if disabled else ""
        cards.append(
            f"""
            <article>
              <h2>#{task['id']} · {escape(task['subject'] or '(no subject)')}</h2>
              <p><strong>Status:</strong> {escape(task['status'])} ·
                 <strong>From:</strong> {escape(task['sender_email'])}</p>
              <p><strong>Classification:</strong>
                 {escape(str(classification.get('intent', 'pending')))} /
                 {escape(str(classification.get('urgency', 'pending')))}</p>
              <details><summary>Original email</summary><pre>{escape(task['plain_text_body'])}</pre></details>
              <form method="post" action="/tasks/{task['id']}/approve">
                <label>Reviewed response</label>
                <textarea name="response_text" rows="10" {disabled_attr}>{escape(task.get('draft_response') or '')}</textarea>
                <button type="submit" {disabled_attr}>Approve and send</button>
              </form>
              <form method="post" action="/tasks/{task['id']}/reject" class="reject">
                <input name="reason" placeholder="Rejection reason" {disabled_attr}>
                <button type="submit" {disabled_attr}>Reject</button>
              </form>
            </article>
            """
        )
    content = "".join(cards) or "<p>No tasks are waiting for review.</p>"
    return f"""<!doctype html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    <title>Email Agent Review</title><style>
    body{{font-family:system-ui;max-width:960px;margin:40px auto;padding:0 20px;background:#f5f6f8;color:#172033}}
    header,article{{background:white;border-radius:14px;padding:22px;margin-bottom:18px;box-shadow:0 3px 16px #1b2a3a12}}
    textarea,input{{width:100%;box-sizing:border-box;margin:8px 0;padding:12px;border:1px solid #ccd3dc;border-radius:8px}}
    button{{padding:10px 16px;border:0;border-radius:8px;background:#2156d9;color:white;cursor:pointer}}
    .reject button{{background:#a72a35}} pre{{white-space:pre-wrap}} .notice{{color:#176b3a}}
    </style></head><body><header><h1>Email Agent Review</h1>
    <p>Local demo: Gmail label <code>email-agent</code>; every reply requires approval.</p>
    <form method="post" action="/sync"><button type="submit">Sync labeled unread Gmail</button></form>
    <p class="notice">{escape(message)}</p></header>{content}</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index(message: str = "") -> str:
    return page(message)


@app.post("/sync")
def sync() -> RedirectResponse:
    try:
        results = service.sync_gmail()
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return RedirectResponse(url=f"/?message=Imported+{len(results)}+message(s)", status_code=303)


@app.post("/tasks/{task_id}/approve")
def approve(task_id: int, response_text: str = Form(...)) -> RedirectResponse:
    try:
        service.approve_and_send(task_id, response_text=response_text)
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(url="/?message=Reply+sent", status_code=303)


@app.post("/tasks/{task_id}/reject")
def reject(task_id: int, reason: str = Form("")) -> RedirectResponse:
    try:
        service.reject(task_id, reason=reason)
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(url="/?message=Task+rejected", status_code=303)


def main() -> None:
    """Start the local review UI; never bind this unauthenticated demo publicly."""
    import uvicorn

    uvicorn.run("email_agent.web:app", host="127.0.0.1", port=8080, reload=False)


if __name__ == "__main__":
    main()
