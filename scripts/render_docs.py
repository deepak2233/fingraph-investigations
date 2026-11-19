"""Render the versioned research report from Markdown and its paper register."""

import json
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "docs/ARCHITECTURE.md").read_text()
papers = json.loads((ROOT / "research.json").read_text())
register = []
for paper in papers:
    register.append(
        f"### {paper['id']} · {paper['title']} ({paper['year']})\n\n"
        f"{paper['finding']} {paper['implication']}\n\n"
        f"The main limitation is that {paper['limit'][0].lower() + paper['limit'][1:]}\n\n"
        f"Reviewed at the level of {paper['review_depth'].lower()}. [Primary source]({paper['url']})\n"
    )
source = source.replace("<!-- PAPER_REGISTER -->", "\n".join(register))
body = markdown.markdown(source, extensions=["tables", "fenced_code", "toc"])
body = body.replace(
    '<img alt="System architecture" src="../architecture.svg" />',
    "<svg" + (ROOT / "architecture.svg").read_text().split("<svg", 1)[-1],
)
css = """*{box-sizing:border-box}body{margin:0;background:#f2f5f3;color:#233b30;font:16px/1.7 Inter,Arial,sans-serif}header{background:#173f30;color:#fff;padding:48px max(24px,calc((100vw - 1050px)/2))}header p{color:#bcd9c8}main{max-width:1120px;margin:auto;padding:38px 38px 80px;background:white}h1{font-size:38px;line-height:1.2}h2{margin-top:54px;padding-top:20px;border-top:1px solid #dce6df;font-size:25px;line-height:1.3}h3{font-size:18px;margin-top:30px}p{max-width:1000px}a{color:#157050;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse;font-size:13px;margin:24px 0}th{text-align:left;background:#edf4ef}td,th{padding:10px;border:1px solid #dce6df;vertical-align:top}code{font-size:12px;background:#eef4f0;padding:2px 4px;overflow-wrap:anywhere}pre{padding:18px;background:#eef4f0;overflow:auto}svg{width:100%;height:auto}footer{padding:20px;color:#6b7c73;text-align:center}@media(max-width:700px){main{padding:20px}h1{font-size:29px}table{display:block;overflow:auto}header{padding:30px 20px}}@media print{body{background:white}main{padding:0}header{padding:20px}h2,h3{break-after:avoid}tr,svg{break-inside:avoid}}"""
page = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FinGraph research and architecture</title><style>{css}</style></head><body><header><p>FINGRAPH / ENGINEERING & RESEARCH</p><h1>From transaction evidence<br>to a recorded investigation.</h1><p>Implementation, architecture, literature, and measured model results.</p></header><main>{body}</main><footer>Deepak Yadav · FinGraph v0.3 · 19 November 2026</footer></body></html>'
(ROOT / "docs/FinGraph_Research_and_Architecture.html").write_text(page)
