"""
main.py — GTM Prospecting Pipeline
FastAPI app that turns a natural language query into HubSpot contacts.

Run: uvicorn main:app --reload --port 8000

POST /prospect
  Body: {"query": "Give me 50 cybersecurity prospects, 50-500 employees, US"}
  Returns: summary + details of what was created/rejected
"""

import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field

from query_parser import parse_prospect_query
from hunter_client import search_prospects
from scorer import score_prospects
from hubspot_client import create_contacts_batch

app = FastAPI(
    title="GTM Prospecting Pipeline",
    description="Natural language → Apollo → Claude scoring → HubSpot contacts",
    version="1.0.0",
)


class ProspectRequest(BaseModel):
    query: str = Field(
        ...,
        example="Give me 50 cybersecurity prospects, 50-500 employees, US",
        description="Natural language prospecting request",
    )
    icp_profile: str = Field(
        default="",
        description="Optional ICP definition. Claude uses this for more precise scoring.",
    )
    min_score: int = Field(
        default=60,
        ge=0,
        le=100,
        description="Minimum fit score (0-100) to accept a prospect. Default 60.",
    )


class ProspectResponse(BaseModel):
    summary: str
    created: int
    skipped_existing: int
    skipped_no_email: int
    rejected_count: int
    filters_applied: dict
    domain_log: list
    rejected_prospects: list
    errors: list


