"""Build a self-contained browser UI for the frozen taxonomy review artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from pipeline.validation.taxonomy_adjudication import (
    SUPPLEMENT_SUBCATEGORIES,
    TaxonomyReviewArtifact,
)

_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Supplements taxonomy review</title>
  <style>
    :root { color-scheme: light; --ink:#172033; --muted:#667085; --line:#d8dee9;
      --brand:#3157d5; --brand-dark:#203d9c; --ok:#087f5b; --no:#c92a2a; --bg:#f4f6fa; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;
      color:var(--ink); background:var(--bg); }
    button,select,input,textarea { font:inherit; }
    button { cursor:pointer; }
    .top { position:sticky; top:0; z-index:5; color:white;
      background:linear-gradient(120deg,#172554,#3157d5); box-shadow:0 3px 18px #17255433; }
    .top-inner { max-width:1200px; margin:auto; padding:18px 24px; }
    h1 { margin:0 0 4px; font-size:clamp(21px,3vw,30px); }
    .subtitle { color:#dbe4ff; font-size:14px; }
    .progress { height:7px; margin-top:14px; overflow:hidden; border-radius:99px; background:#ffffff30; }
    .progress > div { height:100%; width:0; background:#9cffd9; transition:width .2s; }
    .layout { max-width:1200px; margin:24px auto; padding:0 24px 36px; display:grid;
      grid-template-columns:minmax(0,1fr) 340px; gap:20px; }
    .layout > * { min-width:0; }
    .card { background:white; border:1px solid var(--line); border-radius:14px;
      box-shadow:0 8px 28px #1725540d; }
    .main { padding:22px; }
    .side { padding:20px; align-self:start; position:sticky; top:125px; }
    .row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    .between { justify-content:space-between; }
    .eyebrow { color:var(--brand); font-size:12px; font-weight:800; letter-spacing:.08em;
      text-transform:uppercase; }
    .counter { color:var(--muted); font-size:13px; overflow-wrap:anywhere; }
    .creative { display:grid; grid-template-columns:minmax(220px,38%) 1fr; gap:18px;
      margin:18px 0; padding:16px; background:#f8faff; border:1px solid #dce5ff; border-radius:12px; }
    .creative img { width:100%; max-height:430px; object-fit:contain; border-radius:9px;
      background:#e9edf5; border:1px solid var(--line); }
    .image-empty { min-height:220px; display:grid; place-items:center; text-align:center;
      color:var(--muted); background:#e9edf5; border-radius:9px; padding:20px; }
    .page { font-size:20px; font-weight:800; margin:0 0 12px; }
    .field { margin:0 0 14px; }
    .label { display:block; margin-bottom:5px; color:var(--muted); font-size:11px;
      font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
    #title, .copy { overflow-wrap:anywhere; }
    .copy { white-space:pre-wrap; line-height:1.48; max-height:14.8em; overflow-y:auto;
      padding-right:10px; scrollbar-gutter:stable; }
    .chips { display:flex; gap:6px; flex-wrap:wrap; }
    .chip { padding:4px 8px; border-radius:99px; color:#344054; background:#eef2ff;
      border:1px solid #dce3ff; font-size:12px; }
    a { color:var(--brand); font-weight:700; }
    .review { border-top:1px solid var(--line); padding-top:18px; }
    .decision { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:10px 0 16px; }
    .decision label { border:2px solid var(--line); border-radius:10px; padding:14px;
      font-weight:800; text-align:center; cursor:pointer; }
    .decision label:has(input:checked) { border-color:var(--brand); background:#eef2ff; }
    .decision input { margin-right:7px; }
    .form-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    input[type=text], input[type=number], select, textarea { width:100%; padding:10px 11px;
      color:var(--ink); background:white; border:1px solid #bfc7d4; border-radius:8px; }
    textarea { min-height:78px; resize:vertical; }
    .full { grid-column:1/-1; }
    .actions { display:flex; gap:9px; flex-wrap:wrap; margin-top:16px; }
    .btn { padding:10px 14px; border:1px solid var(--line); border-radius:8px;
      color:var(--ink); background:white; font-weight:750; }
    .btn:hover { background:#f3f5f9; }
    .primary { color:white; background:var(--brand); border-color:var(--brand); }
    .primary:hover { background:var(--brand-dark); }
    .danger { color:var(--no); }
    .status { min-height:22px; margin-top:12px; font-size:13px; font-weight:700; }
    .status.ok { color:var(--ok); } .status.error { color:var(--no); }
    .stat-grid { display:grid; grid-template-columns:1fr 1fr; gap:9px; margin:14px 0 18px; }
    .stat { padding:10px; border-radius:9px; background:#f5f7fb; }
    .stat strong { display:block; font-size:20px; } .stat span { color:var(--muted); font-size:11px; }
    .help { color:var(--muted); font-size:13px; line-height:1.5; }
    .notice { padding:10px 12px; border-left:3px solid #f59f00; background:#fff9db;
      border-radius:6px; font-size:13px; line-height:1.45; }
    .hidden { display:none !important; }
    @media (max-width:860px) { .layout { grid-template-columns:1fr; } .side { position:static; }
      .creative { grid-template-columns:1fr; } }
    @media (max-width:520px) { .layout { padding:0 10px 24px; } .main,.side { padding:15px; }
      .form-grid,.decision { grid-template-columns:1fr; } .full { grid-column:auto; }
      .actions .btn { width:100%; white-space:normal; } }
  </style>
</head>
<body>
  <header class="top"><div class="top-inner">
    <h1>Supplements taxonomy review</h1>
    <div class="subtitle">Human gold-set labeling · progress stays in this browser until exported</div>
    <div class="progress"><div id="progressFill"></div></div>
  </div></header>
  <main class="layout">
    <section class="card main">
      <div class="row between">
        <div><div class="eyebrow" id="strataSummary"></div><div class="counter" id="counter"></div></div>
        <div class="row">
          <button class="btn" id="previous">← Previous</button>
          <button class="btn" id="next">Next →</button>
        </div>
      </div>
      <div class="creative">
        <div id="imageWrap"></div>
        <div>
          <h2 class="page" id="pageName"></h2>
          <div class="field"><span class="label">Title</span><div id="title"></div></div>
          <div class="field"><span class="label">Ad copy</span><div class="copy" id="body"></div></div>
          <div class="field"><span class="label">Review strata (sampling aids, not labels)</span>
            <div class="chips" id="strata"></div></div>
          <div class="row"><a id="snapshot" target="_blank" rel="noopener noreferrer">Open Meta ad ↗</a>
            <a id="landing" target="_blank" rel="noopener noreferrer">Open product page ↗</a></div>
        </div>
      </div>
      <div class="review">
        <div class="eyebrow" id="roleHeading"></div>
        <h2>Is the advertised product a dietary supplement?</h2>
        <div class="decision">
          <label><input type="radio" name="decision" value="true"> Yes, supplement</label>
          <label><input type="radio" name="decision" value="false"> No, not a supplement</label>
        </div>
        <div class="form-grid">
          <label id="categoryWrap"><span class="label">Supplement subcategory</span>
            <select id="category"><option value="">Choose a subcategory…</option></select></label>
          <label><span class="label">Notes (optional)</span><textarea id="notes"
            placeholder="Record ambiguity or evidence used."></textarea></label>
          <label class="full hidden" id="rationaleWrap"><span class="label">Adjudication rationale (required)</span>
            <textarea id="rationale" placeholder="Explain why the final decision resolves the disagreement."></textarea></label>
        </div>
        <div class="actions">
          <button class="btn primary" id="save">Save decision & next incomplete</button>
          <button class="btn danger" id="clear">Clear this role's decision</button>
        </div>
        <div class="status" id="status" role="status" aria-live="polite"></div>
      </div>
    </section>
    <aside class="card side">
      <label><span class="label">Review role</span><select id="role">
        <option value="primary">Primary reviewer</option>
        <option value="secondary">Independent second reviewer</option>
        <option value="adjudication">Independent adjudicator</option>
      </select></label>
      <label><span class="label">Your reviewer ID</span><input id="reviewerId" type="text"
        autocomplete="off" placeholder="e.g. avinaash-primary"></label>
      <div class="stat-grid">
        <div class="stat"><strong id="primaryCount">0</strong><span>primary / __ITEM_COUNT__</span></div>
        <div class="stat"><strong id="secondaryCount">0</strong><span>secondary (min __SECONDARY_COUNT__)</span></div>
        <div class="stat"><strong id="disagreementCount">0</strong><span>disagreements</span></div>
        <div class="stat"><strong id="adjudicatedCount">0</strong><span>adjudicated</span></div>
      </div>
      <label><span class="label">View</span><select id="filter">
        <option value="all">All ads</option><option value="incomplete">Incomplete for this role</option>
        <option value="disagreements">Reviewer disagreements</option>
      </select></label>
      <label><span class="label">Jump to ad number</span><input id="jump" type="number" min="1" max="__ITEM_COUNT__"></label>
      <div class="actions">
        <button class="btn primary" id="download">Download review JSON</button>
        <button class="btn" id="importButton">Import prior JSON</button>
        <input class="hidden" id="importFile" type="file" accept="application/json,.json">
        <button class="btn danger" id="reset">Reset browser progress</button>
      </div>
      <p class="notice">Export can be partial for handoff between reviewers. Pipeline validation still
        fails closed until all primary labels, ≥20% independent second reviews, κ ≥0.80, and every
        disagreement's independent adjudication are complete.</p>
      <p class="help"><strong>Shortcuts:</strong> 1 = supplement, 2 = not supplement, ←/→ = navigate,
        Ctrl/⌘+S = save. Human labels only—sampling strata and generated outputs are never labels.</p>
    </aside>
  </main>
  <script id="reviewData" type="application/json">__REVIEW_DATA__</script>
  <script id="displayData" type="application/json">__DISPLAY_DATA__</script>
  <script>
  (() => {
    'use strict';
    const base = JSON.parse(document.getElementById('reviewData').textContent);
    const display = JSON.parse(document.getElementById('displayData').textContent);
    const categories = __CATEGORIES__;
    const storageKey = `supplements-taxonomy-review:${base.sample_sha256}`;
    const reviewerKey = `${storageKey}:reviewer`;
    const clone = value => JSON.parse(JSON.stringify(value));
    let state = clone(base);
    let index = 0;

    const el = id => document.getElementById(id);
    const item = () => state.items[index];
    const role = () => el('role').value;
    const decision = (row, selectedRole = role()) => row[selectedRole];
    const complete = value => value && typeof value.is_supplement === 'boolean';
    const agrees = row => complete(row.primary) && complete(row.secondary) &&
      row.primary.is_supplement === row.secondary.is_supplement &&
      (row.primary.is_supplement !== true ||
        row.primary.supplement_subcategory === row.secondary.supplement_subcategory);
    const disagreement = row => complete(row.primary) && complete(row.secondary) && !agrees(row);
    const setText = (id, value) => { el(id).textContent = value || '—'; };
    const setStatus = (message, kind = 'ok') => {
      el('status').textContent = message; el('status').className = `status ${kind}`;
    };

    function validImported(candidate) {
      if (!candidate || candidate.report_schema_version !== base.report_schema_version ||
          candidate.sample_sha256 !== base.sample_sha256 || !Array.isArray(candidate.items) ||
          candidate.items.length !== base.items.length) return false;
      return candidate.items.every((row, i) => row.ad_id === base.items[i].ad_id);
    }

    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      if (validImported(saved)) state = saved;
    } catch (_) { /* Ignore corrupt local browser state and retain the immutable base. */ }
    el('reviewerId').value = localStorage.getItem(reviewerKey) || '';
    categories.forEach(value => {
      const option = document.createElement('option'); option.value = value;
      option.textContent = value.replaceAll('_', ' '); el('category').append(option);
    });

    function persist() {
      localStorage.setItem(storageKey, JSON.stringify(state));
      localStorage.setItem(reviewerKey, el('reviewerId').value.trim());
    }

    function renderStats() {
      const primary = state.items.filter(row => complete(row.primary)).length;
      const secondary = state.items.filter(row => complete(row.secondary)).length;
      const disagreements = state.items.filter(disagreement).length;
      const adjudicated = state.items.filter(row => disagreement(row) && complete(row.adjudication)).length;
      el('primaryCount').textContent = primary; el('secondaryCount').textContent = secondary;
      el('disagreementCount').textContent = disagreements; el('adjudicatedCount').textContent = adjudicated;
      el('progressFill').style.width = `${100 * primary / state.items.length}%`;
    }

    function render() {
      const row = item(); const extra = display[row.ad_id] || {};
      el('counter').textContent = `Ad ${index + 1} of ${state.items.length} · ID ${row.ad_id}`;
      el('jump').value = index + 1; setText('pageName', row.page_name);
      setText('title', extra.title || row.title); setText('body', extra.body || row.body);
      el('strataSummary').textContent = (row.review_strata || []).join(' · ');
      el('strata').replaceChildren(...(row.review_strata || []).map(value => {
        const chip = document.createElement('span'); chip.className = 'chip'; chip.textContent = value; return chip;
      }));
      const snapshot = el('snapshot'); snapshot.href = row.snapshot_url || '#';
      snapshot.classList.toggle('hidden', !row.snapshot_url);
      const landing = el('landing'); landing.href = extra.link_url || '#';
      landing.classList.toggle('hidden', !extra.link_url);
      const imageWrap = el('imageWrap'); imageWrap.replaceChildren();
      const imageUrl = (extra.image_urls || []).find(Boolean);
      if (imageUrl) {
        const image = document.createElement('img'); image.src = imageUrl; image.alt = `Creative from ${row.page_name}`;
        image.addEventListener('error', () => { const fallback = document.createElement('div');
          fallback.className = 'image-empty'; fallback.textContent = 'Image URL expired. Open the Meta ad instead.';
          image.replaceWith(fallback); }); imageWrap.append(image);
      } else { const fallback = document.createElement('div'); fallback.className = 'image-empty';
        fallback.textContent = 'No embedded image URL. Open the Meta ad.'; imageWrap.append(fallback); }

      const selectedRole = role(); const current = decision(row, selectedRole);
      el('roleHeading').textContent = el('role').selectedOptions[0].textContent;
      document.querySelectorAll('input[name=decision]').forEach(input => {
        input.checked = complete(current) && String(current.is_supplement) === input.value;
      });
      el('category').value = current?.supplement_subcategory || '';
      el('notes').value = current?.notes || ''; el('rationale').value = current?.rationale || '';
      el('rationaleWrap').classList.toggle('hidden', selectedRole !== 'adjudication');
      el('categoryWrap').classList.toggle('hidden', current?.is_supplement === false);
      const adjudicationBlocked = selectedRole === 'adjudication' && !disagreement(row);
      el('save').disabled = adjudicationBlocked;
      setStatus(adjudicationBlocked ? 'Adjudication is available only for reviewer disagreements.' : '',
        adjudicationBlocked ? 'error' : 'ok');
      renderStats();
    }

    function matchingIndexes() {
      const selectedRole = role(); const filter = el('filter').value;
      return state.items.map((row, i) => ({row, i})).filter(({row}) =>
        filter === 'all' || (filter === 'incomplete' && !complete(decision(row, selectedRole))) ||
        (filter === 'disagreements' && disagreement(row))).map(({i}) => i);
    }

    function move(delta) {
      const indexes = matchingIndexes(); if (!indexes.length) return setStatus('No ads match this view.', 'error');
      const position = indexes.indexOf(index); const next = position < 0 ? 0 :
        (position + delta + indexes.length) % indexes.length; index = indexes[next]; render();
    }

    function save() {
      const reviewerId = el('reviewerId').value.trim();
      const selected = document.querySelector('input[name=decision]:checked');
      const selectedRole = role(); const row = item();
      if (!reviewerId) return setStatus('Enter your reviewer ID first.', 'error');
      if (!selected) return setStatus('Choose supplement or non-supplement.', 'error');
      if (selectedRole === 'secondary' && reviewerId === row.primary?.reviewer_id)
        return setStatus('The secondary reviewer must differ from the primary reviewer.', 'error');
      if (selectedRole === 'adjudication') {
        if (!disagreement(row)) return setStatus('This ad has no reviewer disagreement.', 'error');
        if ([row.primary?.reviewer_id, row.secondary?.reviewer_id].includes(reviewerId))
          return setStatus('The adjudicator must be independent of both reviewers.', 'error');
        if (!el('rationale').value.trim()) return setStatus('Adjudication rationale is required.', 'error');
      }
      const isSupplement = selected.value === 'true';
      if (isSupplement && !el('category').value)
        return setStatus('Choose a supplement subcategory.', 'error');
      const value = { reviewer_id:reviewerId, reviewed_at:new Date().toISOString(),
        is_supplement:isSupplement, supplement_subcategory:isSupplement ? el('category').value : null,
        notes:el('notes').value.trim() };
      if (selectedRole === 'adjudication') value.rationale = el('rationale').value.trim();
      row[selectedRole] = value; persist(); renderStats(); setStatus('Saved locally.', 'ok');
      el('filter').value = 'incomplete'; move(1);
    }

    document.querySelectorAll('input[name=decision]').forEach(input => input.addEventListener('change', () => {
      el('categoryWrap').classList.toggle('hidden', input.value === 'false');
      if (input.value === 'false') el('category').value = '';
    }));
    el('previous').addEventListener('click', () => move(-1)); el('next').addEventListener('click', () => move(1));
    el('save').addEventListener('click', save);
    el('clear').addEventListener('click', () => { item()[role()] = role() === 'primary' ?
      {reviewer_id:null,reviewed_at:null,is_supplement:null,supplement_subcategory:null,notes:''} : null;
      persist(); render(); setStatus('Decision cleared.', 'ok'); });
    el('role').addEventListener('change', render); el('filter').addEventListener('change', () => move(1));
    el('reviewerId').addEventListener('change', persist);
    el('jump').addEventListener('change', event => { const value = Number(event.target.value);
      if (value >= 1 && value <= state.items.length) { index = value - 1; render(); } });
    el('download').addEventListener('click', () => { persist(); const blob = new Blob(
      [JSON.stringify(state, null, 2) + '\n'], {type:'application/json'}); const link = document.createElement('a');
      link.href = URL.createObjectURL(blob); link.download = 'supplements_taxonomy_review_v1.json'; link.click();
      URL.revokeObjectURL(link.href); setStatus('Review JSON downloaded.', 'ok'); });
    el('importButton').addEventListener('click', () => el('importFile').click());
    el('importFile').addEventListener('change', event => { const file = event.target.files[0]; if (!file) return;
      const reader = new FileReader(); reader.onload = () => { try { const candidate = JSON.parse(reader.result);
        if (!validImported(candidate)) throw new Error('schema, sample hash, item count, or ad order differs');
        state = candidate; persist(); index = 0; render(); setStatus('Prior review imported.', 'ok');
      } catch (error) { setStatus(`Import rejected: ${error.message}`, 'error'); } }; reader.readAsText(file); });
    el('reset').addEventListener('click', () => { if (!confirm('Delete all browser-saved decisions for this sample?')) return;
      localStorage.removeItem(storageKey); state = clone(base); index = 0; render(); setStatus('Browser progress reset.', 'ok'); });
    document.addEventListener('keydown', event => { if (['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)) return;
      if (event.key === 'ArrowLeft') move(-1); if (event.key === 'ArrowRight') move(1);
      if (event.key === '1' || event.key === '2') { const input = document.querySelector(
        `input[name=decision][value=${event.key === '1' ? 'true' : 'false'}]`); input.checked = true;
        input.dispatchEvent(new Event('change')); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); save(); }
    });
    render();
  })();
  </script>
</body>
</html>
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def build_taxonomy_review_html(review_file: Path, sample_file: Path, output_file: Path) -> None:
    """Validate and embed one immutable review artifact and its display-only sample fields."""
    review_payload = json.loads(review_file.read_text())
    review = TaxonomyReviewArtifact.model_validate(review_payload)
    if review.sample_sha256 != _sha256(sample_file):
        raise ValueError("review sample_sha256 does not match the sample file bytes")

    sample = json.loads(sample_file.read_text())
    sample_by_id = {str(row.get("ad_archive_id") or row.get("ad_id") or ""): row for row in sample}
    review_ids = [item.ad_id for item in review.items]
    if set(review_ids) != set(sample_by_id) or len(sample_by_id) != len(review_ids):
        raise ValueError("sample IDs must exactly match the review artifact")

    display = {
        ad_id: {
            "title": sample_by_id[ad_id].get("title") or "",
            "body": sample_by_id[ad_id].get("body") or "",
            "cta_text": sample_by_id[ad_id].get("cta_text") or "",
            "link_url": sample_by_id[ad_id].get("link_url") or "",
            "image_urls": list(sample_by_id[ad_id].get("image_urls") or []),
        }
        for ad_id in review_ids
    }
    categories = sorted(SUPPLEMENT_SUBCATEGORIES)
    secondary_count = math.ceil(review.minimum_double_review_fraction * len(review.items))
    html = (
        _HTML_TEMPLATE.replace("__REVIEW_DATA__", _safe_json(review_payload))
        .replace("__DISPLAY_DATA__", _safe_json(display))
        .replace("__CATEGORIES__", _safe_json(categories))
        .replace("__ITEM_COUNT__", str(len(review.items)))
        .replace("__SECONDARY_COUNT__", str(secondary_count))
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build_taxonomy_review_html(args.review, args.sample, args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
