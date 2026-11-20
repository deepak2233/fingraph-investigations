const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const script = readFileSync(path.join(__dirname, "../app.js"), "utf8");

function workbench(hash, { published = true, fetch } = {}) {
  const location = new URL(`https://fingraph.example/?case=FG-004${hash}`);
  if (published) location.searchParams.set("mode", "published");
  const entries = [];
  const views = ["overview", "data", "models", "investigation"].map((id) => ({
    id,
    classList: { toggle(name, value) { this[name] = value; } },
  }));
  const buttons = views.map(({ id }) => Object.assign(new EventTarget(), {
    dataset: { view: id },
    classList: { toggle(name, value) { this[name] = value; } },
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { delete this[name]; },
  }));
  const elements = new Map();
  const document = Object.assign(new EventTarget(), {
    currentScript: { src: new URL("/app.js", location).href },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new EventTarget());
      return elements.get(id);
    },
    querySelectorAll(selector) {
      return { ".view": views, ".nav": buttons }[selector];
    },
  });
  const window = Object.assign(new EventTarget(), { scrollTo() {} });
  const history = {
    pushState(_state, _unused, hash) {
      location.hash = hash;
      entries.push(hash);
    },
  };
  const rendered = [];
  document.addEventListener("fingraph:view", (event) => rendered.push(event.detail));
  const context = {
    document, location, history, window, URL, URLSearchParams, CustomEvent,
    fetch, AbortController, setTimeout, clearTimeout,
  };
  runInNewContext(script, context);
  return {
    entries,
    rendered,
    location,
    modeLink: elements.get("demo-mode-link"),
    demoAction: context.demoAction,
    demoOrigin: context.demoOrigin,
    visible: () => views.filter((view) => !view.classList.hidden).map((view) => view.id),
    active: () => buttons.find((button) => button["aria-current"] === "page")?.dataset.view,
    click(view) {
      buttons.find((button) => button.dataset.view === view).dispatchEvent(new Event("click"));
    },
    navigate(hash, event = "hashchange") {
      location.hash = hash;
      window.dispatchEvent(new Event(event));
    },
  };
}

test("direct links, buttons, and browser history keep the section in sync", () => {
  const app = workbench("#data");
  assert.deepEqual(app.visible(), ["data"]);
  app.navigate("#models");
  assert.deepEqual(app.visible(), ["models"]);
  assert.equal(app.active(), "models");
  app.click("overview");
  assert.equal(app.location.hash, "#overview");
  assert.deepEqual(app.entries, ["#overview"]);
  app.navigate("#models", "popstate");
  app.navigate("#models");
  assert.deepEqual(app.visible(), ["models"]);
  assert.deepEqual(app.rendered, ["data", "models", "overview", "models"]);
  assert.equal(app.modeLink.href, "https://fingraph.example/?case=FG-004#models");
});

test("evidence citations preserve the investigation and its anchor", () => {
  const app = workbench("#investigation");
  app.navigate("#evidence-E-T003");
  assert.deepEqual(app.visible(), ["investigation"]);
  assert.equal(app.location.hash, "#evidence-E-T003");
  assert.deepEqual(app.rendered, ["investigation"]);
  assert.deepEqual(app.entries, []);
  app.navigate("#unknown");
  assert.deepEqual(app.visible(), ["overview"]);
});

const graphParams = "action=rdf-explorer&case_id=FG-004&variant=missing_evidence&query=ownership";
const graphRoute = `/api/explorer?${graphParams}`;
const graphEntry = `/api/index.py?${graphParams}`;

test("a missing live RDF route tries the live function entry before using snapshots", async () => {
  const fixture = { case_id: "FG-004", variant: "missing_evidence" };
  const urls = [];
  const app = workbench("#data", {
    published: false,
    async fetch(url) {
      urls.push(url);
      return url === graphRoute
        ? Response.json({ detail: "Unknown asset" }, { status: 404 })
        : Response.json(fixture);
    },
  });
  const response = await app.demoAction(graphRoute, {}, async () => {
    assert.fail("The live function entry supplied the result.");
  }, false, graphEntry);
  assert.deepEqual(urls, [graphRoute, graphEntry]);
  assert.deepEqual(response.result, fixture);
  assert.equal(app.demoOrigin(response), "Live API");
});

test("two missing live RDF routes fall back to the matching published exercise", async () => {
  const fixture = { case_id: "FG-004", variant: "missing_evidence" };
  const urls = [];
  const app = workbench("#data", {
    published: false,
    async fetch(url) {
      urls.push(url);
      return Response.json({ detail: "Unknown asset" }, { status: 404 });
    },
  });
  const response = await app.demoAction(graphRoute, {}, async () => fixture, false, graphEntry);
  assert.deepEqual(urls, [graphRoute, graphEntry]);
  assert.equal(response.result, fixture);
  assert.equal(response.origin, "Published example");
  assert.match(app.demoOrigin(response), /Live API unavailable \(HTTP 404\)\. Showing a published example/);
});

test("an invalid live query still reports the validation error", async () => {
  const app = workbench("#data", {
    published: false,
    async fetch() {
      return Response.json({ detail: "Invalid query" }, { status: 422 });
    },
  });
  await assert.rejects(
    app.demoAction("/api/explorer/FG-004?query=invalid", {}, async () => {
      assert.fail("A rejected query should not use a saved result.");
    }),
    { message: "Request failed (422). Invalid query" },
  );
});
