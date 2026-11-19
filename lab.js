"use strict";
// The explorer reads actual API projections; charts read the checked-in evaluation artifact.
let explored = null;
let graphLoading = false;
const colors = {
  directed_graphsage: "#7561b0",
  logistic_regression: "#3483b1",
  histogram_gradient_boosting: "#087f68",
};
function table(headers, rows) {
  return `<table class="evidence-table"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function plot(
  series,
  {
    xMax = 1,
    yMax = 1,
    xLabel = "Recall",
    yLabel = "Precision",
    baseline = null,
  } = {},
) {
  const w = 560,
    h = 300,
    l = 58,
    r = 20,
    t = 22,
    b = 48;
  const sx = (x) => l + (x / xMax) * (w - l - r),
    sy = (y) => h - b - (y / yMax) * (h - t - b);
  let body = "";
  for (let i = 0; i <= 4; i++) {
    const x = (i * xMax) / 4,
      y = (i * yMax) / 4;
    body += `<line x1="${l}" x2="${w - r}" y1="${sy(y)}" y2="${sy(y)}" stroke="#e4ece8"/><text x="${l - 8}" y="${sy(y) + 4}" text-anchor="end">${y.toFixed(yMax < 0.1 ? 3 : 2)}</text><text x="${sx(x)}" y="${h - b + 20}" text-anchor="middle">${xMax > 1 ? x.toFixed(0) : x.toFixed(2)}</text>`;
  }
  if (baseline !== null)
    body += `<line x1="${l}" x2="${w - r}" y1="${sy(baseline)}" y2="${sy(baseline)}" stroke="#ab8a69" stroke-dasharray="4 4"/>`;
  for (const line of series)
    body += `<polyline points="${line.points.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ")}" fill="none" stroke="${line.color}" stroke-width="2"/>`;
  return `<svg class="metric-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(yLabel)} versus ${esc(xLabel)}"><title>${esc(yLabel)} versus ${esc(xLabel)}</title>${body}<text x="${w / 2}" y="${h - 5}" text-anchor="middle">${esc(xLabel)}</text><text transform="translate(13 ${h / 2}) rotate(-90)" text-anchor="middle">${esc(yLabel)}</text></svg><div class="chart-legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
}
function bars(rows, label, scale = 1) {
  return `<div class="bar-chart" aria-label="${esc(label)}">${rows.map((row) => `<div class="bar-row"><span>${esc(row.name)}</span><div class="bar-track"><i style="width:${Math.min(100, (Math.abs(row.value) / scale) * 100)}%;background:${row.color || "#087f68"}"></i></div><strong>${esc(row.display ?? row.value.toFixed(4))}</strong></div>`).join("")}</div>`;
}
async function loadExplorer() {
  if (graphLoading || !$("data-case").value) return;
  graphLoading = true;
  $("load-graph").disabled = true;
  $("data-status").textContent = "Building RDF and running SHACL…";
  try {
    const params = new URLSearchParams({
      variant: $("graph-variant").value,
      query: $("graph-query").value,
    });
    explored = await request(
      `/api/explorer/${encodeURIComponent($("data-case").value)}?${params}`,
    );
    const source = explored.source;
    $("data-status").textContent = explored.mutation;
    $("data-stats").innerHTML =
      `<article><span>Source transactions</span><strong>${source.transactions.length}</strong></article><article><span>Asserted instance triples</span><strong>${explored.instance_triple_count}</strong></article><article><span>SHACL validation</span><strong class="text-value">${explored.validation.conforms ? "Conforms" : "Blocked"}</strong></article>`;
    $("data-origin").textContent =
      `${source.title} · ${source.data_origin} · as of ${source.as_of}. Tables show unmodified source records; the validation exercise changes only a temporary RDF graph.`;
    $("source-transactions").innerHTML = table(
      ["ID", "Sender", "Receiver", "Amount", "Currency", "Time", "Source ID"],
      source.transactions.map((t) => [
        t.id,
        t.sender,
        t.receiver,
        t.amount,
        t.currency,
        t.timestamp,
        t.source_id,
      ]),
    );
    $("source-accounts").innerHTML = table(
      [
        "Account",
        "Name",
        "Holder",
        "Owner",
        "Percentage",
        "Registry source",
        "Observed at",
      ],
      source.accounts.map((a) => [
        a.id,
        a.name,
        a.holder,
        a.owner ?? "Unknown",
        a.owner_percentage ?? "Unknown",
        a.ownership_source ?? "Unknown",
        a.ownership_observed_at ?? "Unknown",
      ]),
    );
    $("source-json").textContent = JSON.stringify(source, null, 2);
    $("rdf-focus").innerHTML = explored.nodes
      .map(
        (n) =>
          `<option value="${esc(n.id)}">${esc(n.id)} · ${esc(n.types.join(", "))}</option>`,
      )
      .join("");
    $("rdf-focus").value = "ex:" + source.transactions[0].id;
    renderRdfFocus();
    renderTriples();
    $("rdf-turtle").textContent = explored.turtle;
    $("validation-result").innerHTML =
      `<p class="${explored.validation.conforms ? "success" : "error"}"><b>${explored.validation.conforms ? "Conforms: investigation may proceed." : "Nonconforming: investigation is blocked."}</b></p><p>${esc(explored.mutation)}</p>${
        explored.validation.issues.length
          ? table(
              ["Focus node", "Constraint path", "Message"],
              explored.validation.issues.map((i) => [
                i.focus,
                i.path,
                i.message,
              ]),
            )
          : "<p>All configured shapes passed. This verifies structure, not the truth or completeness of external records.</p>"
      }`;
    $("sparql-source").textContent = explored.query.sparql;
    const rows = explored.query.rows;
    $("sparql-rows").innerHTML = rows.length
      ? table(
          Object.keys(rows[0]),
          rows.map((r) => Object.values(r)),
        )
      : "<p>No matching rows. An empty result is not proof that an owner or relationship does not exist outside this graph.</p>";
  } catch (e) {
    $("data-status").textContent = e.message;
  } finally {
    graphLoading = false;
    $("load-graph").disabled = false;
  }
}
function renderTriples() {
  if (!explored) return;
  const q = $("triple-filter").value.toLowerCase();
  const rows = explored.triples.filter((t) =>
    JSON.stringify(t).toLowerCase().includes(q),
  );
  $("triple-count").textContent =
    `${rows.length} / ${explored.triples.length} asserted instance triples. Vocabulary triples are in the Turtle export.`;
  $("rdf-triples").innerHTML = table(
    ["Subject", "Predicate", "Object", "Value type"],
    rows.map((t) => [
      t.subject,
      t.predicate,
      t.object,
      t.literal ? t.datatype || "string literal" : "Resource IRI",
    ]),
  );
}
function renderRdfFocus() {
  const focus = $("rdf-focus").value;
  if (!explored || !focus) return;
  const node = explored.nodes.find((n) => n.id === focus);
  const links = explored.links.filter(
    (l) => l.source === focus || l.target === focus,
  );
  const neighbors = [
    ...new Set(
      links.flatMap((l) => [l.source, l.target]).filter((id) => id !== focus),
    ),
  ];
  const coords = { [focus]: [310, 210] };
  neighbors.forEach((id, i) => {
    const a = (i / neighbors.length) * Math.PI * 2 - Math.PI / 2;
    coords[id] = [310 + 215 * Math.cos(a), 210 + 145 * Math.sin(a)];
  });
  let parts = "";
  links.forEach((link) => {
    const [x1, y1] = coords[link.source],
      [x2, y2] = coords[link.target];
    const len = Math.hypot(x2 - x1, y2 - y1),
      dx = (x2 - x1) / len,
      dy = (y2 - y1) / len;
    const x = (x1 + x2) / 2,
      y = (y1 + y2) / 2;
    parts += `<line x1="${x1 + dx * 28}" y1="${y1 + dy * 28}" x2="${x2 - dx * 34}" y2="${y2 - dy * 34}" stroke="#819aab" stroke-width="1.7" marker-end="url(#rdf-arrow)"/><rect x="${x - 55}" y="${y - 9}" width="110" height="17" rx="4" fill="#fff"/><text x="${x}" y="${y + 3}" text-anchor="middle" font-size="10">${esc(link.predicate)}</text>`;
  });
  for (const [id, [x, y]] of Object.entries(coords)) {
    const info = explored.nodes.find((n) => n.id === id);
    const type =
      info?.types.find((t) => t !== "fg:Party")?.replace("fg:", "") ||
      "Resource";
    parts += `<g role="button" tabindex="0" data-rdf-node="${esc(id)}" aria-label="Inspect ${esc(id)}"><circle cx="${x}" cy="${y}" r="28" fill="${id === focus ? "#d8f4e8" : "#eef0f8"}" stroke="${id === focus ? "#087f68" : "#827ca2"}"/><text x="${x}" y="${y + 4}" text-anchor="middle" font-size="10">${esc(id.replace("ex:", "").slice(-9))}</text><text x="${x}" y="${y + 44}" text-anchor="middle" font-size="10">${esc(id)}</text><text x="${x}" y="${y + 58}" text-anchor="middle" font-size="9" fill="#687e76">${esc(type)}</text></g>`;
  }
  $("rdf-network").innerHTML =
    `<svg viewBox="0 0 620 440" role="img" aria-label="One-hop RDF neighbourhood of ${esc(focus)}"><defs><marker id="rdf-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#819aab"/></marker></defs>${parts}</svg>`;
  const out = explored.triples.filter((t) => t.subject === focus),
    incoming = explored.triples.filter((t) => !t.literal && t.object === focus);
  $("rdf-inspector").innerHTML =
    `<h4>${esc(focus)}</h4><p class="mono">${esc(node.iri)}</p><p>${esc(node.types.join(" · "))}</p>${table(
      ["Property", "Value", "Datatype"],
      out.map((t) => [t.predicate, t.object, t.datatype ?? "Resource"]),
    )}<h4>Incoming references</h4>${
      incoming.length
        ? table(
            ["Subject", "Property"],
            incoming.map((t) => [t.subject, t.predicate]),
          )
        : "<p>No incoming references in this graph.</p>"
    }`;
  document.querySelectorAll("[data-rdf-node]").forEach((el) => {
    const select = () => {
      $("rdf-focus").value = el.dataset.rdfNode;
      renderRdfFocus();
    };
    el.addEventListener("click", select);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        select();
      }
    });
  });
}
function renderEvaluation() {
  if (!evaluationReport) return;
  const modelEntries = Object.entries(evaluationReport.models);
  const series = (field) =>
    modelEntries.map(([name, result]) => ({
      name: modelNames[name],
      color: colors[name],
      points: result.test[field],
    }));
  $("pr-chart").innerHTML = plot(series("pr_curve"), {
    baseline: modelEntries[0][1].test.prevalence,
  });
  $("roc-chart").innerHTML = plot(series("roc_curve"), {
    xLabel: "False positive rate",
    yLabel: "True positive rate",
  });
  const history = evaluationReport.training_history;
  $("training-chart").innerHTML = plot(
    [
      {
        name: `Validation AP · selected epoch ${evaluationReport.selected_epoch}`,
        color: colors.directed_graphsage,
        points: history.map((r) => [r.epoch, r.validation_ap]),
      },
    ],
    {
      xMax: history.length,
      yMax: Math.max(...history.map((r) => r.validation_ap)) * 1.15,
      xLabel: "Epoch",
      yLabel: "Validation AP",
    },
  );
  $("training-table").innerHTML = table(
    ["Epoch", "Training loss", "Validation AP"],
    history.map((r) => [
      r.epoch,
      r.loss.toFixed(6),
      r.validation_ap.toFixed(6),
    ]),
  );
  const importance =
    evaluationReport.validation_permutation.histogram_gradient_boosting.groups;
  $("importance-chart").innerHTML = bars(
    importance.map((i) => ({
      name: i.group,
      value: i.mean_ap_drop,
      color:
        i.mean_ap_drop < 0 ? "#bd7953" : colors.histogram_gradient_boosting,
    })),
    "Validation AP drop",
    Math.max(...importance.map((i) => Math.abs(i.mean_ap_drop))),
  );
  $("importance-table").innerHTML = table(
    ["Feature group", "Mean AP drop", "Std (3 repeats)"],
    importance.map((i) => [
      i.group,
      i.mean_ap_drop.toFixed(5),
      i.std.toFixed(5),
    ]),
  );
  renderBudget();
  renderConfusion();
}
function renderBudget() {
  if (!evaluationReport) return;
  const index = Number($("review-budget").value);
  const rows = Object.entries(evaluationReport.models).map(([key, value]) => ({
    key,
    ...value.test.review_budgets[index],
  }));
  $("budget-label").textContent =
    `${rows[0].percent}% · ${rows[0].reviewed.toLocaleString()} reviews`;
  $("budget-chart").innerHTML = bars(
    rows.map((r) => ({
      name: modelNames[r.key],
      value: r.true_positives,
      color: colors[r.key],
      display: `${r.true_positives} / 74 found`,
    })),
    "Positive cases found",
    74,
  );
  $("budget-table").innerHTML = table(
    [
      "Model",
      "Reviewed",
      "True positives",
      "False positives",
      "Missed positives",
      "Precision",
      "Recall",
    ],
    rows.map((r) => [
      modelNames[r.key],
      r.reviewed,
      r.true_positives,
      r.false_positives,
      r.missed,
      percent(r.precision),
      percent(r.recall),
    ]),
  );
}
function renderConfusion() {
  if (!evaluationReport) return;
  const report = evaluationReport.models[$("diagnostic-model").value].test,
    c = report.confusion;
  $("confusion-matrix").innerHTML =
    `<p>Threshold <b>${c.threshold.toFixed(6)}</b>, selected on validation F1 and fixed before test evaluation.</p><div class="confusion-grid"><article><span>True positives</span><strong>${c.tp}</strong><small>Flagged labelled positives</small></article><article><span>False positives</span><strong>${c.fp.toLocaleString()}</strong><small>Flagged labelled negatives</small></article><article><span>False negatives</span><strong>${c.fn}</strong><small>Missed labelled positives</small></article><article><span>True negatives</span><strong>${c.tn.toLocaleString()}</strong><small>Unflagged labelled negatives</small></article></div><p>Accuracy: <b>${percent(c.accuracy)}</b>. An always-negative classifier gets <b>${percent(c.all_negative_accuracy)}</b> and finds zero positives. This is why accuracy is not the model-selection objective.</p>`;
  $("calibration-table").innerHTML = table(
    ["Score bin", "Rows", "Mean score", "Observed positive rate"],
    report.calibration_bins.map((b) => [
      `${b.lower.toFixed(1)}–${b.upper.toFixed(1)}`,
      b.count,
      b.mean_score.toFixed(4),
      percent(b.positive_rate),
    ]),
  );
}
function renderExplanation(result) {
  const explanation = result.explanations,
    linear = explanation.logistic;
  $("prediction-explanation").innerHTML =
    `<h4>Logistic regression: exact log-odds reconstruction</h4><p>${esc(linear.method)}</p><p>Intercept ${linear.intercept.toFixed(5)} + all feature terms = <b>${linear.logit.toFixed(5)}</b>. Sigmoid(logit) = <b>${result.scores.logistic_regression.toFixed(6)}</b>.</p><div class="table-scroll explanation-table">${table(
      ["Feature", "Encoded value", "Weight", "Logit contribution"],
      linear.contributions.map((i) => [
        i.feature,
        i.value.toFixed(4),
        i.weight.toFixed(4),
        i.logit_contribution.toFixed(5),
      ]),
    )}</div><h4>Gradient boosting: local group sensitivity</h4><p>${esc(explanation.gradient_boosting.method)}</p><div class="table-scroll">${table(
      ["Group replaced", "Original score", "Replacement score", "Difference"],
      explanation.gradient_boosting.sensitivity.map((i) => [
        i.group,
        i.original_score.toFixed(5),
        i.reference_replacement_score.toFixed(5),
        i.score_difference.toFixed(5),
      ]),
    )}</div><h4>GraphSAGE explanation boundary</h4><p>${esc(explanation.graphsage.method)}</p>`;
}
async function loadLab() {
  try {
    const [guide, schema, data] = await Promise.all([
      request("/guide.json"),
      request("/api/ontology"),
      request("/api/cases"),
    ]);
    document.querySelectorAll("[data-guide]").forEach((el) => {
      el.innerHTML = guide[el.dataset.guide]
        .map(
          (item) =>
            `<article class="panel padded"><h3>${esc(item.title)}</h3><p>${esc(item.text)}</p></article>`,
        )
        .join("");
    });
    $("ontology-schema").innerHTML =
      `<p><b>Declared classes:</b> ${schema.classes.map(esc).join(" · ")}</p><p>Account, Person, LegalEntity, Transaction, OwnershipAssertion and Evidence are materialised. Case, RiskSignal and ScreeningCandidate are declared vocabulary for future graph persistence; current case outputs are JSON/SQL.</p><div class="table-scroll">${table(
        ["Property", "Kind", "Domain", "Range"],
        schema.properties.map((p) => [p.name, p.kind, p.domain, p.range]),
      )}</div>`;
    $("ontology-turtle").textContent = schema.ontology_turtle;
    $("shapes-turtle").textContent = schema.shapes_turtle;
    $("data-case").innerHTML = data
      .map(
        (c) =>
          `<option value="${esc(c.id)}">${esc(c.id)} · ${esc(c.title)}</option>`,
      )
      .join("");
    await loadExplorer();
    renderEvaluation();
  } catch (e) {
    $("data-status").textContent = e.message;
  }
}
$("load-graph").addEventListener("click", loadExplorer);
$("rdf-focus").addEventListener("change", renderRdfFocus);
$("triple-filter").addEventListener("input", renderTriples);
$("review-budget").addEventListener("input", renderBudget);
$("diagnostic-model").addEventListener("change", renderConfusion);
document.querySelectorAll("[data-jump]").forEach((b) =>
  b.addEventListener("click", () => {
    showView(b.dataset.jump);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }),
);
loadLab();
