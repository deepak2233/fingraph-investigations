"use strict";
const $ = (id) => document.getElementById(id);
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const assetVersion = new URL(document.currentScript.src).search;
const publishedMode = new URLSearchParams(location.search).get("mode") === "published";
const snapshotFiles = new Map();
let cases = [],
  selected = null,
  current = null,
  busy = false,
  papers = [];
let activeFinding = null;
let focusedTransfer = null;
const titles = {
  overview: [
    "Understand the investigation.",
    "Goals, measured outcomes, boundaries and a guided exercise.",
    "Goal & scope",
  ],
  data: [
    "Inspect how records become evidence.",
    "Source tables, typed RDF relationships, SPARQL results and live validation.",
    "Data & RDF explorer",
  ],
  models: [
    "Inspect the model evidence.",
    "Measured benchmark results and live inference on synthetic banking transactions.",
    "Model lab",
  ],
  pipeline: [
    "Trace every committed decision.",
    "Authenticated ingestion, persistent cases, and versioned analyst reviews.",
    "Pipeline",
  ],
  investigation: [
    "Follow the evidence.",
    "Connected transactions. Source-backed context. Analyst-led decisions.",
    "Investigations",
  ],
  architecture: [
    "Built around verifiable facts.",
    "An executable prototype and a clearly bounded production design.",
    "Architecture",
  ],
  ontology: [
    "Give every relationship meaning.",
    "Typed entities, explicit assertions, source provenance, and validation.",
    "Ontology & validation",
  ],
  research: [
    "Read the evidence behind the design.",
    "Selected papers, technical standards, and explicit research limitations.",
    "Research library",
  ],
};
let activeView = null;
function renderView(view) {
  if (activeView === view) return;
  activeView = view;
  document
    .querySelectorAll(".view")
    .forEach((e) => e.classList.toggle("hidden", e.id !== view));
  document.querySelectorAll(".nav").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("page-title").textContent = titles[view][0];
  $("page-subtitle").textContent = titles[view][1];
  $("breadcrumb").textContent = titles[view][2];
  const modeUrl = new URL(location.href);
  if (publishedMode) modeUrl.searchParams.delete("mode");
  else modeUrl.searchParams.set("mode", "published");
  modeUrl.hash = view;
  $("demo-mode-link").href = modeUrl.href;
  window.scrollTo(0, 0);
  document.dispatchEvent(new CustomEvent("fingraph:view", { detail: view }));
}
function showView(view) {
  const hash = "#" + view;
  if (location.hash !== hash) history.pushState(null, "", hash);
  if (activeView === view) window.scrollTo(0, 0);
  else renderView(view);
}
function syncViewFromHash() {
  const hash = location.hash.slice(1);
  const view = hash.startsWith("evidence-") ? "investigation" : hash;
  renderView(Object.hasOwn(titles, view) ? view : "overview");
}
window.addEventListener("hashchange", syncViewFromHash);
window.addEventListener("popstate", syncViewFromHash);
document
  .querySelectorAll(".nav")
  .forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
