"""Model-written results report: analyses every figure, CSV and result file, writes LaTeX, compiles a PDF.

Everything lands in report/:
  report.tex, report.pdf          the report
  figures/<group>/                copies of every figure (PDF/PNG) and its CSV
  data/                           copies of the result summaries the report was built from
  analysis/*.json                 the model's raw analyses (one per figure / artifact)
  tests.xml, tree.txt             test results and project tree used in the report

Design
  * Tables and numbers in tables come from the files, never from the model.
  * The model writes the prose (per-figure analysis, per-artifact analysis, architecture overview,
    abstract, discussion, limitations). Every decimal number / percentage it writes is checked
    against the numbers in the material it was given; numbers that match nothing are shown in red
    with a "?" (and counted in the report).
  * LLM calls are cached (results/llm_cache), so re-running after a small change is cheap.

    uv run python scripts/make_report.py [--report-model ollama_chat/<name>] [--report-api-base URL] [--vision]
"""
from __future__ import annotations

import argparse
import ast
import base64
import datetime as dt
import json
import math
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT, add_config_args, config_from_args  # noqa: E402
from horizon.llm import LLM  # noqa: E402
from horizon.stage2.schemas import extract_json  # noqa: E402

REPORT = ROOT / "report"
EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "llm_cache", "mlartifacts", ".pytest_cache", "raw", "report",
                "camel-prompt-injection", ".ipynb_checkpoints"}
PCT = 10 ** 2
NUM = re.compile(r"(?<![\w.])[-+]?\d+\.\d+(?:[eE][-+]?\d+)?%?|(?<![\w.])[-+]?\d+(?:[eE][-+]?\d+)?%")

SYSTEM = (
    "You are a careful research assistant helping an M.Sc. Data Science student (TU Freiberg) write the results "
    "chapter of a thesis on LLM security. The project is 'Horizon', a two-stage defense against indirect prompt "
    "injection: Stage 1 = keyed spotlighting with Clopper-Pearson, chi-square DRO and adaptive risk bounds and a "
    "Sion minimax mix pi* over spotlighting variants; Stage 2 = a math-hardened CaMeL (privileged/quarantined LLMs, "
    "majority vote with a Hoeffding bound, typed values with capability labels, a Z3-verified noninterference policy, "
    "conformal risk control of user confirmations, a Sion LP over configurations and the final value V_Horizon); plus "
    "a direct-injection/jailbreak layer (DeBERTa injection classifier, Qwen3Guard, canary tokens).\n"
    "Rules: use ONLY numbers that appear in the material you are given and copy them exactly; never invent results. "
    "If inputs are marked SIMULATED or 'theory mode', say clearly that the figure illustrates the math and is not an "
    "experimental result. If sample sizes are small, say so. Write plain prose without LaTeX or Markdown. "
    "Reply with ONLY one JSON object, no other text."
)


# =============================================================== utilities ==
def esc(s: str) -> str:
    """Escape plain text for LaTeX."""
    rep = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
           "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(s))          # control characters break TeX
    return "".join(rep.get(c, c) for c in s)


def esc_keep_math(s: str) -> str:
    """Escape text but keep $...$ segments (figure formulas are written in LaTeX math)."""
    parts = str(s).split("$")
    if len(parts) % 2 == 0:              # unbalanced dollars → treat everything as text
        return esc(s)
    return "".join(f"${p}$" if i % 2 else esc(p) for i, p in enumerate(parts))


def loads_lenient(raw: str) -> dict:
    """json.loads on the first JSON object; repairs stray backslashes (models like to write LaTeX)."""
    blob = extract_json(raw)
    blob = re.sub(r"\\([tbfr])(?=[a-zA-Z])", r"\\\\\1", blob)    # \tau, \beta, \frac, \rho are LaTeX, not escapes
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", blob))


def numbers_of(text: str) -> list[float]:
    out = []
    for tok in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text):
        try:
            v = float(tok)
            out += [v, v * PCT, v / PCT]           # a fraction may be written as a percentage and back
        except ValueError:
            pass
    return out


