"""Draw the Horizon pipeline diagrams (Graphviz) into docs/ as PDF + PNG + SVG (+ the .dot source).

  docs/pipeline_runtime.*    one request: direct layer → Stage 2 (CaMeL) ← Stage 1, with every math tool
  docs/pipeline_workflow.*   what ./run_all.sh does, from setup to the model-written PDF report

Colors follow the Stage 1/2 slide diagrams: lavender = LLM / spotlighting, mint = math tool,
white = code / data, rose = direct / jailbreak layer (not part of the Horizon math).

    uv run python scripts/make_pipeline_diagram.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"

LLM = 'fillcolor="#EEEDFB" color="#5B4FC9" fontcolor="#2E2780"'
MATH = 'fillcolor="#E1F3EE" color="#0F6B50" fontcolor="#0B4A37"'
CODE = 'fillcolor="#FFFFFF" color="#A8A6A0" fontcolor="#1F1F1E"'
DIRECT = 'fillcolor="#FBEAF0" color="#BE185D" fontcolor="#7A0F3C"'
DATA = 'fillcolor="#F6F5F2" color="#A8A6A0" fontcolor="#1F1F1E"'

HEAD = """digraph G {
  graph [fontname="DejaVu Sans" fontsize=12 rankdir=%s nodesep=0.35 ranksep=0.45 pad=0.3 splines=spline
         labelloc=t labeljust=l label=<%s>];
  node  [fontname="DejaVu Sans" fontsize=11 shape=box style="rounded,filled" penwidth=1.4 margin="0.18,0.08"];
  edge  [fontname="DejaVu Sans" fontsize=9 color="#7A7974" arrowsize=0.7 penwidth=1.1 fontcolor="#52514E"];
"""


def n(name: str, title: str, sub: str, style: str) -> str:
    sub_html = f'<BR/><FONT POINT-SIZE="9">{sub}</FONT>' if sub else ""
    return f'  {name} [label=<<B>{title}</B>{sub_html}> {style}];\n'


def math_edge(a: str, b: str, label: str = "") -> str:
    lab = f' label="{label}"' if label else ""
    return f'  {a} -> {b} [style=dashed color="#0F6B50" arrowhead=none constraint=false{lab}];\n'


def runtime() -> str:
    g = HEAD % ("TB", '<B>Horizon — runtime pipeline (one request)</B><BR/><FONT POINT-SIZE="10">'
                'indirect injection = Stage 1 + Stage 2 (Horizon math) · direct injection / jailbreak = rose layer</FONT>')
    # ---------------- Stage 1 (offline, chooses π*) -----------------
    g += '  subgraph cluster_s1 { label=<<B>Stage 1 — keyed spotlighting (offline, Slides 11–27)</B>> style="rounded,dashed" color="#5B4FC9" fontsize=11;\n'
    g += n("s1data", "Data", "BIPIA clean docs · LLMail-Inject + BIPIA canary attacks", DATA)
    g += n("s1def", "8 defenses τ, fresh key κ", "none · delimit · ^ · dyn. marker N<SUP>k</SUP> · ROT13<BR/>shift 25 · scramble |Σ|! · Base64", LLM)
    g += n("s1forge", "Forging", "q / |K<SUB>τ</SUB>|  (log-space)", MATH)
    g += n("s1cp", "ASR bound", "Clopper–Pearson Beta<SUP>−1</SUP>(1−α; s+1, n−s)", MATH)
    g += n("s1dro", "χ²-DRO", "p̄ + √(2ρ p̄(1−p̄))", MATH)
    g += n("s1adp", "Adaptive attacker", "argmax<SUB>δ∈Δ_B</SUB> E<SUB>κ</SUB>[A] → p̄<SUP>adp</SUP>", MATH)
    g += n("s1comb", "Combined risk", "R̄<SUB>τ</SUB>(ρ) = min{1, q/|K| + max(DRO, adaptive)}", MATH)
    g += n("s1choice", "Choice rule", "τ* = argmin R̄  s.t.  U ≥ U<SUB>none</SUB> − η", MATH)
    g += n("s1sion", "Sion game, level 1", "V<SUB>1</SUB> SOCP on paired A[x, τ] → π*", MATH)
    g += n("s1out", "Stage 1 outputs", "π* · |K| = min |K<SUB>τ</SUB>| · R̄<SUB>Q</SUB> = Σ π* R̄<SUB>τ</SUB>", CODE)
    g += "  s1data -> s1def; s1def -> {s1forge s1cp s1dro s1adp}; {s1forge s1cp s1dro s1adp} -> s1comb;\n"
    g += "  s1comb -> {s1choice s1sion}; {s1choice s1sion} -> s1out;\n  }\n"
    # ---------------- runtime chain -----------------
    g += n("user", "User prompt u", "trusted channel", CODE)
    g += n("fw", "Input guard", "LlamaFirewall + Prompt Guard 2", DIRECT)
    g += n("pllm", "Privileged LLM", "sees ONLY u → writes a program", LLM)
    g += n("interp", "CaMeL interpreter", "tracks data + control-flow dependencies", CODE)
    g += n("ext", "External data", "emails · web · tool outputs (untrusted)", DATA)
    g += n("qllm", "Quarantined LLM × m", "each run: own τ ~ π*, own fresh κ<BR/>no tools · typed JSON schema", LLM)
    g += n("vote", "Majority vote", "&gt; m/2 agree, else refuse · score s(x) = agreement", CODE)
    g += n("typed", "Typed value + capability", "v = (data, type, cap) · cap ∈ {SYSTEM ⊑ USER ⊑ UNTRUSTED}", CODE)
    g += n("policy", "Policy check", "YAML rule table per tool + role rules", CODE)
    g += n("allow", "ALLOW", "policy met", CODE)
    g += n("deny", "DENY", "violation", CODE)
    g += n("ask", "CONFIRM → ask user", "only if s(x) ≥ λ̂, else DENY", CODE)
    g += n("tool", "Tool call", "AgentDojo environment", CODE)
    g += n("og", "Output guard", "canary token check + Llama Guard", DIRECT)
    g += n("ans", "Answer", "", CODE)
    g += "  user -> fw -> pllm -> interp; ext -> interp; interp -> qllm [label=\" untrusted x\"]; qllm -> vote -> typed -> policy;\n"
    g += "  policy -> {allow deny ask}; allow -> tool; ask -> tool [label=\" approved\"]; tool -> og -> ans;\n"
    g += '  s1out -> qllm [style=dashed color="#5B4FC9" label=" π*, |K|"];\n'
    # ---------------- math tools on the right -----------------
    g += n("mforge", "Forging", "Σ π*· q / |K<SUB>τ</SUB>|", MATH)
    g += n("mleak", "QIF leakage", "≤ Σ log<SUB>2</SUB>|S| bits · cap per config", MATH)
    g += n("mhoef", "Hoeffding", "exp(−2m(½ − R̄<SUB>Q</SUB>)²) · vs empirical", MATH)
    g += n("mz3", "Noninterference", "Z3 proof at startup: UNTRUSTED ⇒ ¬ALLOW", MATH)
    g += n("mcrc", "Conformal risk control", "λ̂: (n/(n+1))R̂ + 1/(n+1) ≤ α<SUB>user</SUB>", MATH)
    g += n("mfinal", "Horizon risk + Sion game", "loss ℓ = G + a·C + h·F  ·  Sion LP → π* (Horizon), w*<BR/><BR/>V (Horizon) = min{1, Σπ q/|K| + max(E[L] + √(2ρ Var L), Σπ ℓ̄ adp)}", MATH)
    g += math_edge("qllm", "mforge") + math_edge("qllm", "mleak") + math_edge("vote", "mhoef")
    g += math_edge("policy", "mz3") + math_edge("ask", "mcrc")
    g += '  s1out -> mhoef [style=dashed color="#5B4FC9" label=" R̄Q" constraint=false];\n'
    g += '  {tool deny ask} -> mfinal [style=dashed color="#0F6B50" label=" per-attack G, C, F"];\n'
    g += "  {rank=same; qllm; mforge; mleak}\n  {rank=same; vote; mhoef}\n  {rank=same; policy; mz3}\n  {rank=same; ask; mcrc}\n"
    # legend
    g += '  subgraph cluster_leg { label=<<B>Legend</B>> style="rounded" color="#D8D6D0" fontsize=10;\n'
    g += n("l1", "LLM / spotlighting", "", LLM) + n("l2", "Math tool", "", MATH) + n("l3", "Code / data", "", CODE)
    g += n("l4", "Direct / jailbreak layer", "not in the Horizon math", DIRECT)
    g += "  l1 -> l2 -> l3 -> l4 [style=invis];\n  }\n}\n"
    return g


def workflow() -> str:
    g = HEAD % ("TB", '<B>Horizon — research workflow (./run_all.sh)</B><BR/><FONT POINT-SIZE="10">'
                'quick = default · pilot = + small real runs · full = + every §11 experiment</FONT>')
    g += n("setup", "1  Setup", "scripts/setup.sh: uv · Python 3.11 · uv.lock · CaMeL (pinned) · Ollama + Qwen3-8B · Jupyter kernel", CODE)
    g += n("data", "2  Data + data card", "BIPIA · LLMail-Inject · HarmBench · JBB · deepset · Gandalf · Tensor Trust · XSTest · Alpaca", DATA)
    g += n("splits", "3  Fixed splits", "fit → choose τ*, π*, λ̂ · report → evaluate at fixed choices · paired design", DATA)
    g += n("serve", "4  Model serving", "Ollama (GPU) + developer→system role proxy · colibri (report model)", CODE)
    g += n("tests", "5  Unit tests (§9)", "77 tests · slide values only in tests/ (grep check)", MATH)
    g += n("theory", "6  Theory-mode figures", "F1–F15 from params.yaml (SIMULATED inputs labelled)", MATH)
    g += n("smoke", "7  Smoke test", "quarantine + vote · policy · Z3 refusal · canary · guards", CODE)
    g += '  subgraph cluster_exp { label=<<B>8  Experiments (level pilot / full)</B>> style="rounded,dashed" color="#5B4FC9" fontsize=11;\n'
    g += n("camel", "CaMeL baseline", "reference code, unmodified", LLM)
    g += n("s1run", "Stage 1 runner", "paired A[x, τ] · utility U<SUB>τ</SUB> · adaptive attacker<BR/>fit: π*, τ* → report: evaluated", LLM)
    g += n("cal", "Calibrate λ̂", "CRC on the calibration split", MATH)
    g += n("ablate", "AgentDojo ablations", "none · Stage 1 · Stage 2 · Stage 1+2 · full<BR/>static, then adaptive attacks", LLM)
    g += n("jb", "Jailbreak runner", "detection / FPR · PyRIT PAIR, TAP, Crescendo · garak", DIRECT)
    g += "  camel -> s1run; s1run -> cal [label=\" pi_star.json\"]; cal -> ablate [label=\" lambda_hat.json\"]; ablate -> jb;\n  }\n"
    g += n("figs", "9  Figures", "F1–F18 · PDF + PNG + CSV · formula + parameter footer", MATH)
    g += n("mlflow", "MLflow", "config · seed · model · split · per-example outcomes", DATA)
    g += n("nb", "10  Notebook", "Horizon.ipynb executed: every step with outputs", CODE)
    g += n("report", "11  Model-written report", "Qwen3.6-35B-A3B (colibri) analyses every figure, CSV, result<BR/>tables from files · numbers checked → LaTeX → report.pdf", LLM)
    g += "  setup -> data -> splits -> serve -> tests -> theory -> smoke -> camel;\n"
    g += "  jb -> figs -> nb -> report; {s1run ablate jb} -> mlflow [style=dashed]; mlflow -> report [style=dashed];\n}\n"
    return g


def render(name: str, src: str) -> None:
    OUT.mkdir(exist_ok=True)
    dot = OUT / f"{name}.dot"
    dot.write_text(src)
    for fmt in ["pdf", "png", "svg"]:
        extra = ["-Gdpi=200"] if fmt == "png" else []
        subprocess.run(["dot", f"-T{fmt}", *extra, str(dot), "-o", str(OUT / f"{name}.{fmt}")], check=True)
    print(f"wrote docs/{name}.(pdf|png|svg|dot)")


def main() -> None:
    render("pipeline_runtime", runtime())
    render("pipeline_workflow", workflow())


if __name__ == "__main__":
    sys.exit(main())