$("scope-link").addEventListener("click", (e) => {
  e.preventDefault();
  showView("overview");
});
async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? "");
    } catch {}
    const error = new Error(`Request failed (${response.status}). ${detail}`.trim());
    error.status = response.status;
    throw error;
  }
  return response.json();
}
async function boundedRequest(path, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await request(path, { ...options, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) throw Error("The request timed out.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
async function snapshot(name) {
  if (!snapshotFiles.has(name)) {
    snapshotFiles.set(name, boundedRequest(`/demo/${name}.json${assetVersion}`, {}, 12000));
  }
  try {
    return await snapshotFiles.get(name);
  } catch (error) {
    snapshotFiles.delete(name);
    throw error;
  }
}
async function demoAction(path, options, readSnapshot, preferPublished = false) {
  if (publishedMode) return { result: await readSnapshot(), origin: "Published example" };
  if (preferPublished) {
    try {
      return { result: await readSnapshot(), origin: "Published example" };
    } catch {
      // The first graph can also be retrieved from the normal API.
    }
  }
  try {
    return { result: await boundedRequest(path, options), origin: "Live API" };
  } catch (error) {
    if (error.status && error.status < 500 && ![408, 429].includes(error.status)) throw error;
    try {
      return { result: await readSnapshot(), origin: "Published example", liveUnavailable: true };
    } catch {
      throw Error(`The live API and published example are unavailable. ${error.message}`);
    }
  }
}
function demoOrigin(response) {
  return response.liveUnavailable
    ? "Live API unavailable. Showing a published example. You can retry the action."
    : response.origin;
}
function queue() {
  const q = $("search").value.toLowerCase();
  const filtered = cases.filter((c) =>
    (c.id + " " + c.title + " " + c.summary).toLowerCase().includes(q),
  );
  $("cases").innerHTML = filtered.length
    ? filtered
        .map(
          (c) =>
            `<button class="case-button ${c.id === selected?.id ? "selected" : ""}" data-case="${esc(c.id)}"><div class="case-meta"><span>${esc(c.id)}</span><span class="case-type">SYNTHETIC</span></div><h3>${esc(c.title)}</h3><p>${c.transactions.length} transactions · ${new Set(c.transactions.flatMap((t) => [t.sender, t.receiver])).size} active accounts</p></button>`,
        )
        .join("")
    : '<p class="muted padded">No cases match your search.</p>';
  document.querySelectorAll("[data-case]").forEach((b) => {
    b.disabled = busy;
    b.addEventListener("click", () => choose(b.dataset.case));
  });
}
$("search").addEventListener("input", queue);
function choose(id) {
  if (busy) return;
  selected = cases.find((c) => c.id === id);
  current = null;
  activeFinding = null;
  focusedTransfer = selected.transactions[0]?.id ?? null;
  queue();
  $("case-id").textContent = id;
  $("case-title").textContent = selected.title;
  $("case-summary").textContent = selected.summary;
  $("run").disabled = false;
  $("result-state").textContent = "Ready to investigate";
  $("status").textContent = "";
  $("result").innerHTML =
    '<div class="empty-state"><div class="empty-icon">⌘</div><h3>Inspect the network. Then investigate.</h3><p>The workflow checks graph signals, screening freshness, ownership, and evidence.<br>Every draft requires review.</p></div>';
  $("rule-explainer").classList.add("hidden");
  drawNetwork();
  renderTransferTimeline();
  updateTransferFocus();
}
function bindGraphButton(element, activate) {
  element.addEventListener("click", activate);
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate();
    }
  });
}
function drawNetwork() {
  const active = [
    ...new Set(selected.transactions.flatMap((t) => [t.sender, t.receiver])),
  ];
  const coords = {};
  const n = active.length;
  active.forEach((id, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    coords[id] = n === 1
      ? [340, 150]
      : [340 + Math.cos(angle) * 220, 155 + Math.sin(angle) * 110];
  });
  let lines = "",
    nodes = "";
  for (const t of selected.transactions) {
    const [x1, y1] = coords[t.sender],
      [x2, y2] = coords[t.receiver];
    const distance = Math.hypot(x2 - x1, y2 - y1);
    const dx = (x2 - x1) / (distance || 1);
    const dy = (y2 - y1) / (distance || 1);
    const path = distance
      ? `M${x1 + dx * 24} ${y1 + dy * 24} L${x2 - dx * 27} ${y2 - dy * 27}`
      : `M${x1 - 15} ${y1 - 20} C${x1 - 55} ${y1 - 69} ${x1 + 55} ${y1 - 69} ${x1 + 15} ${y1 - 20}`;
    const mx = (x1 + x2) / 2;
    const my = distance ? (y1 + y2) / 2 : y1 - 66;
    lines += `<g class="transfer-edge" data-graph-transfer="${esc(t.id)}" role="button" tabindex="0" aria-label="Inspect transfer ${esc(t.id)}" aria-pressed="false">
      <path d="${path}" class="transfer-target"/><path d="${path}" class="transfer-line" marker-end="url(#arrow)"/>
      <rect x="${mx - 57}" y="${my - 9}" width="114" height="18" rx="4"/>
      <text x="${mx}" y="${my + 3}" text-anchor="middle" font-size="10">${esc(t.id)} · ${esc(t.currency)} ${(Number(t.amount) / 1000).toFixed(1)}k</text></g>`;
  }
  for (const id of active) {
    const [x, y] = coords[id];
    const a = selected.accounts.find((a) => a.id === id);
    nodes += `<g class="graph-node" role="button" tabindex="0" data-node="${esc(id)}" aria-label="Inspect ${esc(a.name)}"><circle cx="${x}" cy="${y}" r="23" fill="${id === "A001" ? "#e0f2e9" : "#f1f5f8"}" stroke="${id === "A001" ? "#63a68a" : "#9fb5c5"}" stroke-width="1.5"/><text x="${x}" y="${y + 4}" text-anchor="middle" font-size="11" fill="#4d7180">${esc(id.slice(-3))}</text><text x="${x}" y="${y + 40}" text-anchor="middle" font-size="11" fill="#506b7a">${esc(a.name)}</text><text x="${x}" y="${y + 54}" text-anchor="middle" font-size="9" fill="#9ba9b2">${esc(id)}</text></g>`;
  }
  $("network").innerHTML =
    `<svg viewBox="0 0 680 330" role="group" aria-label="${esc(selected.title)} directed transaction network"><defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="#9bb1bf"/></marker></defs>${lines}${nodes}</svg>`;
  $("node-detail").textContent =
    "Select an account to inspect its synthetic identity.";
  $("network").querySelectorAll("[data-node]").forEach((node) => {
    bindGraphButton(node, () => {
      const a = selected.accounts.find((account) => account.id === node.dataset.node);
      $("node-detail").textContent =
        `${a.id} · ${a.name} · Holder ${a.holder} · ${!a.owner || (selected.missing_ownership && a.id === "A001") ? "Owner assertion unavailable" : `Direct owner assertion ${a.owner} (synthetic registry)`}`;
    });
  });
  $("network").querySelectorAll("[data-graph-transfer]").forEach((edge) =>
    bindGraphButton(edge, () => selectTransfer(edge.dataset.graphTransfer)),
  );
}

