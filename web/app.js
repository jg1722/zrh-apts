const $ = (s, r=document) => r.querySelector(s);
const api = (p, opts) => fetch(p, opts).then(r => r.json());
let TAB = "triage";
let LAST = [];

const BUCKET_LABEL = { A: "🔥 Bucket A", B: "⭐ Bucket B", null: "🤔 Maybe", "": "🤔 Maybe" };
const CHIPS = [["too_expensive","Too €€"],["dated","Dated K/B"],["wrong_area","Wrong area"],
  ["too_far","Too far"],["too_small","Too small"],["ugly_building","Ugly bldg"],["bad_layout","Bad layout"]];

function chf(n){ return n ? "CHF " + Number(n).toLocaleString("de-CH") : "—"; }

// availability date -> "1 Sep 2026"; passes through non-ISO values (e.g. "ab sofort")
function fmtDate(iso){
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? esc(iso)
    : d.toLocaleDateString("en-GB", {day:"numeric", month:"short", year:"numeric"});
}

// escape text from scraped listings before putting it in innerHTML
function esc(s){
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function tabFilter(l){
  if (TAB === "replied") return !!(l.reply_candidate || l.reply || l.confirmation || l.status === "replied");
  if (TAB === "contacted") return l.status === "contacted" || l.decision === "outreach";
  if (TAB === "declined") return l.decision === "deprioritized";
  if (TAB === "triage") return !l.decision;
  return true; // all
}

function params(){
  const p = new URLSearchParams();
  for (const k of ["q","bucket","rent_max","score_min"]) { const v = $("#"+k).value; if (v) p.set(k, v); }
  if ($("#include_rejected").checked) p.set("include_rejected","1");
  return p.toString();
}

async function load(){
  if (TAB === "learning") return renderLearning();
  if (TAB === "draftstyle") return renderDraftStyle();
  const data = await api("/api/listings?" + params());
  if (!data || !data.listings){ $("#content").innerHTML = '<p class="muted">Error loading listings.</p>'; return; }
  LAST = data.listings;
  const rows = data.listings.filter(tabFilter);
  const content = $("#content");
  content.innerHTML = "";
  if (TAB === "replied"){ renderReplied(rows, content); refreshReplyBadge(); return; }
  const groups = {};
  for (const l of rows){ const k = l.bucket || ""; (groups[k] ||= []).push(l); }
  const order = ["A","B",""];
  if (!rows.length){ content.innerHTML = '<p class="muted">Nothing here.</p>'; refreshReplyBadge(); return; }
  for (const k of order){
    if (!groups[k]) continue;
    const sec = document.createElement("div"); sec.className = "section";
    sec.innerHTML = `<h2>${BUCKET_LABEL[k]} <span class="muted">${groups[k].length}</span></h2>`;
    const grid = document.createElement("div"); grid.className = "grid";
    groups[k].forEach(l => grid.appendChild(card(l)));
    sec.appendChild(grid); content.appendChild(sec);
  }
  refreshReplyBadge();
}

function refreshReplyBadge(){
  const b = $("#reply-badge");
  if (!b) return;
  const n = LAST.filter(l => l.reply_candidate && !l.reply).length;
  b.textContent = n ? String(n) : "";
  b.hidden = !n;
}

function renderReplied(rows, content){
  content.innerHTML = "";
  const pending = rows.filter(l => l.reply_candidate && !l.reply);
  const done = rows.filter(l => l.reply);
  const delivered = rows.filter(l => l.confirmation && !l.reply && !l.reply_candidate);
  if (!pending.length && !done.length && !delivered.length){
    content.innerHTML = '<p class="muted">No replies yet.</p>'; return;
  }
  const sec = (title, list) => {
    if (!list.length) return;
    const s = document.createElement("div"); s.className = "section";
    s.innerHTML = `<h2>${title} <span class="muted">${list.length}</span></h2>`;
    const g = document.createElement("div"); g.className = "grid";
    list.forEach(l => g.appendChild(card(l)));
    s.appendChild(g); content.appendChild(s);
  };
  sec("📨 Needs confirm", pending);
  sec("✓ Confirmed", done);
  sec("✅ Delivered (awaiting reply)", delivered);
}

function condTags(l){
  const t = [];
  if (l.cluster_size) t.push(`<span class="tag warn">⚠ ${l.cluster_size}× mass-listing</span>`);
  const c = (k,label) => { const v=l["condition_"+k]; if(v==="modern")t.push(`<span class="tag">modern ${label}</span>`); else if(v==="dated")t.push(`<span class="tag warn">dated ${label}</span>`); };
  c("kitchen","kitchen"); c("bath","bath");
  if (l.has_balcony) t.push('<span class="tag">balcony</span>');
  if (l.has_parking) t.push('<span class="tag">parking</span>');
  if (l.hood_category) t.push(`<span class="tag hood">${esc(l.hood_category)}</span>`);
  return t.join("");
}

function card(l){
  const el = document.createElement("div"); el.className = "card"; el.dataset.id = l.id;
  const chan = l.outreach_channel === "email" ? "email" : "form";
  const rent = l.rent_net || l.rent_gross;
  // Heading: prefer a clean location. Flatfox auto-titles embed a price
  // ("8810 Horgen - CHF 2'680 incl. utilities per month") that can disagree with
  // the meta rent (verify_listings refreshes rent_gross but not the title), so we
  // only fall back to the raw title when there's no street or locality at all.
  const street = l.street || [l.zipcode, l.city].filter(Boolean).join(" ") || l.title || "—";
  el.innerHTML = `
    <div class="thumb" style="background-image:url('/api/photo/${l.id}/0')">
      <span class="chan ${chan==='email'?'email':''}">${chan}</span>
      <span class="score">${l.score ?? ""}</span>
    </div>
    <div class="body">
      <h4>${esc(street)}</h4>
      <p class="meta">${l.rooms ?? "?"} rm · ${l.size_sqm ?? "?"} m² · ${chf(rent)}${l.rent_net?" net":""}<br>
        ${esc(l.hood_name || l.hood_category || "")} ${l.transit_min?("· 🚆 "+esc(l.transit_min)+" min"):""}${l.availability?(" · 📅 "+fmtDate(l.availability)):""}</p>
      <div class="tags">${condTags(l)}</div>
      <div class="state"></div>
    </div>`;
  renderState(el, l);
  el.querySelector(".thumb").onclick = () => window.open(l.url, "_blank");
  return el;
}

// "request delivered" line from the auto-captured form-submission receipt
function delivLine(l){
  const c = l.confirmation;
  if (!c) return "";
  return `<div class="deliv">📬 Request delivered${c.received_at ? (" · " + fmtDate(c.received_at)) : ""}` +
    `${c.gmail_link ? ` · <a class="link" href="${esc(c.gmail_link)}" target="_blank" rel="noopener">receipt</a>` : ""}</div>`;
}

function draftLine(r){
  return (r && r.draft)
    ? `<div class="deliv">✎ Draft ready in Gmail · <a class="link" href="${esc(r.gmail_link || "#")}" target="_blank" rel="noopener">open thread</a></div>`
    : "";
}

// automated/personal tag for a reply object
function replyTag(r){
  return r.automated ? '<span class="rtag auto">automated</span>'
                     : '<span class="rtag pers">personal</span>';
}

// summary + next-steps block, shown on both candidate and confirmed reply cards
function replyBody(r){
  const sum = r.summary || r.snippet || "";
  const ns = (r.next_steps || "").trim();
  const next = (ns && !/^(none|n\/a|-)$/i.test(ns)) ? `<div class="next">→ ${esc(ns)}</div>` : "";
  return `<div class="snip">${esc(sum)}</div>${next}`;
}

function renderState(el, l){
  const box = el.querySelector(".state");
  if (l.reply){
    box.innerHTML = `${delivLine(l)}${draftLine(l.reply)}<div class="done">✓ Replied${l.reply.received_at ? (" · " + fmtDate(l.reply.received_at)) : ""} ${replyTag(l.reply)}</div>
      ${replyBody(l.reply)}
      <a class="link" href="${esc(l.reply.gmail_link || "#")}" target="_blank" rel="noopener">open in Gmail</a>`;
    return;
  }
  if (l.reply_candidate){
    const c = l.reply_candidate;
    box.innerHTML = `<div class="reply-cand">
        <div class="meta"><b>Reply found</b>${c.from ? (" · " + esc(c.from)) : ""} ${replyTag(c)} — confirm?</div>
        ${replyBody(c)}
        <a class="link" href="${esc(c.gmail_link || "#")}" target="_blank" rel="noopener">open in Gmail</a>
        <div class="acts" style="margin-top:6px">
          <div class="btn ghost act-noreply" style="flex:0 0 42%">✗ Not a match</div>
          <div class="btn primary act-confirmreply">✓ Confirm reply</div></div></div>`;
    box.querySelector(".reply-cand").insertAdjacentHTML("afterbegin", delivLine(l));
    box.querySelector(".reply-cand").insertAdjacentHTML("beforeend", draftLine(l.reply_candidate));
    box.querySelector(".act-confirmreply").onclick = () => confirmReply(l, el);
    box.querySelector(".act-noreply").onclick = () => rejectReply(l, el);
    return;
  }
  if (l.decision === "outreach" || l.status === "contacted"){
    box.innerHTML = `${delivLine(l)}<div class="done">✓ Reached out${l.decision_at?(" · "+l.decision_at.slice(0,10)):""}</div>
      <span class="link act-undo">undo</span>`;
    box.querySelector(".act-undo").onclick = () => reset(l, el);
    return;
  }
  if (l.decision === "deprioritized"){
    box.innerHTML = `<div class="muted">Declined — ${(l.decline_reasons||[]).map(esc).join(", ")||"no reason"}</div>
      <span class="link act-undo">undo</span>`;
    box.querySelector(".act-undo").onclick = () => reset(l, el);
    return;
  }
  box.innerHTML = `<div class="acts">
      <div class="btn primary act-reach">Reach out</div>
      <div class="btn ghost act-decline">Decline</div></div>`;
  box.querySelector(".act-reach").onclick = () => reachOut(l, el);
  box.querySelector(".act-decline").onclick = () => showDecline(l, el);
}

async function reachOut(l, el){
  // Step 1: fetch the pre-written message, SHOW it (editable), copy to clipboard,
  // and open the form/Gmail. Does NOT mark anything — the listing only counts as
  // reached out after the explicit confirm below.
  const msg = await api("/api/message/" + l.id);
  let copied = true;
  const fullText = `${msg.subject}\n\n${msg.body}`;
  try { await navigator.clipboard.writeText(fullText); } catch(e){ copied = false; }
  const isEmail = msg.channel === "email" && msg.email;
  const target = isEmail
    ? `https://mail.google.com/mail/?view=cm&to=${encodeURIComponent(msg.email)}`
      + `&su=${encodeURIComponent(msg.subject)}&body=${encodeURIComponent(msg.body)}`
    : l.url;
  const attr = s => String(s==null?"":s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  const win = window.open(target, "_blank");
  toast(!win ? "Popup blocked — message shown below; open the listing manually"
             : (copied ? "Message copied · " + (isEmail?"Gmail":"form") + " opened"
                       : "Opened — copy the text below manually"));
  // Step 2: show the pre-formulated text so it can be read/edited/re-copied,
  // then the pending confirm. Marking only happens on the explicit click.
  const box = el.querySelector(".state");
  box.innerHTML = `
    <div class="meta">${isEmail?"Email":"Contact-form"} message${msg.language?(" · "+attr(msg.language)):""} — paste into the ${isEmail?"draft":"form"}:</div>
    <input class="msg-subj" value="${attr(msg.subject)}" />
    <textarea class="msg-body" rows="8"></textarea>
    <div class="acts" style="margin:6px 0">
      <div class="btn act-copy">⧉ Copy again</div>
      <a class="btn act-open" href="${attr(target)}" target="_blank" rel="noopener">↗ Open ${isEmail?"Gmail":"listing"}</a>
    </div>
    <div class="meta">Did you reach out?</div>
    <div class="acts"><div class="btn ghost act-cancel" style="flex:0 0 40%">Not yet</div>
      <div class="btn primary act-confirm">✓ Mark reached out</div></div>`;
  box.querySelector(".msg-body").value = msg.body || "";
  box.querySelector(".act-copy").onclick = async () => {
    const s = box.querySelector(".msg-subj").value, b = box.querySelector(".msg-body").value;
    try { await navigator.clipboard.writeText(`${s}\n\n${b}`); toast("Copied"); }
    catch(e){ toast("Clipboard blocked — select & ⌘C"); }
  };
  box.querySelector(".act-cancel").onclick = () => renderState(el, l);
  box.querySelector(".act-confirm").onclick = () => confirmReached(l, el);
}

async function confirmReached(l, el){
  await api("/api/reach-out/" + l.id, {method:"POST"});
  l.decision = "outreach"; l.status = "contacted"; l.decision_at = new Date().toISOString();
  renderState(el, l); toast("Marked reached out");
}

async function confirmReply(l, el){
  await api("/api/reply/confirm/" + l.id, {method:"POST"});
  l.reply = Object.assign({confirmed_at: new Date().toISOString()}, l.reply_candidate);
  l.reply_candidate = null; l.status = "replied";
  if (TAB === "replied") load(); else { renderState(el, l); refreshReplyBadge(); }
  toast("Reply confirmed");
}

async function rejectReply(l, el){
  await api("/api/reply/reject/" + l.id, {method:"POST"});
  l.reply_candidate = null;
  if (TAB === "replied") load(); else { renderState(el, l); refreshReplyBadge(); }
  toast("Dismissed");
}

async function reset(l, el){
  await api("/api/reset/" + l.id, {method:"POST"});
  l.decision = null; l.status = "new"; l.decline_reasons = null;
  renderState(el, l); toast("Undone");
}

function toast(msg){ const t=$("#toast"); t.textContent=msg; t.hidden=false; setTimeout(()=>t.hidden=true, 2200); }

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach(x=>x.classList.remove("active"));
  b.classList.add("active"); TAB = b.dataset.tab; load();
});
["q","bucket","rent_max","score_min","include_rejected"].forEach(id =>
  $("#"+id).addEventListener("input", () => load()));