def checked(text: str, sources: list[float], tol: float, stats: dict) -> str:
    """Escape model text; decimals/percentages not found in the source material become red '?'-marked."""
    out, last = [], 0
    for m in NUM.finditer(text):
        out.append(esc(text[last:m.start()]))
        tok = m.group(0)
        v = float(tok.rstrip("%"))
        ok = any(math.isclose(v, s, rel_tol=tol, abs_tol=1e-9) for s in sources)
        stats["checked"] += 1
        if ok:
            out.append(esc(tok))
        else:
            stats["unverified"] += 1
            out.append(r"\unverified{" + esc(tok) + "}")
        last = m.end()
    out.append(esc(text[last:]))
    return "".join(out)


def latex_table(df: pd.DataFrame, caption: str, fmt: str = "{:.4g}", longtable: bool = False,
                spec: str | None = None) -> str:
    cols = list(df.columns)
    head = " & ".join(esc(c) for c in cols) + r" \\"
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cells.append("--" if math.isnan(v) else esc(fmt.format(v)))
            else:
                cells.append(esc(v))
        rows.append(" & ".join(cells) + r" \\")
    spec = spec or "l" * len(cols)
    if longtable:
        return ("\\begin{small}\\begin{longtable}{" + spec + "}\n\\caption{" + esc(caption) + "}\\\\\n\\toprule\n" + head
                + "\n\\midrule\\endhead\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{longtable}\\end{small}\n")
    return ("\\begin{table}[H]\\centering\\small\n\\caption{" + esc(caption) + "}\n\\resizebox{\\ifdim\\width>\\linewidth"
            "\\linewidth\\else\\width\\fi}{!}{\\begin{tabular}{" + spec + "}\n\\toprule\n" + head + "\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}}\n\\end{table}\n")


def project_tree(root: Path, depth: int) -> str:
    lines = [root.name + "/"]

    def walk(d: Path, prefix: str, level: int) -> None:
        if level >= depth:
            return
        items = sorted([p for p in d.iterdir() if p.name not in EXCLUDE_DIRS and not p.name.startswith(".")
                        and not p.name.endswith(".pyc")], key=lambda p: (p.is_file(), p.name))
        files = [p for p in items if p.is_file()]
        if len(files) > 12:                       # summarize large folders
            shown = [p for p in items if p.is_dir()] + files[:6]
            hidden = len(files) - 6
        else:
            shown, hidden = items, 0
        for i, p in enumerate(shown):
            last = i == len(shown) - 1 and hidden == 0
            lines.append(f"{prefix}{'`-- ' if last else '|-- '}{p.name}{'/' if p.is_dir() else ''}")
            if p.is_dir():
                walk(p, prefix + ("    " if last else "|   "), level + 1)
        if hidden:
            lines.append(f"{prefix}`-- ... {hidden} more files")
    walk(root, "", 0)
    return "\n".join(lines)


def module_docs(root: Path) -> pd.DataFrame:
    rows = []
    for d in ["horizon", "eval", "scripts", "tests"]:
        for p in sorted((root / d).rglob("*.py")):
            if p.name == "__init__.py":
                continue
            try:
                doc = ast.get_docstring(ast.parse(p.read_text())) or ""
            except SyntaxError:
                doc = ""
            rows.append({"file": str(p.relative_to(root)), "purpose": doc.strip().splitlines()[0] if doc else ""})
    return pd.DataFrame(rows)


def csv_summary(p: Path, n_rows: int) -> str:
    try:
        df = pd.read_csv(p)
    except Exception as e:
        return f"(unreadable CSV: {e})"
    df = df.drop(columns=[c for c in df.columns if c in ("reply", "injection", "prompt")], errors="ignore")
    parts = [f"file {p.name}: {df.shape[0]} rows × {df.shape[1]} columns: {list(df.columns)}"]
    num = df.select_dtypes("number")
    if not num.empty:
        parts.append("numeric summary:\n" + num.describe().T[["mean", "min", "max"]].round(5).to_string())
    parts.append(f"first {n_rows} rows:\n" + df.head(n_rows).round(5).to_string(index=False))
    return "\n".join(parts)


# ================================================================ artifacts ==
@dataclass
class Figure:
    group: str
    name: str
    caption: str
    png: Path
    pdf: Path
    csv: Path | None


@dataclass
class Artifact:
    key: str
    title: str
    context: str
    tables: list[str] = field(default_factory=list)