function chronologicalTransfers() {
  return [...selected.transactions].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

function renderTransferTimeline() {
  $("transfer-evidence").classList.remove("hidden");
  const timeline = $("transfer-timeline");
  timeline.innerHTML = chronologicalTransfers().map((t) => `
    <li><button type="button" data-timeline-transfer="${esc(t.id)}" aria-pressed="false">
      <span><time datetime="${esc(t.timestamp)}">${esc(new Date(t.timestamp).toISOString().slice(11, 16))} UTC</time><strong>${esc(t.id)}</strong></span>
      <span>${esc(t.sender)} → ${esc(t.receiver)}</span>
      <span class="transfer-support-label"></span>
    </button></li>`).join("");
  timeline.querySelectorAll("[data-timeline-transfer]").forEach((control) =>
    control.addEventListener("click", () => selectTransfer(control.dataset.timelineTransfer)),
  );
}

function selectTransfer(id) {
  focusedTransfer = id;
  updateTransferFocus();
}

function updateTransferFocus() {
  const evidence = new Set(activeFinding?.evidence ?? []);
  const transfers = chronologicalTransfers().filter((t) => evidence.has(t.id));
  if (transfers.length) {
    const minutes = (Date.parse(transfers.at(-1).timestamp) - Date.parse(transfers[0].timestamp)) / 60000;
    $("transfer-summary").textContent = `${transfers.length} supporting transfers over ${Number(minutes.toFixed(2))} minutes`;
  } else {
    $("transfer-summary").textContent = `${selected.transactions.length} transfers in source order by time`;
  }
  document.querySelectorAll("[data-graph-transfer], [data-timeline-transfer]").forEach((control) => {
    const id = control.dataset.graphTransfer ?? control.dataset.timelineTransfer;
    const supported = evidence.has(id);
    control.classList.toggle("supports-finding", supported);
    control.setAttribute("aria-pressed", String(id === focusedTransfer));
    const label = control.querySelector(".transfer-support-label");
    if (label) label.textContent = supported ? "Rule evidence" : "";
  });
  const transaction = selected.transactions.find((t) => t.id === focusedTransfer);
  if (!transaction) return;
  const href = new URL(location.href);
  href.searchParams.set("case", selected.id);
  href.searchParams.set("resource", `ex:${transaction.id}`);
  href.hash = "data";
  $("transfer-detail").innerHTML = `
    <h4>${esc(transaction.id)} · ${esc(transaction.sender)} → ${esc(transaction.receiver)}</h4>
    <dl><dt>Amount</dt><dd>${esc(transaction.currency)} ${esc(transaction.amount)}</dd>
    <dt>Event time</dt><dd>${esc(transaction.timestamp)}</dd>
    <dt>Source record</dt><dd>${esc(transaction.source_id)}</dd>
    <dt>Rule evidence</dt><dd>${!activeFinding ? "Select a finding after investigation" : evidence.has(transaction.id) ? `Supports ${esc(activeFinding.id)}` : "Outside the selected finding"}</dd></dl>
    <a href="${esc(href.href)}">Inspect this RDF resource ↗</a>`;
}
$("run").addEventListener("click", async () => {
  if (!selected || busy) return;
  busy = true;
  queue();
  $("run").disabled = true;
  $("run").textContent = "Investigating…";
  $("status").className = "";
  $("status").textContent = publishedMode
    ? "Loading the published investigation…"
    : "Running RDF validation and five specialist steps…";
  try {
    const caseId = selected.id;
    const response = await demoAction(
      "/api/investigate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_id: caseId }),
      },
      async () => (await snapshot(caseId)).investigation,
    );
    current = { ...response.result, demo_execution: response.origin };
    renderResult();
    if (Array.isArray(current.signals)) renderRuleExplanation();
    $("status").textContent = `${demoOrigin(response)} · Analyst review is required.`;
  } catch (e) {
    $("status").className = "error";
    $("status").textContent = e.message;
  } finally {
    busy = false;
    queue();
    $("run").disabled = false;
    $("run").innerHTML = "Run investigation <span>↗</span>";
  }
});
function renderResult() {
  if (current.status === "quarantined") {
    $("result").innerHTML =
      '<div class="padded error">Validation failed. Case quarantined before analysis.</div>';
    $("result-state").textContent = "Quarantined";
    return;
  }
  const d = current.draft;
  $("result-state").textContent = "Awaiting analyst review";
  $("result").innerHTML =
    `<div class="result-grid"><div class="trace"><div><div class="eyebrow">WORKFLOW TRACE</div><p class="muted">Deterministic specialist execution</p></div>${current.trace.map((t) => `<div class="trace-item"><h4>${esc(t.agent)}</h4><p>${esc(t.result)}</p></div>`).join("")}</div><div class="draft"><div class="draft-top"><div class="score">${d.heuristic_score}<small>/100</small></div><div><h3>${esc(d.title)}</h3><span class="muted">Demo prioritisation points · not a probability or ML prediction</span></div></div><h4>OBSERVATIONS</h4>${d.findings.map((f) => `<p class="finding">${esc(f.text)} ${f.evidence.map((e) => `<a href="#evidence-${esc(e)}" class="citation">${esc(e)}</a>`).join("")}</p>`).join("")}<h4>OWNERSHIP CONTEXT</h4><p class="finding">${current.ownership.shared.length ? current.ownership.shared.map((o) => `${esc(o.owner)} has direct assertions for ${o.accounts.map(esc).join(", ")}.`).join(" ") : "No shared direct owner was found among the active accounts."} This is not a legal determination of beneficial ownership.</p><h4>UNRESOLVED GAPS</h4>${d.gaps.map((g) => `<p class="gap">${esc(g)}</p>`).join("")}<h4>EVIDENCE REGISTER</h4><table class="evidence-table"><thead><tr><th>ID</th><th>Observed record</th><th>Source</th></tr></thead><tbody>${current.evidence.map((e) => `<tr id="evidence-${esc(e.id)}"><td>${esc(e.id)}</td><td>${esc(e.text)}</td><td>${esc(e.source)}</td></tr>`).join("")}</tbody></table><div class="review"><h4>Analyst review</h4><p>This review is a browser-session demonstration. It does not update a bank case system or submit a regulatory report. Downloads contain synthetic data only.</p><label class="muted" for="reviewer">Reviewer name</label><input id="reviewer" maxlength="80" placeholder="Enter a demonstration reviewer name"><label class="muted" for="reason">Review rationale</label><textarea id="reason" maxlength="2000" placeholder="Record the reason for your review decision…"></textarea><div class="review-actions"><button class="primary" id="approve">Mark draft reviewed</button><button class="secondary" id="reject">Request more evidence</button><button class="secondary" id="export">Export case JSON ↓</button></div><p id="review-status" role="status"></p></div><div class="provenance">RDF validation: passed · Ontology ${esc(current.provenance.ontology_version)} · ${esc(current.provenance.policy_version)}<br>Fixture SHA-256: ${esc(current.provenance.case_sha256)}<br>As of ${esc(current.provenance.as_of)} · Checkpoint: request-local · No trained model</div></div></div>`;
  $("approve").addEventListener("click", () => review("reviewed"));
  $("reject").addEventListener("click", () =>
    review("more_evidence_requested"),
  );
  $("export").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(current, null, 2)], {
        type: "application/json",
      }),
      url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selected.id}-synthetic-case.json`;
    a.click();
    URL.revokeObjectURL(url);
  });
}
function review(action) {
  const reviewer = $("reviewer").value.trim(),
    reason = $("reason").value.trim();
  if (!reviewer || !reason) {
    $("review-status").textContent = "Enter a reviewer name and a rationale.";
    return;
  }
  current.local_review = {
    action,
    reviewer,
    reason,
    at: new Date().toISOString(),
    scope: "browser-session demonstration, not server approval",
  };
  $("review-status").textContent =
    action === "reviewed"
      ? "Draft marked reviewed for this browser session. Export to retain the synthetic record."
      : "More evidence requested for this browser session.";
  $("result-state").textContent =
    action === "reviewed"
      ? "Reviewed · session only"
      : "More evidence requested";
}
function renderPapers() {
  const q = $("paper-search").value.toLowerCase();
  const filtered = papers.filter((p) =>
    JSON.stringify(p).toLowerCase().includes(q),
  );
  $("paper-count").textContent = `${filtered.length} / ${papers.length} papers`;
  $("papers").innerHTML =
    filtered
      .map(
        (p) =>
          `<article class="panel paper"><div class="eyebrow">${esc(p.category)} · ${esc(p.year)}</div><h3>${esc(p.title)}</h3><p>${esc(p.finding)}</p><p class="takeaway"><b>Design implication:</b> ${esc(p.implication)}</p><p><b>Limit:</b> ${esc(p.limit)}</p><p class="muted">Reading depth · ${esc(p.review_depth)}</p><a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">Read primary source ↗</a></article>`,
      )
      .join("") || '<p class="muted">No papers match this filter.</p>';
}
$("paper-search").addEventListener("input", renderPapers);