async function renderLearning(){
  const s = await api("/api/learning");
  const w = (o)=>o?Object.entries(o).map(([k,v])=>`${k}: ${v}`).join(" · "):"—";
  const rows = Object.keys(s.baseline.weights).map(k =>
    `<tr><td>${k}</td><td>${s.baseline.weights[k]}</td>
       <td>${s.learned.weights ? s.learned.weights[k] : s.baseline.weights[k]}</td></tr>`).join("");
  const counts = Object.entries(s.dimension_counts||{}).map(([k,v])=>`${k}: ${v}`).join(" · ") || "none yet";
  $("#content").innerHTML = `
    <div id="learning">
      <h2>Taste / Learning ${s.paused?'<span class="tag warn">paused</span>':''}</h2>
      <p class="muted">${s.cold_start_remaining>0
        ? `Cold start: ${s.cold_start_remaining} more decision(s) before learning kicks in.`
        : 'Learning active. Scores below reflect your decisions.'}</p>
      <p>Decline signals → ${counts}</p>
      <h3>Weights (baseline → learned)</h3>
      <table><tr><th>dimension</th><th>baseline</th><th>learned</th></tr>${rows}</table>
      <h3>Hood preferences (learned)</h3>
      <p class="muted">${w(s.learned.hood_preferences)}</p>
      <p>Price ceiling (value_worst_chf_m2): baseline ${s.baseline.value_worst_chf_m2}
         → learned ${s.learned.value_worst_chf_m2 ?? s.baseline.value_worst_chf_m2}</p>
      <div class="acts" style="max-width:360px;margin-top:14px">
        <div class="btn act-pause">${s.paused?'Resume':'Pause'} learning</div>
        <div class="btn danger act-reset">Reset learning</div>
      </div>
    </div>`;
  $(".act-pause").onclick = async () => { await api("/api/learning/"+(s.paused?"resume":"pause"),{method:"POST"}); renderLearning(); };
  $(".act-reset").onclick = async () => { if(confirm("Reset all learned preferences?")){ await api("/api/learning/reset",{method:"POST"}); renderLearning(); } };
}

