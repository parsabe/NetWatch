"""Draw the Horizon pipeline as one flowchart (Graphviz) → docs/pipeline.{pdf,png,svg,dot}.

Three bands, read top to bottom:
  ① BEFORE  — prepared once, offline: data, Stage 1 experiments → π*, calibration → λ̂, Z3 proof of the rules
  ② DURING  — one user request, steps 1–10 through the guards, the CaMeL agent and Stage 1 marking
  ③ AFTER   — testing: attack tools against 5 defence setups, counted and bounded → reports

Colors: green = math · lavender = AI model · rose = guard / attack tool · white = program code · beige = data.

    uv run python scripts/make_pipeline_diagram.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"
NAME = "pipeline"

LLM = 'fillcolor="#EEEDFB" color="#5B4FC9" fontcolor="#2E2780"'
MATH = 'fillcolor="#E1F3EE" color="#0F6B50" fontcolor="#0B4A37"'
CODE = 'fillcolor="#FFFFFF" color="#A8A6A0" fontcolor="#1F1F1E"'
GUARD = 'fillcolor="#FBEAF0" color="#BE185D" fontcolor="#7A0F3C"'
DATA = 'fillcolor="#F6F5F2" color="#A8A6A0" fontcolor="#1F1F1E"'
GREEN, PURPLE, GREY = "#0F6B50", "#5B4FC9", "#7A7974"

HEAD = """digraph G {
  graph [fontname="DejaVu Sans" fontsize=13 rankdir=TB nodesep=0.45 ranksep=0.42 pad=0.35 splines=spline
         newrank=true labelloc=t labeljust=c label=<%s>];
  node  [fontname="DejaVu Sans" fontsize=12 shape=box style="rounded,filled" penwidth=1.5 margin="0.20,0.10"];
  edge  [fontname="DejaVu Sans" fontsize=10 color="#7A7974" arrowsize=0.8 penwidth=1.3 fontcolor="#52514E"];
"""


def n(name: str, title: str, sub: str = "", style: str = CODE, shape: str = "") -> str:
    sub_html = f'<BR/><FONT POINT-SIZE="10">{sub}</FONT>' if sub else ""
    shp = f" shape={shape}" if shape else ""
    return f'  {name} [label=<<B>{title}</B>{sub_html}> {style}{shp}];\n'


def band(name: str, title: str, color: str, body: str) -> str:
    return (f'  subgraph cluster_{name} {{ label=<<B>{title}</B>> style="rounded,dashed" color="{color}" '
            f'fontsize=15 margin=16;\n{body}  }}\n')


def math_link(a: str, b: str, label: str = "") -> str:
    lab = f' label="{label}"' if label else ""
    return f'  {a} -> {b} [style=dashed color="{GREEN}" arrowhead=none constraint=false{lab}];\n'


def flowchart() -> str:
    g = HEAD % ('<B>Horizon — how the whole pipeline works</B><BR/><FONT POINT-SIZE="11">'
                '<FONT COLOR="#0F6B50"><B>green = math</B></FONT> · lavender = AI model · rose = guard / attack tool · '
                'white = program code · beige = data</FONT>')

    # ------------------------------------------------------------- ① BEFORE (offline)
    b = n("data", "Datasets", "indirect: BIPIA · LLMail-Inject<BR/>direct: HackAPrompt · HarmBench · JBB · Gandalf · "
          "XSTest · Alpaca", DATA)
    b += n("s1run", "Stage 1 experiment", "every attack document × 8 markings τ<BR/>(delimiters · ^ · N<SUP>k</SUP> · "
           "ROT13 · shift · scramble · Base64)<BR/>new secret key κ each time · Qwen3-8B answers", LLM)
    b += n("s1math", "Stage 1 maths", "attack rate bound p̄ = Beta<SUP>−1</SUP>(1−α; s+1, n−s)<BR/>χ²-DRO  g<SUB>ρ</SUB>(p̄) "
           "= p̄ + √(2ρp̄(1−p̄))<BR/>forging q/|K<SUB>τ</SUB>| · adaptive attacker p̄<SUP>adp</SUP><BR/>"
           "R̄<SUB>τ</SUB> = min{1, q/|K<SUB>τ</SUB>| + max(g<SUB>ρ</SUB>, p̄<SUP>adp</SUP>)}", MATH)
    b += n("sion1", "Sion game (level 1)", "V<SUB>1</SUB> = min<SUB>π</SUB> max<SUB>attack</SUB>  (cvxpy SOCP)<BR/>"
           "→ <B>π*</B> = best random mix of markings", MATH)
    b += n("cal", "Calibration", "conformal risk control on agent runs<BR/>"
           "λ̂ = smallest λ with (n/(n+1))R̂(λ) + 1/(n+1) ≤ α<SUB>user</SUB>", MATH)
    b += n("z3", "Rule table proof", "Z3 proves config/policy.yaml:<BR/>untrusted data ⇒ never ALLOW", MATH)
    b += "  data -> s1run -> s1math -> sion1 -> cal;\n  {rank=same; s1run; z3}\n"
    g += band("before", "① BEFORE — prepared once (offline)", GREY, b)

    # ------------------------------------------------------------- ② DURING (one request)
    d = n("user", "User request", "e.g. “summarise my inbox”  (trusted)", DATA)
    d += n("ig", "1 · Input guard", "DeBERTa injection classifier<BR/>blocks a jailbreak / injection typed by the user", GUARD)
    d += n("pllm", "2 · Planner AI (P-LLM)", "sees ONLY the request, never outside data<BR/>writes a small program", LLM)
    d += n("interp", "3 · CaMeL interpreter", "runs the program, calls tools,<BR/>remembers where every value came from", CODE)
    d += n("ext", "Outside data", "emails · web pages · tool results<BR/>(UNTRUSTED — may hide an attack)", DATA)
    d += n("qllm", "4 · Reader AIs (Q-LLM) × m", "each reader: own marking τ ~ π* and own key κ<BR/>"
           "no tools · must answer in a fixed format", LLM)
    d += n("vote", "5 · Majority vote", "&gt; m/2 readers agree → value, else refused", CODE)
    d += n("label", "6 · Label the value", "SYSTEM ⊑ USER ⊑ UNTRUSTED<BR/>mixed data keeps the strictest label", CODE)
    d += n("rules", "7 · Rule check", "rule table per tool", CODE)
    d += n("dec", "decision?", "", CODE, shape="diamond")
    d += n("conf", "8 · Ask or deny?", "readers' agreement s(x) ≥ λ̂ ?", MATH, shape="diamond")
    d += n("ask", "ask YOU", "approve / decline", CODE)
    d += n("deny", "DENY", "action blocked", CODE)
    d += n("tool", "9 · Tool runs", "AgentDojo world (simulated, safe)", CODE)
    d += n("og", "10 · Output guard", "Qwen3Guard (harmful?) + canary<BR/>(secret code word leaked?)", GUARD)
    d += n("ans", "Answer", "", DATA)
    # math beside the steps
    d += n("hoef", "Hoeffding", "P(vote fooled) ≤ exp(−2m(½ − R̄<SUB>Q</SUB>)²)", MATH)
    d += n("leak", "Leakage", "≤ Σ log<SUB>2</SUB>|S| bits per answer<BR/>≤ leakage_cap_bits", MATH)
    d += n("join", "Label join", "cap(f(v<SUB>1..k</SUB>)) = ⊔ cap(v<SUB>i</SUB>)", MATH)
    d += ("  user -> ig -> pllm -> interp; interp -> qllm [label=\" reads untrusted data\"]; ext -> qllm;\n"
          "  qllm -> vote -> label -> rules -> dec;\n"
          "  dec -> tool [label=\" ALLOW\"]; dec -> deny [label=\" DENY\"]; dec -> conf [label=\" CONFIRM\"];\n"
          "  conf -> ask [label=\" yes\"]; conf -> deny [label=\" no\"]; ask -> tool [label=\" approved\"];\n"
          "  tool -> og -> ans;\n")
    d += math_link("vote", "hoef") + math_link("qllm", "leak") + math_link("label", "join")
    d += "  {rank=same; vote; hoef}\n  {rank=same; qllm; leak; ext}\n  {rank=same; label; join}\n"
    g += band("during", "② DURING — one request, step by step (online)", PURPLE, d)

    # ------------------------------------------------------------- ③ AFTER (testing)
    a = n("tools", "Attack tools", "AgentDojo injections · PyRIT XPIA (indirect)<BR/>PyRIT PAIR · TAP · Crescendo "
          "(jailbreaks)<BR/>garak probes · guard detection on datasets", GUARD)
    a += n("setups", "5 defence setups (same attacks)", "none · stage1 · stage2 · stage1+2 · full<BR/>"
           "each test = one request through ②", CODE)
    a += n("count", "Count &amp; bound", "attack got through? → ASR = s/n<BR/>95 % Clopper–Pearson interval · "
           "McNemar · bootstrap<BR/>loss ℓ = G + a·C + h·F", MATH)
    a += n("sion2", "Sion game (level 2) + Horizon value", "min over π∈Π, max over w of Σ w ℓ̄  (scipy LP)"
           "<BR/>V<SUB>H</SUB> = min{1, Σπ q/|K| + max(E[L] + √(2ρ Var L), Σπ ℓ̄<SUP>adp</SUP>)}", MATH)
    a += n("out", "Results", "./run_campaign.sh → report/campaign/ (process + through / blocked)<BR/>"
           "figures F1–F18 · report/report.pdf (written by the colibri model) · MLflow", DATA)
    a += "  tools -> setups -> count -> sion2 -> out;\n"
    g += band("after", "③ AFTER — test and measure", GREY, a)

    # ------------------------------------------------------------- links between the bands
    g += f'  sion1 -> qllm [color="{PURPLE}" penwidth=2.2 label=" π* (which markings, how often)"];\n'
    g += f'  cal -> conf [color="{GREEN}" penwidth=2 style=dashed label=" λ̂"];\n'
    g += f'  z3 -> rules [color="{GREEN}" penwidth=2 style=dashed label=" proven rules"];\n'
    g += "  cal -> user [style=invis weight=10];\n  z3 -> user [style=invis];\n"     # ① above ②
    g += f'  ans -> tools [style=invis];\n'
    g += "}\n"
    return g


def render(name: str, src: str) -> None:
    OUT.mkdir(exist_ok=True)
    dot = OUT / f"{name}.dot"
    dot.write_text(src)
    for fmt in ["pdf", "png", "svg"]:
        extra = ["-Gdpi=170"] if fmt == "png" else []
        subprocess.run(["dot", f"-T{fmt}", *extra, str(dot), "-o", str(OUT / f"{name}.{fmt}")], check=True)
    print(f"wrote docs/{name}.(pdf|png|svg|dot)")


def main() -> None:
    render(NAME, flowchart())


if __name__ == "__main__":
    sys.exit(main())