function renderCatalog(catalog) {
  cases = catalog.cases;
  papers = catalog.research;
  choose(cases[0].id);
  renderPapers();
  renderModels(catalog);
}

let evaluationReport = null;
let inferenceCases = [];
const modelNames = {
  directed_graphsage: "Directed GraphSAGE",
  logistic_regression: "Logistic regression",
  histogram_gradient_boosting: "Gradient boosting",
};
const percent = (value) => (value * 100).toFixed(2) + "%";
function renderModels(catalog) {
  evaluationReport = catalog.evaluation;
  inferenceCases = catalog.examples;
  const test = evaluationReport.models.directed_graphsage.test;
  $("model-summary").innerHTML =
    `<article><span>Sampled transactions</span><strong>${evaluationReport.dataset.sample_rows.toLocaleString()}<small>5% of HI-Small</small></strong></article><article><span>Held-out transactions</span><strong>${test.rows.toLocaleString()}<small>${test.positives} synthetic positives</small></strong></article><article><span>Model comparison</span><strong>3<small>same time boundaries</small></strong></article>`;
  const entries = Object.entries(evaluationReport.models);
  $("metric-table").innerHTML =
    "<thead><tr><th>Model</th><th>Average precision</th><th>ROC AUC</th><th>Precision at 1%</th><th>Recall at 1%</th></tr></thead><tbody>" +
    entries
      .map(
        ([name, result]) =>
          `<tr><td>${modelNames[name]}</td><td>${result.test.average_precision.toFixed(4)}</td><td>${result.test.roc_auc.toFixed(4)}</td><td>${percent(result.test.precision_at_1_percent)}</td><td>${percent(result.test.recall_at_1_percent)}</td></tr>`,
      )
      .join("") +
    "</tbody>";
  const best = entries.reduce((a, b) =>
    a[1].validation.average_precision > b[1].validation.average_precision
      ? a
      : b,
  );
  $("model-conclusion").textContent =
    `${modelNames[best[0]]} leads on validation average precision. The held-out positive prevalence is ${percent(test.prevalence)}. Results cover one synthetic dataset sample and one seed.`;
  $("model-limitations").innerHTML = evaluationReport.limitations
    .map((text) => `<li>${esc(text)}</li>`)
    .join("");
  $("model-example").innerHTML = inferenceCases
    .map(
      (item) =>
        `<option value="${esc(item.id)}">${esc(item.id)} · ${esc(item.currency)} ${esc(item.amount)}</option>`,
    )
    .join("");
  $("score-model").disabled = !inferenceCases.length;
  renderModelInput();
}
function renderModelInput() {
  const item = inferenceCases.find(
    (example) => example.id === $("model-example").value,
  );
  if (!item) return;
  $("model-input").innerHTML =
    `<dl><dt>Transfer</dt><dd>${esc(item.sender)} → ${esc(item.receiver)}</dd><dt>Method</dt><dd>${esc(item.format)}</dd><dt>Timestamp</dt><dd>${esc(item.timestamp)}</dd><dt>Graph history cutoff</dt><dd>${esc(item.graph_cutoff)}</dd></dl>`;
  $("model-output").textContent = "";
  if ($("prediction-explanation"))
    $("prediction-explanation").innerHTML =
      "<p>Score this transaction to inspect its prediction explanation.</p>";
}
$("model-example").addEventListener("change", renderModelInput);
$("try-inference").addEventListener("click", () => {
  $("inference-panel").scrollIntoView({ block: "start" });
  $("model-example").focus({ preventScroll: true });
});
$("score-model").addEventListener("click", async () => {
  const button = $("score-model");
  const example = $("model-example");
  const exampleId = example.value;
  button.disabled = true;
  example.disabled = true;
  $("model-output").textContent = publishedMode
    ? "Loading the published scores…"
    : "Running model inference…";
  $("prediction-explanation").textContent = "Loading prediction explanation…";
  try {
    const response = await demoAction(
      "/api/model/score",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ example_id: exampleId }),
      },
      async () => (await snapshot("scores"))[exampleId],
    );
    const result = response.result;
    const inferenceNote = response.origin === "Published example"
      ? "Scores and explanations saved by the release. The graph embeddings use history before the test period."
      : result.inference;
    renderExplanation(result);
    $("model-output").innerHTML =
      `<p class="muted">${esc(demoOrigin(response))}</p>` + Object.entries(result.scores)
        .map(
          ([name, value]) =>
            `<div class="model-score"><span>${modelNames[name]}</span><strong>${value.toFixed(4)}</strong></div>`,
        )
        .join("") +
      `<p>Dataset label <b>${result.known_synthetic_label === 1 ? "Synthetic laundering" : "Synthetic legitimate"}</b></p><p class="muted">${esc(inferenceNote)}</p>`;
  } catch (error) {
    $("model-output").textContent = error.message;
    $("prediction-explanation").textContent = "Score the transaction again to retry.";
  } finally {
    button.disabled = false;
    example.disabled = false;
  }
});
let serviceCase = null;
let serviceBusy = false;
async function showServiceAvailability() {
  if (publishedMode) return;
  const status = $("service-availability");
  status.textContent = "Checking the durable review service…";
  try {
    const health = await boundedRequest("/api/health");
    $("service-form").classList.toggle("hidden", !health.private_review_configured);
    status.textContent = health.private_review_configured
      ? "The private review API is configured. An authorised OIDC token is required."
      : "Durable review requires a separate backend. You can try analyst review in Investigations.";
  } catch {
    $("service-form").classList.add("hidden");
    status.textContent = "Could not verify the durable service. Reopen Pipeline to retry.";
  }
}
document.addEventListener("fingraph:view", (event) => {
  if (event.detail === "pipeline") showServiceAvailability();
});
function serviceHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: "Bearer " + $("service-token").value.trim(),
  };
}
function displayService() {
  $("service-output").textContent = JSON.stringify(
    {
      run_id: serviceCase.run_id,
      status: serviceCase.status,
      version: serviceCase.version,
      decision: serviceCase.decision,
      provenance: serviceCase.provenance,
    },
    null,
    2,
  );
  $("reload-service").disabled = false;
  $("review-service").disabled =
    serviceCase.status !== "awaiting_analyst_review";
  $("evidence-service").disabled =
    serviceCase.status !== "awaiting_analyst_review";
}
async function serviceAction(action) {
  if (serviceBusy) return;
  if (!$("service-token").value.trim()) {
    $("service-status").textContent =
      "Enter an OIDC token for the configured private API.";
    return;
  }
  serviceBusy = true;
  try {
    await action();
    displayService();
    $("service-status").textContent = "Server state loaded successfully.";
  } catch (error) {
    $("service-status").textContent = error.message;
  } finally {
    serviceBusy = false;
  }
}
$("ingest-service").addEventListener("click", () =>
  serviceAction(async () => {
    if (!selected) throw Error("Choose an investigation scenario first.");
    const payload = {
      schema_version: 1,
      event_id: crypto.randomUUID(),
      tenant_id: $("service-tenant").value.trim(),
      received_at: new Date().toISOString(),
      case: selected,
    };
    serviceCase = await request("/api/v1/events", {
      method: "POST",
      headers: serviceHeaders(),
      body: JSON.stringify(payload),
    });
  }),
);
$("reload-service").addEventListener("click", () =>
  serviceAction(async () => {
    serviceCase = await request(
      "/api/v1/investigations/" + encodeURIComponent(serviceCase.run_id),
      { headers: serviceHeaders() },
    );
  }),
);
async function submitServiceReview(action) {
  await serviceAction(async () => {
    const reason = $("service-reason").value.trim();
    if (!reason) throw Error("Enter the review rationale.");
    serviceCase = await request(
      "/api/v1/investigations/" +
        encodeURIComponent(serviceCase.run_id) +
        "/reviews",
      {
        method: "POST",
        headers: serviceHeaders(),
        body: JSON.stringify({
          action,
          reason,
          expected_version: serviceCase.version,
          request_id: crypto.randomUUID(),
        }),
      },
    );
  });
}
$("review-service").addEventListener("click", () =>
  submitServiceReview("approve"),
);
$("evidence-service").addEventListener("click", () =>
  submitServiceReview("request_evidence"),
);
function renderRuleExplanation() {
  const host = $("rule-explainer");
  host.classList.remove("hidden");
  host.innerHTML = `<h3>Why these findings appeared</h3><p>Choose a rule to highlight its supporting transfers in the network and timeline.</p><div class="finding-controls" aria-label="Investigation findings">
    ${current.signals.map((signal, i) => `<button type="button" class="secondary" data-signal="${i}" aria-pressed="false">${esc(signal.type.replaceAll("_", " "))} · ${signal.points} points</button>`).join(" ")}
    <button type="button" class="secondary" id="clear-highlight" aria-pressed="false">Show all transfers</button></div><div id="rule-detail" role="status"></div>`;
  const inspect = (signal) => {
    activeFinding = signal ?? null;
    if (activeFinding && !activeFinding.evidence.includes(focusedTransfer)) {
      focusedTransfer = chronologicalTransfers().find((t) => activeFinding.evidence.includes(t.id))?.id;
    }
    updateTransferFocus();
    host.querySelectorAll("[data-signal]").forEach((button) =>
      button.setAttribute("aria-pressed", String(signal === current.signals[Number(button.dataset.signal)])),
    );
    $("clear-highlight").setAttribute("aria-pressed", String(!signal));
    $("rule-detail").innerHTML = signal
      ? `<p><b>${esc(signal.id)}</b> · ${esc(signal.description)}</p><p>Rule evidence: ${signal.evidence.map(esc).join(", ")}. Prioritisation points are configured constants, not learned probabilities.</p><p>Alternative explanation to investigate: legitimate supplier settlement, treasury movement or intercompany cash management. Obtain economic purpose and source-of-funds evidence before escalation.</p>`
      : `<p>${current.signals.length ? "Showing all source transfers." : "None of the configured transaction patterns matched this case. Check the draft for evidence gaps."}</p>`;
  };
  host
    .querySelectorAll("[data-signal]")
    .forEach((b) =>
      b.addEventListener("click", () =>
        inspect(current.signals[Number(b.dataset.signal)]),
      ),
    );
  $("clear-highlight").addEventListener("click", () => inspect(null));
  inspect(current.signals[0]);
}
$("demo-mode-link").textContent = publishedMode ? "Use live API" : "Use published examples";
if (publishedMode) {
  $("workbench-mode").textContent = "Published examples mode. Results were generated by the release pipeline.";
}
syncViewFromHash();