@app.post("/prospect", response_model=ProspectResponse)
def run_pipeline(req: ProspectRequest):
    """
    Full pipeline:
      1. Parse NL query → Apollo filters
      2. Fetch prospects from Apollo
      3. Score each with Claude
      4. Create accepted contacts in HubSpot
      5. Return summary
    """

    # ── Step 1: Parse natural language into Apollo filters ──────────────────
    try:
        filters = parse_prospect_query(req.query)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse query: {e}")

    count = max(1, min(int(filters.get("count", 25)), 100))

    # ── Step 2: Fetch from Hunter.io ────────────────────────────────────────
    try:
        raw_prospects, domain_log = search_prospects(
            target_domains=filters.get("target_domains", []),
            person_titles=filters.get("person_titles", []),
            count=count,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Hunter API error: {e}")

    if not raw_prospects:
        raise HTTPException(
            status_code=404,
            detail=f"Hunter returned 0 contacts across {len(filters.get('target_domains', []))} domains. Check your HUNTER_API_KEY or try a different query.",
        )

    # ── Step 3: Score prospects with Claude ─────────────────────────────────
    try:
        scored = score_prospects(raw_prospects, req.query, icp_profile=req.icp_profile)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Scoring error: {e}")

    # ── Step 4: Split accepted vs rejected ──────────────────────────────────
    accepted = [p for p in scored if p["fit_score"] >= req.min_score]
    rejected = [p for p in scored if p["fit_score"] < req.min_score]

    # ── Step 5: Create contacts in HubSpot ─────────────────────────────────
    try:
        hs_result = create_contacts_batch(accepted)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")

    # ── Build response ──────────────────────────────────────────────────────
    total_rejected = len(rejected)
    summary = (
        f"Created {hs_result['created']} contacts in HubSpot"
        f" + {total_rejected} rejected (fit score < {req.min_score})"
    )
    if hs_result["skipped_existing"]:
        summary += f" + {hs_result['skipped_existing']} already existed"
    if hs_result["skipped_no_email"]:
        summary += f" + {hs_result['skipped_no_email']} skipped (no email)"

    return ProspectResponse(
        summary=summary,
        created=hs_result["created"],
        skipped_existing=hs_result["skipped_existing"],
        skipped_no_email=hs_result["skipped_no_email"],
        rejected_count=total_rejected,
        filters_applied=filters,
        domain_log=domain_log,
        rejected_prospects=[
            {
                "name": f"{p['first_name']} {p['last_name']}".strip(),
                "title": p.get("title", ""),
                "company": p.get("company", ""),
                "score": p["fit_score"],
                "reason": p.get("fit_reason", ""),
            }
            for p in rejected
        ],
        errors=hs_result["errors"],
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Prospecting Pipeline</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f9fafb;
      color: #111827;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
    }

    /* ── Header ── */
    header {
      width: 100%;
      padding: 16px 32px;
      background: #fff;
      border-bottom: 1px solid #e5e7eb;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
    }
    .logo {
      width: 28px; height: 28px;
      background: #16a34a;
      border-radius: 7px;
      display: flex; align-items: center; justify-content: center;
      font-size: 14px;
    }
    header h1 { font-size: 0.95rem; font-weight: 700; color: #111827; letter-spacing: -0.01em; }
    header .pill {
      font-size: 0.68rem;
      background: #f0fdf4;
      color: #16a34a;
      border: 1px solid #bbf7d0;
      padding: 2px 9px;
      border-radius: 99px;
      font-weight: 600;
      letter-spacing: 0.03em;
    }

    /* ── Page body ── */
    .page { width: 100%; max-width: 720px; padding: 36px 20px 80px; display: flex; flex-direction: column; gap: 20px; }

    /* ── Chat box card ── */
    .chat-card {
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }

    .chat-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 18px;
      cursor: pointer;
      user-select: none;
      border-bottom: 1px solid #e5e7eb;
      background: #fff;
    }
    .chat-header:hover { background: #f9fafb; }
    .chat-header-left { display: flex; align-items: center; gap: 8px; }
    .chat-title { font-size: 0.875rem; font-weight: 600; color: #111827; }
    .chat-sub { font-size: 0.75rem; color: #9ca3af; }
    .toggle-btn {
      font-size: 0.75rem;
      color: #6b7280;
      background: #f3f4f6;
      border: none;
      border-radius: 6px;
      padding: 4px 10px;
      cursor: pointer;
      font-weight: 500;
    }
    .toggle-btn:hover { background: #e5e7eb; }

    .chat-body { padding: 18px; display: flex; flex-direction: column; gap: 14px; }
    .chat-body.hidden { display: none; }

    /* Fields */
    .field label {
      display: block;
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: #6b7280;
      margin-bottom: 6px;
    }
    .field label .opt { font-weight: 400; text-transform: none; color: #d1d5db; letter-spacing: 0; }

    textarea, input[type=number] {
      width: 100%;
      background: #fff;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      color: #111827;
      font-family: inherit;
      font-size: 0.875rem;
      padding: 10px 13px;
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
      resize: vertical;
      line-height: 1.5;
    }
    textarea { min-height: 80px; }
    textarea.short { min-height: 58px; }
    textarea:focus, input[type=number]:focus {
      border-color: #16a34a;
      box-shadow: 0 0 0 3px rgba(22,163,74,0.1);
    }
    textarea::placeholder, input::placeholder { color: #d1d5db; }

    .bottom-row { display: flex; gap: 10px; align-items: flex-end; }
    .score-field { flex: 0 0 110px; }
    .score-field label { font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #6b7280; display: block; margin-bottom: 6px; }
    .score-field input { padding: 9px 12px; }

    button#btn {
      flex: 1;
      background: #16a34a;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 11px 18px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
      letter-spacing: 0.01em;
    }
    button#btn:hover { background: #15803d; }
    button#btn:disabled { background: #86efac; cursor: not-allowed; }

    /* ── Pipeline steps ── */
    .steps-card {
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      padding: 18px 20px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .steps-title { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #9ca3af; margin-bottom: 14px; }
    .steps-row { display: flex; align-items: center; gap: 0; }
    .step-item { display: flex; flex-direction: column; align-items: center; flex: 1; position: relative; }
    .step-item:not(:last-child)::after {
      content: '';
      position: absolute;
      top: 14px;
      left: 55%;
      width: 90%;
      height: 1px;
      background: #e5e7eb;
      z-index: 0;
    }
    .step-item:not(:last-child).done-line::after { background: #86efac; }
    .step-dot {
      width: 28px; height: 28px;
      border-radius: 50%;
      background: #f3f4f6;
      border: 2px solid #e5e7eb;
      display: flex; align-items: center; justify-content: center;
      font-size: 11px;
      font-weight: 700;
      color: #9ca3af;
      z-index: 1;
      transition: all 0.25s;
      position: relative;
    }
    .step-dot.active { background: #f0fdf4; border-color: #16a34a; color: #16a34a; }
    .step-dot.done   { background: #16a34a; border-color: #16a34a; color: #fff; }
    .step-dot.error  { background: #fef2f2; border-color: #ef4444; color: #ef4444; }
    .step-lbl { font-size: 0.68rem; color: #9ca3af; margin-top: 6px; text-align: center; line-height: 1.3; max-width: 80px; transition: color 0.2s; }
    .step-lbl.active { color: #16a34a; font-weight: 600; }
    .step-lbl.done   { color: #15803d; }
    .step-lbl.error  { color: #ef4444; }

    /* ── Results ── */
    .results { display: flex; flex-direction: column; gap: 16px; }

    .empty-card {
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      padding: 52px 20px;
      text-align: center;
      color: #d1d5db;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .empty-card .ei { font-size: 2rem; margin-bottom: 10px; }
    .empty-card p { font-size: 0.875rem; }

    /* Stat cards */
    .stat-row { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; }
    .stat-card {
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 16px 18px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .stat-label { font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #9ca3af; margin-bottom: 6px; }
    .stat-value { font-size: 2rem; font-weight: 800; line-height: 1; }
    .stat-card.green .stat-value { color: #16a34a; }
    .stat-card.red   .stat-value { color: #ef4444; }
    .stat-card.gray  .stat-value { color: #6b7280; }

    /* Section card */
    .section-card {
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 16px 18px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .section-head { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #9ca3af; margin-bottom: 12px; }

    /* Tags */
    .tag-list { display: flex; flex-wrap: wrap; gap: 6px; }
    .tag { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 4px 10px; font-size: 0.78rem; color: #374151; }
    .tag.icp { background: #f0fdf4; border-color: #bbf7d0; color: #15803d; }

    /* Domain grid */
    .domain-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px,1fr)); gap: 6px; }
    .domain-chip {
      background: #f9fafb;
      border: 1px solid #e5e7eb;
      border-radius: 7px;
      padding: 7px 11px;
      display: flex; justify-content: space-between; align-items: center;
      font-size: 0.78rem;
    }
    .domain-chip .d-name { color: #374151; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .domain-chip .d-count { font-size: 0.7rem; color: #9ca3af; flex-shrink: 0; margin-left: 6px; }
    .domain-chip.ok { border-color: #bbf7d0; background: #f0fdf4; }
    .domain-chip.ok .d-name { color: #15803d; }
    .domain-chip.ok .d-count { color: #16a34a; font-weight: 600; }

    /* Table */
    .table-wrap { border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; }
    table { width: 100%; border-collapse: collapse; font-size: 0.83rem; }
    th { text-align: left; padding: 9px 14px; background: #f9fafb; color: #9ca3af; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; border-bottom: 1px solid #e5e7eb; }
    td { padding: 10px 14px; border-bottom: 1px solid #f3f4f6; color: #374151; vertical-align: top; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: #f9fafb; }

    .score-pill { display: inline-block; padding: 2px 9px; border-radius: 99px; font-size: 0.72rem; font-weight: 700; }
    .score-high { background: #dcfce7; color: #15803d; }
    .score-mid  { background: #fef9c3; color: #92400e; }
    .score-low  { background: #fee2e2; color: #b91c1c; }

    .error-box { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 14px 16px; font-size: 0.85rem; color: #b91c1c; line-height: 1.6; }

    .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.5); border-top-color: #fff; border-radius: 50%; animation: spin 0.7s linear infinite; vertical-align: middle; margin-right: 6px; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>

<header>
  <div class="logo">🎯</div>
  <h1>Prospecting Pipeline</h1>
  <span class="pill">Hunter · Claude · HubSpot</span>
</header>

<div class="page">

  <!-- Chat box -->
  <div class="chat-card">
    <div class="chat-header" onclick="toggleChat()">
      <div class="chat-header-left">
        <span style="font-size:1.1rem">💬</span>
        <div>
          <div class="chat-title">New Prospecting Run</div>
          <div class="chat-sub">Describe who you're looking for</div>
        </div>
      </div>
      <button class="toggle-btn" id="toggle-label">Hide</button>
    </div>

    <div class="chat-body" id="chat-body">
      <div class="field">
        <label>Prospecting Request</label>
        <textarea id="query" placeholder="Give me 50 cybersecurity prospects, 50-500 employees, US"></textarea>
      </div>

      <div class="field">
        <label>ICP Profile <span class="opt">— optional, improves scoring accuracy</span></label>
        <textarea id="icp" class="short" placeholder="B2B SaaS, 50-500 employees, US, security buyer — titles: CISO / VP Eng / IT Director"></textarea>
      </div>

      <div class="bottom-row">
        <div class="score-field">
          <label>Min Score</label>
          <input type="number" id="score" value="60" min="0" max="100"/>
        </div>
        <button id="btn" onclick="run()">▶ Run Pipeline</button>
      </div>
    </div>
  </div>

  <!-- Pipeline steps -->
  <div class="steps-card">
    <div class="steps-title">Pipeline Status</div>
    <div class="steps-row">
      <div class="step-item" id="si1">
        <div class="step-dot" id="s1">1</div>
        <div class="step-lbl" id="sl1">Parse Query</div>
      </div>
      <div class="step-item" id="si2">
        <div class="step-dot" id="s2">2</div>
        <div class="step-lbl" id="sl2">Hunter Fetch</div>
      </div>
      <div class="step-item" id="si3">
        <div class="step-dot" id="s3">3</div>
        <div class="step-lbl" id="sl3">Claude Score</div>
      </div>
      <div class="step-item" id="si4">
        <div class="step-dot" id="s4">4</div>
        <div class="step-lbl" id="sl4">HubSpot Sync</div>
      </div>
    </div>
  </div>

  <!-- Results -->
  <div class="results" id="results">
    <div class="empty-card">
      <div class="ei">📋</div>
      <p>Results will appear here after you run the pipeline</p>
    </div>
  </div>

</div>

<script>
  let chatVisible = true;

  function toggleChat() {
    chatVisible = !chatVisible;
    document.getElementById('chat-body').classList.toggle('hidden', !chatVisible);
    document.getElementById('toggle-label').textContent = chatVisible ? 'Hide' : 'Show';
  }

  function setStep(n, state) {
    document.getElementById('s' + n).className = 'step-dot ' + state;
    document.getElementById('sl' + n).className = 'step-lbl ' + state;
  }

  function resetSteps() {
    for (let i = 1; i <= 4; i++) setStep(i, '');
  }

  function scoreClass(s) {
    return s >= 70 ? 'score-high' : s >= 45 ? 'score-mid' : 'score-low';
  }

  async function run() {
    const query = document.getElementById('query').value.trim();
    if (!query) { document.getElementById('query').focus(); return; }

    const btn       = document.getElementById('btn');
    const results   = document.getElementById('results');
    const minScore  = parseInt(document.getElementById('score').value) || 60;
    const icpProfile = document.getElementById('icp').value.trim();

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Running…';
    resetSteps();
    setStep(1, 'active');
    results.innerHTML = '';

    // Animate steps while waiting
    let stepTimer = 2;
    const stepInterval = setInterval(() => {
      if (stepTimer <= 4) {
        setStep(stepTimer - 1, 'done');
        setStep(stepTimer, 'active');
        stepTimer++;
      }
    }, 5000);

    try {
      const res = await fetch('/prospect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, icp_profile: icpProfile, min_score: minScore })
      });

      clearInterval(stepInterval);
      const data = await res.json();

      if (!res.ok) {
        resetSteps();
        setStep(1, 'error');
        results.innerHTML = `<div class="error-box">❌ ${data.detail || JSON.stringify(data)}</div>`;
        return;
      }

      for (let i = 1; i <= 4; i++) setStep(i, 'done');

      // Auto-collapse chat after success
      if (chatVisible) toggleChat();

      const f = data.filters_applied || {};
      let html = '';

      // Stat cards
      html += `<div class="stat-row">
        <div class="stat-card green"><div class="stat-label">Created in HubSpot</div><div class="stat-value">${data.created}</div></div>
        <div class="stat-card red"><div class="stat-label">Rejected (low fit)</div><div class="stat-value">${data.rejected_count}</div></div>
        <div class="stat-card gray"><div class="stat-label">Skipped / Existing</div><div class="stat-value">${(data.skipped_existing||0)+(data.skipped_no_email||0)}</div></div>
      </div>`;

      // Filters
      html += `<div class="section-card"><div class="section-head">Filters Applied</div><div class="tag-list">`;
      if (f.keywords)          html += `<span class="tag">🔍 ${f.keywords}</span>`;
      if (f.locations?.length) html += f.locations.map(l=>`<span class="tag">📍 ${l}</span>`).join('');
      if (f.employee_min||f.employee_max) html += `<span class="tag">👥 ${f.employee_min||1}–${f.employee_max||'∞'} employees</span>`;
      if (f.count)             html += `<span class="tag">🎯 ${f.count} requested</span>`;
      if (icpProfile)          html += `<span class="tag icp">✦ ICP profile applied</span>`;
      html += `</div></div>`;

      // Domain log
      if (data.domain_log?.length) {
        const total = data.domain_log.reduce((s,d)=>s+d.found,0);
        html += `<div class="section-card"><div class="section-head">Domains Searched — ${total} contacts pulled</div><div class="domain-grid">`;
        for (const d of data.domain_log) {
          html += `<div class="domain-chip ${d.found>0?'ok':''}">
            <span class="d-name">${d.domain}</span>
            <span class="d-count">${d.found>0?d.found+' found':d.status}</span>
          </div>`;
        }
        html += `</div></div>`;
      }

      // Rejected table
      if (data.rejected_prospects?.length) {
        html += `<div class="section-card"><div class="section-head">Rejected — Score &lt; ${minScore}</div>
          <div class="table-wrap"><table><thead><tr>
            <th>Name</th><th>Title</th><th>Company</th><th>Score</th><th>Reason</th>
          </tr></thead><tbody>`;
        for (const p of data.rejected_prospects) {
          html += `<tr>
            <td>${p.name||'—'}</td><td>${p.title||'—'}</td><td>${p.company||'—'}</td>
            <td><span class="score-pill ${scoreClass(p.score)}">${p.score}</span></td>
            <td>${p.reason||'—'}</td>
          </tr>`;
        }
        html += `</tbody></table></div></div>`;
      }

      // Errors
      if (data.errors?.length) {
        html += `<div class="error-box">⚠️ HubSpot errors:<br>${data.errors.map(e=>`${e.name||e.email}: ${e.error}`).join('<br>')}</div>`;
      }

      results.innerHTML = html;

    } catch (err) {
      clearInterval(stepInterval);
      resetSteps();
      setStep(1, 'error');
      results.innerHTML = `<div class="error-box">❌ Request failed: ${err.message}</div>`;
    } finally {
      btn.disabled = false;
      btn.innerHTML = '▶ Run Pipeline';
    }
  }

  document.getElementById('query').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run();
  });
</script>
</body>
</html>""")

