"""Local-only FastAPI review UI for Gmail drafts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
import logging
import time
import uuid

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from email_agent.auth import SESSION_COOKIE, ReviewAuth
from email_agent.config import get_settings
from email_agent.observability import configure_logging
from email_agent.storage import (
    database_ready,
    enqueue_job,
    enqueue_review_job,
    list_dead_jobs,
    list_review_tasks,
    queue_metrics,
    record_audit_event,
    retry_dead_job,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize authentication and logging; workers own graph execution."""
    settings = get_settings()
    configure_logging(settings.log_level)
    application.state.review_auth = ReviewAuth.from_settings(settings)
    yield


app = FastAPI(
    title="Email Agent Review",
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Request failed",
            extra={"event": "http.failed", "request_id": request_id},
        )
        raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'"
    logger.info(
        "Request completed",
        extra={
            "event": "http.completed",
            "request_id": request_id,
            "status_code": response.status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        },
    )
    return response


def _auth(request: Request) -> ReviewAuth:
    return request.app.state.review_auth


def _current_user(request: Request) -> str | None:
    return _auth(request).verify_session(request.cookies.get(SESSION_COOKIE))


def _require_user(request: Request) -> str:
    phone = _current_user(request)
    if phone is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return phone


def _require_csrf(request: Request, csrf_token: str) -> None:
    session_token = request.cookies.get(SESSION_COOKIE)
    if not _auth(request).verify_csrf(session_token, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


def _audit(request: Request, **values) -> None:
    try:
        record_audit_event(request_id=request.state.request_id, **values)
    except Exception:
        logger.exception(
            "Audit event could not be persisted",
            extra={"event": "audit.failed", "request_id": request.state.request_id},
        )


def login_page(message: str = "") -> str:
    return f"""<!doctype html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    <title>Sign in · Email Agent Review</title><style>
    body{{font-family:system-ui;max-width:460px;margin:80px auto;padding:0 20px;background:#f5f6f8;color:#172033}}
    main{{background:white;border-radius:14px;padding:26px;box-shadow:0 3px 16px #1b2a3a12}}
    input{{width:100%;box-sizing:border-box;margin:8px 0 14px;padding:12px;border:1px solid #ccd3dc;border-radius:8px}}
    button{{padding:10px 16px;border:0;border-radius:8px;background:#2156d9;color:white;cursor:pointer;width:100%}}
    .error{{color:#a72a35}}
    </style></head><body><main><h1>Email Agent Review</h1>
    <p>Sign in to review and send email drafts.</p>
    <p class="error">{escape(message)}</p>
    <form method="post" action="/login">
      <label>Phone</label><input name="phone" autocomplete="username" required>
      <label>Password</label><input name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form></main></body></html>"""


def page(message: str = "", phone: str = "", csrf_token: str = "") -> str:
    cards = []
    for task in list_review_tasks():
        classification = task.get("classification") or {}
        evidence = task.get("evidence_evaluation") or {}
        validation = task.get("response_validation") or {}
        reasons = validation.get("review_reasons") or []
        reasons_html = "".join(f"<li>{escape(str(reason))}</li>" for reason in reasons)
        no_draft_notice = (
            "<p class=\"warning\">Automation stopped during triage. "
            "A reviewer must compose the response manually.</p>"
            if not task.get("draft_response")
            else ""
        )
        disabled = task["status"] != "waiting_for_review"
        disabled_attr = "disabled" if disabled else ""
        retry_html = (
            f"""<form method="post" action="/jobs/{task['latest_job_id']}/retry">
                <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                <button type="submit">Retry failed job</button></form>"""
            if task.get("latest_job_status") == "dead"
            else ""
        )
        job_error = (
            f'<p class="error"><strong>Last error:</strong> {escape(task["latest_job_error"])}</p>'
            if task.get("latest_job_error")
            else ""
        )
        cards.append(
            f"""
            <article>
              <h2>#{task['id']} · {escape(task['subject'] or '(no subject)')}</h2>
              <p><strong>Status:</strong> {escape(task['status'])} ·
                 <strong>From:</strong> {escape(task['sender_email'])}</p>
              {job_error}
              <p><strong>Classification:</strong>
                 {escape(str(classification.get('intent', 'pending')))} /
                 {escape(str(classification.get('urgency', 'pending')))}</p>
              <p><strong>Evidence:</strong>
                 {escape(str(evidence.get('evidence_sufficient', 'not evaluated')))} ·
                 <strong>Draft safe:</strong>
                 {escape(str(validation.get('safe_to_send', 'not evaluated')))}</p>
              {f'<ul class="warning">{reasons_html}</ul>' if reasons_html else ''}
              {no_draft_notice}
              <details><summary>Original email</summary><pre>{escape(task['plain_text_body'])}</pre></details>
              <form method="post" action="/tasks/{task['id']}/approve">
                <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                <label>{'Reviewer-authored response' if not task.get('draft_response') else 'Reviewed response'}</label>
                <textarea name="response_text" rows="10" {disabled_attr}>{escape(task.get('draft_response') or '')}</textarea>
                <button type="submit" {disabled_attr}>Approve and send</button>
              </form>
              <form method="post" action="/tasks/{task['id']}/reject" class="reject">
                <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                <input name="reason" placeholder="Rejection reason" {disabled_attr}>
                <button type="submit" {disabled_attr}>Reject</button>
              </form>
              {retry_html}
            </article>
            """
        )
    content = "".join(cards) or "<p>No tasks are waiting for review.</p>"
    dead_jobs = "".join(
        f"""<article><h2>Dead job #{job['id']}</h2>
        <p><strong>Type:</strong> {escape(job['job_type'])} ·
        <strong>Attempts:</strong> {job['attempt_count']}/{job['max_attempts']}</p>
        <p class="error">{escape(job.get('last_error') or 'Unknown failure')}</p>
        <form method="post" action="/jobs/{job['id']}/retry">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
          <button type="submit">Retry failed job</button>
        </form></article>"""
        for job in list_dead_jobs()
    )
    return f"""<!doctype html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    <title>Email Agent Review</title><style>
    body{{font-family:system-ui;max-width:960px;margin:40px auto;padding:0 20px;background:#f5f6f8;color:#172033}}
    header,article{{background:white;border-radius:14px;padding:22px;margin-bottom:18px;box-shadow:0 3px 16px #1b2a3a12}}
    textarea,input{{width:100%;box-sizing:border-box;margin:8px 0;padding:12px;border:1px solid #ccd3dc;border-radius:8px}}
    button{{padding:10px 16px;border:0;border-radius:8px;background:#2156d9;color:white;cursor:pointer}}
    .reject button{{background:#a72a35}} pre{{white-space:pre-wrap}} .notice{{color:#176b3a}} .error{{color:#a72a35}}
    .warning{{color:#8a3a00;background:#fff4e5;padding:12px 28px;border-radius:8px}}
    </style></head><body><header><h1>Email Agent Review</h1>
    <p>Signed in as <code>{escape(phone)}</code>. Every reply requires approval.</p>
    <form method="post" action="/sync"><input type="hidden" name="csrf_token" value="{escape(csrf_token)}"><button type="submit">Queue Gmail sync</button></form>
    <form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{escape(csrf_token)}"><button type="submit">Sign out</button></form>
    <p class="notice">{escape(message)}</p></header>{content}{dead_jobs}</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str = "") -> Response:
    phone = _current_user(request)
    if phone is None:
        return RedirectResponse(url="/login", status_code=303)
    session_token = request.cookies.get(SESSION_COOKIE) or ""
    return HTMLResponse(page(message, phone, _auth(request).issue_csrf(session_token)))


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    if _current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(login_page())


@app.post("/login")
def login(
    request: Request, phone: str = Form(...), password: str = Form(...)
) -> Response:
    auth = _auth(request)
    if not auth.verify_credentials(phone, password):
        _audit(request, actor=phone, action="auth.login", outcome="failure")
        return HTMLResponse(login_page("Invalid phone or password."), status_code=401)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        auth.issue_session(phone),
        max_age=8 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    _audit(request, actor=phone, action="auth.login")
    return response


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    phone = _require_user(request)
    _require_csrf(request, csrf_token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    _audit(request, actor=phone, action="auth.logout")
    return response


@app.post("/sync")
def sync(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    phone = _require_user(request)
    _require_csrf(request, csrf_token)
    job_id, _ = enqueue_job(
        "sync_gmail",
        deduplication_key=f"sync:{uuid.uuid4().hex}",
        payload={"limit": 20, "requested_by": phone},
    )
    _audit(request, actor=phone, action="gmail.sync_queued", entity_type="job", entity_id=str(job_id))
    return RedirectResponse(url=f"/?message=Gmail+sync+queued+as+job+{job_id}", status_code=303)


@app.post("/tasks/{task_id}/approve")
def approve(
    request: Request,
    task_id: int,
    response_text: str = Form(...),
    csrf_token: str = Form(...),
) -> RedirectResponse:
    phone = _require_user(request)
    _require_csrf(request, csrf_token)
    final_response = response_text.strip()
    if not final_response:
        raise HTTPException(status_code=422, detail="Approved response cannot be empty.")
    try:
        job_id, _ = enqueue_review_job(
            task_id,
            approved=True,
            reviewer_id=phone,
            response_text=final_response,
        )
    except (ValueError, LookupError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _audit(request, actor=phone, action="review.approval_queued", entity_type="task", entity_id=str(task_id), metadata={"job_id": job_id})
    return RedirectResponse(url=f"/?message=Reply+queued+as+job+{job_id}", status_code=303)


@app.post("/tasks/{task_id}/reject")
def reject(
    request: Request,
    task_id: int,
    reason: str = Form(""),
    csrf_token: str = Form(...),
) -> RedirectResponse:
    phone = _require_user(request)
    _require_csrf(request, csrf_token)
    try:
        job_id, _ = enqueue_review_job(
            task_id,
            approved=False,
            reviewer_id=phone,
            reason=reason.strip() or "Rejected during local review.",
        )
    except (ValueError, LookupError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _audit(request, actor=phone, action="review.rejection_queued", entity_type="task", entity_id=str(task_id), metadata={"job_id": job_id})
    return RedirectResponse(url=f"/?message=Rejection+queued+as+job+{job_id}", status_code=303)


@app.post("/jobs/{job_id}/retry")
def retry_job_route(
    request: Request, job_id: int, csrf_token: str = Form(...)
) -> RedirectResponse:
    phone = _require_user(request)
    _require_csrf(request, csrf_token)
    try:
        retry_dead_job(job_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _audit(request, actor=phone, action="job.manual_retry", entity_type="job", entity_id=str(job_id))
    return RedirectResponse(url=f"/?message=Job+{job_id}+queued+for+retry", status_code=303)


@app.get("/health/live")
def health_live() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/health/ready")
def health_ready() -> JSONResponse:
    try:
        ready = database_ready()
    except Exception as error:
        return JSONResponse({"status": "not_ready", "error": type(error).__name__}, status_code=503)
    return JSONResponse({"status": "ready" if ready else "not_ready"}, status_code=200 if ready else 503)


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    values = queue_metrics()
    lines = [
        "# HELP email_agent_jobs Number of jobs by status.",
        "# TYPE email_agent_jobs gauge",
    ]
    lines.extend(
        f'email_agent_jobs{{status="{status}"}} {count}'
        for status, count in sorted(values["jobs"].items())
    )
    lines.extend([
        "# HELP email_agent_tasks Number of email tasks by status.",
        "# TYPE email_agent_tasks gauge",
    ])
    lines.extend(
        f'email_agent_tasks{{status="{status}"}} {count}'
        for status, count in sorted(values["tasks"].items())
    )
    lines.extend([
        "# HELP email_agent_oldest_ready_job_seconds Age of the oldest queued job.",
        "# TYPE email_agent_oldest_ready_job_seconds gauge",
        f'email_agent_oldest_ready_job_seconds {values["oldest_ready_job_seconds"]}',
    ])
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def main() -> None:
    """Start the authenticated local review UI."""
    import uvicorn

    uvicorn.run("email_agent.web:app", host="127.0.0.1", port=8080, reload=False)


if __name__ == "__main__":
    main()
