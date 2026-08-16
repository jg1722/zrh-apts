# Workflow 5 — Outreach (draft-only, user-approved)

**Trigger:** the user says `outreach <id>` (e.g. `outreach flatfox-123456`) after
reading the digest. This step is NEVER part of the automated morning run.

## Decision gate (what keeps a listing on / off the digest)
The digest shows every undecided match in its bucket **every run** until the user
decides — it does NOT drop a listing just because it was seen before. There are
two decisions, each moving the listing to its own digest list:
- **outreach** — the moment the user says `outreach <id>`, record the decision so
  it leaves the buckets: `.venv/bin/python scripts/decide.py <id> outreach`.
  (Sending a first contact, i.e. status ≥ contacted, also counts as decided.)
- **deprioritize** — the user says `deprioritize <id>` / `skip <id>`:
  `.venv/bin/python scripts/decide.py <id> deprioritize --note "<why>"`.
- **reset** — `decide.py <id> reset` returns it to its bucket (undecided).

## Golden rule
`outreach.auto_send` in `config/criteria.yaml` is `false`. **Always draft into
Gmail and stop.** The user reviews and explicitly approves before anything sends.
Only if the user flips `auto_send: true` may sending happen without that pause.

## First contact
1. Load the listing details:
   ```
   .venv/bin/python scripts/show.py <id>
   ```
2. Find the landlord/agency contact. If the listing has no email, the listing
   `url` has the application form — tell the user (we don't auto-submit forms).
3. Draft the email using `templates/first_contact.md`:
   - German, formal "Sie", short and direct.
   - One sentence on who the user is (per their chosen intro / style file).
   - Mention a full Bewerbungsdossier is **ready on request** — do NOT attach it.
   - **Match the user's own application-email style** if a sample exists in
     `templates/` (mirror its wording and structure).
4. Create the draft via the **Gmail connector** (draft only). Show the user the
   draft (to, subject, body) and ask for approval.
5. On the user's approval to send → send via Gmail, then:
   ```
   .venv/bin/python scripts/set_status.py <id> contacted --note "first contact sent"
   ```

## On a landlord reply
1. Summarise the reply for the user (key points, any questions, any documents
   requested).
2. Mark progress:
   ```
   .venv/bin/python scripts/set_status.py <id> replied
   ```
3. Draft the response (`templates/reply_guidelines.md`):
   - Attach the dossier from `dossier/` (PDFs there).
   - Propose **2–3 concrete viewing slots**.
   - Answer any questions the landlord raised.
4. Draft into Gmail, show the user, wait for approval. On send →
   `set_status.py <id> viewing`.

## Status machine
`new → contacted → replied → viewing → closed | rejected`
Update it with `scripts/set_status.py` at each transition so the store stays
accurate and the digest dedup keeps working.

## Never
- Never send without approval (unless `auto_send: true`).
- Never attach the dossier on first contact.
- Never invent landlord contact details — if you can't find an address, say so.