def collect_figures() -> list[Figure]:
    figs = []
    for cap in sorted((ROOT / "figures").rglob("captions.json")):
        group = "theory" if cap.parent == ROOT / "figures" else cap.parent.name
        for name, caption in json.loads(cap.read_text()).items():
            png, pdf, csv = (cap.parent / f"{name}.{e}" for e in ("png", "pdf", "csv"))
            if png.exists() and pdf.exists():
                figs.append(Figure(group, name, caption, png, pdf, csv if csv.exists() else None))
    return figs


def collect_artifacts(cfg) -> list[Artifact]:
    arts = []
    res = ROOT / "results"
    for sp in sorted((res / "stage1").glob("*/summary.json")):
        S = json.loads(sp.read_text())
        tab = pd.DataFrame(S["variants"]).T.reset_index().rename(columns={"index": "variant"})
        cols = [c for c in ["variant", "s", "n", "p_hat", "p_bar", "U", "s_a", "n_a", "p_adp_bar", "log10_K"] if c in tab]
        tab = tab[cols]
        ctx = f"Stage 1 run '{sp.parent.name}' (split {S['split']}, n = {S['n']} paired attacks, alpha = {S['alpha']}).\n"
        ctx += tab.to_string(index=False)
        for k in ["pi_star", "report", "note"]:
            if k in S:
                ctx += f"\n{k}: {json.dumps(S[k], default=str)}"
        mc = pd.Series(S.get("mcnemar", {}))
        if len(mc):
            ctx += f"\nMcNemar p-values (pairs): smallest {mc.min():.3g}, number of pairs with p<alpha: {(mc < S['alpha']).sum()} of {len(mc)}"
        arts.append(Artifact(f"stage1_{sp.parent.name}", f"Stage 1 results — {sp.parent.name}", ctx,
                             [latex_table(tab, f"Stage 1 per-variant results ({sp.parent.name}); from summary.json")]))
    ps = res / "stage1" / "pi_star.json"
    if ps.exists():
        d = json.loads(ps.read_text())
        t = pd.DataFrame([{"variant": k, "pi_star": v} for k, v in d["pi_star"].items()])
        arts.append(Artifact("pi_star", "Stage 1 → Stage 2 hand-off (π*, R̄_Q, |K|)",
                             "Stage 1 output passed to Stage 2: " + json.dumps(d, default=str),
                             [latex_table(t, f"π* chosen on '{d.get('chosen_on')}' at ρ={d['rho']}, η={d['eta']}, "
                                             f"q={d['q']}; R̄_Q={d['R_Q']:.4g}; log|K|={d['log_K']:.4g}")]))
    for ep in sorted((res / "stage2").glob("*/episodes.csv")):
        if "-" in ep.parent.name:                  # per-job folders of a campaign; merged into <split>/
            continue
        df = pd.read_csv(ep)
        agg = (df.assign(kind=df["style"].where(df["style"] == "benign", "attack"))
                 .groupby(["config", "kind"]).agg(n=("utility", "size"), utility=("utility", "mean"),
                                                   asr=("asr", "mean"), G=("G", "mean"), C=("C", "mean"),
                                                   F=("F", "mean"), seconds=("seconds", "mean"))
                 .reset_index())
        ctx = f"AgentDojo episodes ({ep.parent.name} split). Aggregates per configuration:\n" + agg.round(4).to_string(index=False)
        cal = ep.parent / "calibration.csv"
        if cal.exists():
            c = pd.read_csv(cal)
            ctx += f"\nCONFIRM events: {len(c)}, dangerous among them: {int(c['dangerous'].sum())}, mean score {c['score'].mean():.4g}"
        arts.append(Artifact(f"stage2_{ep.parent.name}", f"Stage 2 AgentDojo episodes — {ep.parent.name}", ctx,
                             [latex_table(agg, f"AgentDojo per-configuration aggregates ({ep.parent.name}); asr = attack success rate")]))
    lh = res / "stage2" / "lambda_hat.json"
    if lh.exists():
        arts.append(Artifact("lambda_hat", "Conformal calibration λ̂", "Calibrated threshold: " + lh.read_text()))
    sm = res / "smoke" / "smoke.json"
    if sm.exists():
        S = json.loads(sm.read_text())
        rows = []
        for k, v in S.items():
            rows.append({"check": k, "result": json.dumps(v, default=str)[:110]})
        arts.append(Artifact("smoke", "Smoke test — does each layer fire?", "Smoke test results: " + json.dumps(S, default=str),
                             [latex_table(pd.DataFrame(rows), "Per-layer smoke test (results/smoke/smoke.json)", longtable=True,
                                         spec="p{0.25\\linewidth}p{0.69\\linewidth}")]))
    logs = sorted((res / "camel_baseline").glob("*.log"))
    if logs:
        lines = []
        for f in logs:
            for l in f.read_text().splitlines():
                if re.search(r"utility|security", l):
                    lines.append({"run": f.stem, "line": l.strip()})
        arts.append(Artifact("camel", "CaMeL reference implementation (unmodified) baseline",
                             "CaMeL baseline on AgentDojo with the local model. 'security' is AgentDojo's attack-success "
                             "indicator (0 = no injection succeeded).\n" + pd.DataFrame(lines).to_string(index=False),
                             [latex_table(pd.DataFrame(lines), "CaMeL baseline output lines")]))
    for f in sorted((res / "testing").glob("*.csv")):
        if "_items" in f.stem:                     # per-case rows; their summaries are the other CSVs
            continue
        arts.append(Artifact(f"testing_{f.stem}", f"Testing phase — {f.stem}", csv_summary(f, cfg.report["csv_preview_rows"])))
    camp = ROOT / "report" / "campaign"
    if (camp / "summary.json").exists():
        S = json.loads((camp / "summary.json").read_text())
        proc = pd.read_csv(camp / "process.csv")
        grp = pd.read_csv(camp / "results_by_group.csv")
        cols = [c for c in ["area", "group", "config", "tests", "attacks", "got_through", "asr", "asr_cp_lo",
                            "asr_cp_hi", "benign", "benign_ok", "false_alarms"] if c in grp]
        arts.append(Artifact("campaign", "Test campaign (eval/campaign.py) — process and outcomes",
                             "Campaign summary: " + json.dumps(S, default=str) + "\nPhases:\n"
                             + proc.to_string(index=False) + "\nResults by group:\n" + grp[cols].to_string(index=False),
                             [latex_table(proc[["order", "phase", "status", "duration", "tests planned", "tests done"]],
                                          "Campaign phases in scheduling order", longtable=True),
                              latex_table(grp[cols], "Campaign results by group (got_through = attack worked)",
                                          "{:.3g}", longtable=True)]))
    return arts