async function renderDraftStyle(){
  const s = await api("/api/draft-style");
  const items = (s.notes || []).map(n =>
    `<li>${esc(n.text)} ${n.from ? `<span class="muted">· ${esc(n.from)}</span>` : ""}</li>`).join("")
    || '<li class="muted">No lessons yet — they appear after you edit & send drafts.</li>';
  $("#content").innerHTML = `
    <div id="learning">
      <h2>Draft style ✎ ${s.paused ? '<span class="tag warn">paused</span>' : ''}</h2>
      <p class="muted">Lessons learned from how you edit drafts before sending — applied to every new draft.</p>
      <ul>${items}</ul>
      <div class="acts" style="max-width:360px;margin-top:14px">
        <div class="btn act-pause">${s.paused ? 'Resume' : 'Pause'} learning</div>
        <div class="btn danger act-reset">Reset</div>
      </div>
    </div>`;
  $(".act-pause").onclick = async () => { await api("/api/draft-style/" + (s.paused ? "resume" : "pause"), {method:"POST"}); renderDraftStyle(); };
  $(".act-reset").onclick = async () => { if (confirm("Reset all learned draft lessons?")) { await api("/api/draft-style/reset", {method:"POST"}); renderDraftStyle(); } };
}

function showDecline(l, el){
  const box = el.querySelector(".state");
  const chips = CHIPS.map(([k,label]) => `<span class="chip" data-k="${k}">${label}</span>`).join("");
  box.innerHTML = `<p class="meta">Why are you passing?</p>
    <div class="chips">${chips}</div>
    <input class="note" placeholder="optional note…">
    <div class="acts"><div class="btn ghost act-cancel" style="flex:0 0 38%">Cancel</div>
      <div class="btn danger act-confirm">Confirm decline</div></div>`;
  const picked = new Set();
  box.querySelectorAll(".chip").forEach(c => c.onclick = () => {
    c.classList.toggle("on"); picked.has(c.dataset.k) ? picked.delete(c.dataset.k) : picked.add(c.dataset.k);
  });
  box.querySelector(".act-cancel").onclick = () => renderState(el, l);
  box.querySelector(".act-confirm").onclick = async () => {
    const note = box.querySelector(".note").value;
    await api("/api/decline/" + l.id, {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({reasons:[...picked], note})});
    l.decision = "deprioritized"; l.decline_reasons = [...picked];
    renderState(el, l); toast("Declined — taste updated");
  };
}

let checkPoll = null;
async function startCheck(){
  const btn = $("#check-now");
  const r = await api("/api/check-replies", {method:"POST"});
  if (r && r.error === "already running"){ toast("Already checking…"); }
  btn.disabled = true; btn.textContent = "Checking…";
  if (checkPoll) clearInterval(checkPoll);
  checkPoll = setInterval(pollCheck, 3000);
}
async function pollCheck(){
  const s = await api("/api/check-replies/status");
  if (!s || s.running) return;
  clearInterval(checkPoll); checkPoll = null;
  const btn = $("#check-now"); btn.disabled = false; btn.textContent = "⟳ Check now";
  const m = s.summary || {};
  toast(s.error ? ("Check failed: " + s.error)
                : ("Checked · " + (m.drafted ? (m.drafted + " new draft(s)") : "up to date")));
  load();
}
$("#check-now").onclick = startCheck;

load();
