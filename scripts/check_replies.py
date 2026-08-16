#!/usr/bin/env python3
"""Run the full Gmail reply pipeline: match -> draft -> learn.

Shared by the morning cron (bin/run_morning.sh) and the UI 'Check now' button.
Each claude -p step is skipped when its prep finds no jobs, so a quiet run makes
no LLM calls. Read-only on Gmail except creating drafts; never sends mail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apply_replies  # noqa: E402
import draft_learn  # noqa: E402
import draft_replies  # noqa: E402
import reply_context  # noqa: E402
from applib import config, paths  # noqa: E402
from applib.store import Store  # noqa: E402

MATCHER_TOOLS = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
                 "Task", "Read", "Write"]
DRAFTER_TOOLS = ["mcp__claude_ai_Gmail__get_thread", "mcp__claude_ai_Gmail__create_draft",
                 "Task", "Read", "Write"]
LEARNER_TOOLS = ["mcp__claude_ai_Gmail__get_thread", "Read", "Write"]

MATCHER_PROMPT = (
    "Match Gmail messages to apartment outreach. Read data/.outreach_context.json (a JSON list; "
    "each item is one apartment we contacted, with id, subject, email, channel, street, zipcode, "
    "city, decision_at, url). For EACH apartment, use the Gmail search tool to find threads "
    "received on or after its decision_at: for email-channel items search by the sender email "
    "and/or our subject line; for form-channel items (email is null) search by the street plus "
    "locality. Classify each found message into one of two kinds: (1) CONFIRMATION — an automated "
    "platform/agency receipt acknowledging that OUR enquiry was sent/received for this apartment "
    "(e.g. 'Bestaetigung zum Versand Ihrer Kontaktanfrage', 'Eingang Ihrer Anfrage', no-reply "
    "submission receipts); (2) REPLY — a substantive personal/agency response that moves things "
    "forward (offers a viewing, asks for documents, gives a contact person, answers questions). "
    "For EACH candidate of EITHER kind, dispatch a SEPARATE independent reviewer subagent (Task "
    "tool, subagent_type general-purpose) given ONLY the email's from/subject/snippet/received-date "
    "and the apartment's address/email/subject/decision_at, asking strictly (a) whether it concerns "
    "THAT apartment, and (b) which kind it is, with a one-line reason and confidence 0..1. Keep "
    "CONFIRMATIONS the reviewer approves with confidence >= 0.7 and REPLIES with confidence >= 0.6; "
    "discard anything about a different apartment or clearly unrelated. For every kept REPLY, read "
    "the thread (Gmail get_thread) and add: automated (boolean — true if it is an automated/system "
    "message, false if written by a person), summary (one sentence, <=25 words), and next_steps "
    "(<=15 words, the concrete action the applicant must take next; empty string if none). Build gmail_link "
    "as https://mail.google.com/mail/u/0/#all/<thread_id>. Write a JSON object mapping apartment id "
    "-> {confirmation: {thread_id, gmail_link, from, subject, snippet, received_at, reviewer_reason} "
    "| null, reply: {thread_id, gmail_link, from, subject, snippet, received_at, matched_by:"
    "'email'|'form', confidence, reviewer_reason, automated, summary, next_steps} | null} to "
    "data/.reply_matches.json (write {} if nothing). Do NOT send, reply to, label, star, or modify "
    "any email. Do nothing else."
)

# The drafter and learner prompts embed the applicant's real name, phone and
# mailbox, so they are built at call time from the gitignored
# config/applicant.yaml rather than frozen as module constants.
DRAFTER_PROMPT_TEMPLATE = (
    "Draft reply emails for apartment enquiries. Read data/.draft_jobs.json (a JSON list; each item: "
    "id, thread_id, listing {street, rooms, size_sqm, url}, reply {from, subject, snippet}, applicant "
    "{...}, timing {...}, notes [learned style lessons]). For EACH item: use Gmail get_thread on "
    "thread_id to read the latest message from the agency. Compose a concise, polite reply in the "
    "SAME language as that message (Swiss agencies: German, formal 'Sie'). The reply should thank "
    "them, confirm continued interest in the specific apartment, state availability for a viewing "
    "using the timing windows (viewing_window_de / viewing_window_en), offer a full application "
    "dossier on request, and "
    "answer any direct question. Apply EVERY lesson in notes. Sign as '__NAME__', phone "
    "__PHONE__, __EMAIL__. Then dispatch ONE independent reviewer subagent (Task "
    "tool, subagent_type general-purpose) with the composed draft + the listing address + the "
    "incoming message, asking it to confirm the draft is accurate (correct address, no invented "
    "facts, availability present, appropriate tone) and flag any problem; revise if flagged. Then "
    "create the draft with the Gmail create_draft tool: to = the bare email address extracted from "
    "the reply 'from' field (strip any display name, e.g. 'Agency <a@x.ch>' -> 'a@x.ch'), subject = "
    "'Re: ' + the original subject, replyToMessageId = the latest message id you read, body = your "
    "reply. Record {id: {draft_id: <returned id>, text: <the body>}} for each into "
    "data/.draft_results.json. Do NOT send, label, star, or modify any email; only create drafts. "
    "Do nothing else."
)

LEARNER_PROMPT_TEMPLATE = (
    "Learn from how the applicant edits reply drafts before sending. Read data/.learn_jobs.json (a JSON list; "
    "each item: id, thread_id, draft_text). For EACH item: use Gmail get_thread on thread_id and find "
    "the most recent message SENT BY the applicant (from __EMAIL__) in that thread. If there is "
    "no such sent message yet, set learned=false and lessons=[] (they haven't sent). If there is one, "
    "compare it to draft_text and extract up to 3 short, general, reusable lessons about how they "
    "changed it (tone, length, content added/removed, phrasing, availability specifics), each phrased "
    "as an instruction for future drafts (e.g. 'Keep it to 3 short sentences'). If they sent it "
    "essentially unchanged, lessons=[]. Set learned=true for any id where a sent message existed. "
    "Write {id: {learned: <bool>, lessons: [<str>]}} for each to data/.learn_results.json. Read-only: "
    "do NOT send, draft, label, or modify any email. Do nothing else."
)


def _fill(template: str) -> str:
    """Substitute the applicant's identity into a prompt template.

    Plain .replace() rather than str.format(): the prompts are full of literal
    JSON braces ({id: {learned: ...}}) that format() would try to interpret.
    """
    ident = config.applicant().get("identity") or {}
    return (template
            .replace("__NAME__", str(ident.get("name") or "").strip())
            .replace("__PHONE__", str(ident.get("phone") or "").strip())
            .replace("__EMAIL__", str(ident.get("email") or "").strip()))


def _run_claude(prompt: str, tools: list[str]) -> bool:
    """Invoke claude -p headless. Returns True on success. Isolated for stubbing."""
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", *tools,
             "--permission-mode", "acceptEdits"],
            stdin=subprocess.DEVNULL, cwd=str(paths.ROOT), timeout=900)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"check_replies: claude step failed: {e}")
        return False


def _read_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def run() -> dict:
    summary = {"matched": None, "drafted": 0, "learned": 0}

    # 1. match
    ctx = reply_context.build_context(Store.load())
    _write_json(paths.OUTREACH_CONTEXT_FILE, ctx)
    if ctx:
        paths.REPLY_MATCHES_FILE.unlink(missing_ok=True)
        if _run_claude(MATCHER_PROMPT, MATCHER_TOOLS):
            summary["matched"] = apply_replies.apply_matches(Store.load(),
                                                             _read_json(paths.REPLY_MATCHES_FILE))

    # 2. draft
    jobs = draft_replies.build_jobs(Store.load())
    _write_json(paths.DRAFT_JOBS_FILE, jobs)
    if jobs:
        paths.DRAFT_RESULTS_FILE.unlink(missing_ok=True)
        if _run_claude(_fill(DRAFTER_PROMPT_TEMPLATE), DRAFTER_TOOLS):
            summary["drafted"] = draft_replies.apply_drafts(
                Store.load(), _read_json(paths.DRAFT_RESULTS_FILE)).get("drafted", 0)

    # 3. learn
    ljobs = draft_learn.build_jobs(Store.load())
    _write_json(paths.LEARN_JOBS_FILE, ljobs)
    if ljobs:
        paths.LEARN_RESULTS_FILE.unlink(missing_ok=True)
        if _run_claude(_fill(LEARNER_PROMPT_TEMPLATE), LEARNER_TOOLS):
            summary["learned"] = draft_learn.apply_learnings(
                Store.load(), _read_json(paths.LEARN_RESULTS_FILE)).get("learned", 0)

    return summary


def main() -> int:
    print(f"check_replies: {run()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