def run_tests() -> tuple[pd.DataFrame, str]:
    xml = REPORT / "tests.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"], cwd=ROOT,
                   capture_output=True, text=True)
    rows = []
    for tc in ET.parse(xml).getroot().iter("testcase"):
        status = "failed" if tc.find("failure") is not None or tc.find("error") is not None else (
            "skipped" if tc.find("skipped") is not None else "passed")
        rows.append({"file": tc.get("classname", "").split(".")[-1], "test": tc.get("name"), "status": status})
    df = pd.DataFrame(rows)
    by = df.groupby(["file", "status"]).size().unstack(fill_value=0).reset_index()
    summary = f"{(df.status == 'passed').sum()} passed, {(df.status == 'failed').sum()} failed, {(df.status == 'skipped').sum()} skipped"
    return by, summary


# ================================================================== the model ==
class Analyst:
    def __init__(self, cfg):
        r = cfg.report
        mcfg = {"temperature": r["temperature"], "max_tokens": r["max_tokens"], "cache_dir": cfg.models["cache_dir"],
                "request_timeout_s": r["request_timeout_s"], "api_base": r["api_base"],
                "api_key_env": r["api_key_env"], "disable_thinking": r["disable_thinking"],
                "reasoning_effort_off": cfg.models["reasoning_effort_off"]}
        self.llm = LLM(r["model"], mcfg)
        self.r = r
        self.out = REPORT / "analysis"
        self.out.mkdir(parents=True, exist_ok=True)

    def ask(self, key: str, task: str, context: str, schema: str, image: Path | None = None) -> dict:
        context = context[: self.r["max_context_chars"]]
        text = f"{task}\n\nReply with JSON exactly of this form: {schema}\n\nMATERIAL:\n{context}"
        content = text
        if image is not None and self.r["vision"]:
            b64 = base64.b64encode(image.read_bytes()).decode()
            content = [{"type": "text", "text": text},
                       {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
        data = None
        for attempt in range(2):
            raw = self.llm.chat(msgs)
            try:
                data = loads_lenient(raw)
                break
            except Exception:
                msgs = msgs + [{"role": "assistant", "content": raw},
                               {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."}]
        if data is None:
            data = {"summary": raw}
        nested = str(data.get("summary", "")).strip()
        if nested.startswith("{"):                 # some models put the whole JSON inside "summary"
            try:
                data = {**data, **loads_lenient(nested)}
            except Exception:
                pass
        data["_context"] = context
        (self.out / f"{key}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print(f"  analysed {key}", flush=True)
        return data


FIG_SCHEMA = '{"summary": "<3-6 sentences>", "observations": ["<observation>", "..."], "caveats": ["<caveat>", "..."]}'
ART_SCHEMA = FIG_SCHEMA
ARCH_SCHEMA = '{"overview": "<one paragraph>", "components": ["<component: role>", "..."]}'
EXEC_SCHEMA = ('{"abstract": "<one paragraph>", "discussion": "<2-3 paragraphs separated by blank lines>", '
               '"limitations": ["..."], "next_steps": ["..."]}')


# ================================================================== LaTeX ==
PREAMBLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage{fontspec}
\setmainfont{%(font)s}
\setmonofont{DejaVu Sans Mono}[Scale=0.82]
\usepackage{amsmath,amssymb}
\usepackage[margin=2.1cm]{geometry}
\usepackage{graphicx,booktabs,longtable,float,xcolor,caption,listings,enumitem}
\usepackage[hidelinks]{hyperref}
\newcommand{\unverified}[1]{\textcolor{red}{#1\textsuperscript{?}}}
\lstset{basicstyle=\ttfamily\scriptsize,breaklines=true,columns=fullflexible,frame=single,rulecolor=\color{black!20}}
\setlist{itemsep=1pt,topsep=3pt}
\captionsetup{font=small}
\title{Horizon: implementation and results report\\\large RQ1 — indirect prompt injection}
\author{Generated by \texttt{scripts/make\_report.py}\\ analysis model: \texttt{%(model)s}}
\date{%(date)s}
\begin{document}
\maketitle
"""


def items(xs, sources, tol, stats) -> str:
    xs = [x for x in (xs or []) if str(x).strip()]
    if not xs:
        return ""
    return "\\begin{itemize}\n" + "\n".join(f"\\item {checked(str(x), sources, tol, stats)}" for x in xs) + "\n\\end{itemize}\n"


def analysis_block(a: dict, tol: float, stats: dict) -> str:
    src = numbers_of(a.get("_context", ""))
    s = ""
    if a.get("summary"):
        s += checked(str(a["summary"]), src, tol, stats) + "\n\n"
    if a.get("observations"):
        s += "\\textbf{Observations.}\n" + items(a["observations"], src, tol, stats)
    if a.get("caveats"):
        s += "\\textbf{Caveats.}\n" + items(a["caveats"], src, tol, stats)
    return s


GROUP_TITLE = {"theory": "Theory-mode figures (math-only or SIMULATED inputs)", "pilot": "Pilot figures (real LLM runs, small n)",
               "results": "Figures from full experiment runs"}


def main() -> None:
    ap = add_config_args(argparse.ArgumentParser())
    ap.add_argument("--report-model")
    ap.add_argument("--report-api-base")
    ap.add_argument("--vision", action="store_true")
    ap.add_argument("--no-compile", action="store_true")
    args = ap.parse_args()
    over = list(args.set)
    if args.report_model:
        over.append(f"report.model={args.report_model}")
    if args.report_api_base:
        over.append(f"report.api_base={args.report_api_base}")
    if args.vision:
        over.append("report.vision=true")
    args.set = over
    cfg = config_from_args(args)
    r = cfg.report
    tol = r["number_check_rel_tol"]
    REPORT.mkdir(exist_ok=True)
    for sub in ["figures", "data"]:
        shutil.rmtree(REPORT / sub, ignore_errors=True)
    print(f"report model: {r['model']} @ {r['api_base']} (vision={r['vision']})")

    # ---- gather ----------------------------------------------------------------
    tree = project_tree(ROOT, r["tree_depth"])
    (REPORT / "tree.txt").write_text(tree)
    mods = module_docs(ROOT)
    tests_by, tests_summary = run_tests()
    figs = collect_figures()
    arts = collect_artifacts(cfg)
    card = json.loads((ROOT / "data" / "data_card.json").read_text()) if (ROOT / "data" / "data_card.json").exists() else {}
    for f in figs:
        dst = REPORT / "figures" / f.group
        dst.mkdir(parents=True, exist_ok=True)
        for p in (f.png, f.pdf, f.csv):
            if p:
                shutil.copy2(p, dst / p.name)
    (REPORT / "data").mkdir(exist_ok=True)
    for p in list((ROOT / "results").rglob("summary.json")) + list((ROOT / "results").rglob("*.json")):
        if "llm_cache" in p.parts or "mlartifacts" in p.parts:
            continue
        shutil.copy2(p, REPORT / "data" / ("__".join(p.relative_to(ROOT / "results").parts)))

    # ---- model analyses -----------------------------------------------------------
    an = Analyst(cfg)
    stats = {"checked": 0, "unverified": 0}
    arch = an.ask("architecture", "Describe the architecture of this research code base for the thesis: what each "
                  "top-level part does and how data flows from Stage 1 to Stage 2 and the direct layer.",
                  "PROJECT TREE:\n" + tree + "\n\nMODULES (file: first docstring line):\n" + mods.to_string(index=False),
                  ARCH_SCHEMA)
    fig_an = {}
    for f in figs:
        ctx = f"Figure {f.name} (group: {GROUP_TITLE.get(f.group, f.group)}).\nCaption and formula: {f.caption}\n"
        if f.csv:
            ctx += "\nPlotted data:\n" + csv_summary(f.csv, r["csv_preview_rows"])
        fig_an[(f.group, f.name)] = an.ask(f"fig_{f.group}_{f.name}", "Analyse this figure of the thesis: what it shows, "
                                           "what the numbers say, and what a reader should conclude.", ctx, FIG_SCHEMA, f.png)
    art_an = {a.key: an.ask(f"art_{a.key}", f"Analyse this result: {a.title}.", a.context, ART_SCHEMA) for a in arts}
    digest = "\n\n".join(
        [f"[{k}] " + v.get("summary", "") for k, v in art_an.items()]
        + [f"[figure {g}/{n}] " + v.get("summary", "") for (g, n), v in fig_an.items()]
        + [f"[tests] {tests_summary}"])
    exe = an.ask("executive", "Write the abstract, a discussion of the results so far, the limitations, and concrete "
                 "next steps for this thesis chapter, based only on these analyses.", digest, EXEC_SCHEMA)

    # ---- LaTeX ------------------------------------------------------------------------
    src_digest = numbers_of(digest)
    L = [PREAMBLE % {"font": r["main_font"], "model": esc(r["model"]), "date": dt.date.today().isoformat()}]
    L.append("\\begin{abstract}\n" + checked(str(exe.get("abstract", "")), src_digest, tol, stats) + "\n\\end{abstract}\n")
    L.append("\\noindent\\small\\textit{All prose in this report was written by the analysis model "
             f"\\texttt{{{esc(r['model'])}}}; tables are computed directly from the result files. Decimal numbers and "
             "percentages in the prose are checked against the material the model was given; unmatched numbers are "
             "marked \\unverified{like this}. VERIFYSTATS}\\normalsize\n\\tableofcontents\n\\newpage\n")

    L.append("\\section{Project architecture}\n")
    src = numbers_of(arch.get("_context", ""))
    L.append(checked(str(arch.get("overview", "")), src, tol, stats) + "\n")
    L.append(items(arch.get("components"), src, tol, stats))
    for name, cap in [("pipeline", "How the whole pipeline works: (1) prepared once offline, (2) one request step by "
                                   "step, (3) testing and measurement; green boxes are math.")]:
        src_pdf = ROOT / "docs" / f"{name}.pdf"
        if src_pdf.exists():
            (REPORT / "figures" / "docs").mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_pdf, REPORT / "figures" / "docs" / src_pdf.name)
            L.append("\\begin{figure}[H]\\centering\n\\includegraphics[width=\\linewidth,height=0.8\\textheight,keepaspectratio]"
                     f"{{figures/docs/{name}.pdf}}\n\\caption{{{cap}}}\n\\end{{figure}}\n")
    L.append("\\subsection{Folder structure}\n\\begin{lstlisting}\n" + tree + "\n\\end{lstlisting}\n")
    L.append("\\subsection{Modules}\n" + latex_table(mods, "Modules and their purpose (first docstring line)", longtable=True,
                                                  spec="p{0.36\\linewidth}p{0.58\\linewidth}"))

    L.append("\\section{Configuration and data}\n")
    flags = pd.DataFrame([{"flag": k, "value": str(v)} for k, v in cfg.flags.model_dump().items()])
    L.append(latex_table(flags, "Ablation flags used for this report's configuration"))
    if card:
        dc = pd.DataFrame([{"dataset": v["name"], "license": v.get("license", ""),
                            "version": (v.get("version") or "—")[:10],
                            "size MB": round(v["size_bytes"] / 1e6, 1) if "size_bytes" in v else None,
                            "status": v.get("skipped", "downloaded")[:40]} for v in card.values()])
        L.append(latex_table(dc, "Data card (data/data_card.json)"))

    L.append("\\section{Tests}\n" + esc(f"Unit tests (§9 of the specification): {tests_summary}.") + "\n"
             + latex_table(tests_by, "pytest results per file"))

    L.append("\\section{Results}\n")
    for a in arts:
        L.append(f"\\subsection{{{esc(a.title)}}}\n")
        L += a.tables
        L.append(analysis_block(art_an[a.key], tol, stats))

    for group in sorted({f.group for f in figs}, key=lambda g: ["pilot", "results", "theory"].index(g)
                        if g in ("pilot", "results", "theory") else 9):
        L.append(f"\\section{{{esc(GROUP_TITLE.get(group, group))}}}\n")
        for f in [x for x in figs if x.group == group]:
            title = f.name.replace("_", " ")
            L.append("\\begin{figure}[H]\\centering\n"
                     f"\\includegraphics[width=\\linewidth,height=0.42\\textheight,keepaspectratio]{{figures/{group}/{f.pdf.name}}}\n"
                     f"\\caption[{esc(title)}]{{{esc_keep_math(f.caption)}}}\n\\end{{figure}}\n")
            L.append(f"\\paragraph{{Analysis of {esc(title)}.}} " + analysis_block(fig_an[(f.group, f.name)], tol, stats))

    L.append("\\section{Discussion}\n")
    for para in str(exe.get("discussion", "")).split("\n\n"):
        L.append(checked(para, src_digest, tol, stats) + "\n\n")
    L.append("\\subsection{Limitations}\n" + items(exe.get("limitations"), src_digest, tol, stats))
    L.append("\\subsection{Next steps}\n" + items(exe.get("next_steps"), src_digest, tol, stats))

    L.append("\\appendix\n\\section{Parameters (config/params.yaml)}\n\\begin{lstlisting}\n"
             + (ROOT / "config" / "params.yaml").read_text() + "\n\\end{lstlisting}\n")
    L.append("\\section{Policy table (config/policy.yaml)}\n\\begin{lstlisting}\n"
             + (ROOT / "config" / "policy.yaml").read_text() + "\n\\end{lstlisting}\n")
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    L.append("\\section{Reproducibility}\n" + esc(
        f"git commit {git or 'n/a'}; Python {sys.version.split()[0]}; dependencies pinned in uv.lock; experiment "
        f"model {cfg.models['q_llm']}; analysis model {r['model']}; seed {cfg.run['seed']}. Re-create everything with "
        "./run_all.sh (see README.md).") + "\n\\end{document}\n")

    tex = "".join(L).replace("VERIFYSTATS", esc(f"{stats['checked']} numbers checked, {stats['unverified']} unverified."))
    (REPORT / "report.tex").write_text(tex)
    print(f"wrote {REPORT / 'report.tex'}  ({stats['checked']} numbers checked, {stats['unverified']} unverified)")
    if args.no_compile:
        return
    eng = {"xelatex": "-pdfxe", "lualatex": "-pdflua", "pdflatex": "-pdf"}[r["latex_engine"]]
    p = subprocess.run(["latexmk", eng, "-interaction=nonstopmode", "-halt-on-error", "report.tex"], cwd=REPORT,
                       capture_output=True, text=True)
    if p.returncode != 0 or not (REPORT / "report.pdf").exists():
        log = (REPORT / "report.log").read_text(errors="ignore") if (REPORT / "report.log").exists() else p.stdout
        err = [l for l in log.splitlines() if l.startswith("!")][:5]
        sys.exit(f"LaTeX failed: {err or log[-1500:]}")
    print(f"wrote {REPORT / 'report.pdf'}")


if __name__ == "__main__":
    main()
