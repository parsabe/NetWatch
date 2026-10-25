"""Horizon — interactive demo shell (menu-driven, plain language).

    ./horizon.sh                 opens the menu
    ./horizon.sh help            every project command

Menu
  1  Attack a DOCUMENT   (Stage 1)  outside text with hidden instructions → is the AI fooled?
  2  Attack an AGENT     (Stage 2)  an AI with tools reads poisoned data → can it be made to act?
  3  Try a JAILBREAK     (direct)   you are the attacker → do the input/output guards stop you?
  4  Quick self-test                unit tests + smoke test
  5  Results & reports              saved experiments, figures, the PDF
  6  Settings                       simple toggles (variant, readers m, policy, …)
  7  Help & glossary                what every word / symbol means
  8  Command mode                   type commands directly (advanced)
Every step is written to results/shell/session_*.jsonl.
"""
from __future__ import annotations

import argparse
import cmd
import json
import shlex
import subprocess
import sys
import traceback
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import DEFAULT_PARAMS, ROOT  # noqa: E402
from horizon.llm import LLM, server_alive  # noqa: E402
from horizon.shell_engine import (LAMBDA_HAT, PI_STAR, DirectEngine, SessionLog, Stage1Engine,  # noqa: E402
                                  Stage2Engine, attacker_entities, input_guard_accessible, load_samples, output_guard_accessible, pi_star_info,
                                  prepare_config, scan_untrusted)
from horizon.stage2.vote import hoeffding_vote_bound  # noqa: E402

con = Console(highlight=False)
DECISION = {"ALLOW": "green", "DENY": "red", "CONFIRM": "yellow"}
JAILBREAK_FILE = "demo/jailbreak_samples.txt"

OVERVIEW = """\
**Horizon in 30 seconds.** An AI assistant reads things it did not write: emails, web pages, chat messages.
An attacker can hide instructions in that text (*indirect prompt injection*), or the user can attack the AI
directly (*jailbreak*). Horizon defends in layers:

* **Direct layer** — checks what *you* type (injection classifier) and what the AI answers (canary token, Qwen3Guard).
* **Stage 1 — keyed spotlighting** — outside text is scrambled/marked with a *secret key that changes every time*,
  so the AI can tell "data" from "instructions", and the attacker cannot fake the marking.
* **Stage 2 — safe agent (CaMeL)** — a *planner AI* that only ever sees your request writes a small program.
  Outside data is read only by *isolated reader AIs* (no tools, several copies with different keys, they vote).
  Every value carries a *label* (yours / system / untrusted). A *rule table, mathematically proven by Z3*,
  never lets untrusted data trigger an action on its own — it is blocked or **you** are asked.
"""

GLOSSARY = {
    "indirect injection": "Hidden instructions inside outside content (email, web page, tool output) that the AI reads.",
    "jailbreak / direct injection": "The user themself tries to make the AI break its rules.",
    "outside source": "Any text the AI reads that you did not write: documents, emails, web pages, tool results.",
    "canary": "A random code word planted in an attack. If it shows up in the AI's answer, the attack worked.",
    "τ (variant / marking)": "How outside text is marked: delimiters, a marker between words, a cipher, Base64, …",
    "κ (key)": "The secret for this one request (e.g. which marker / which scrambling). New every time.",
    "|K| (key space)": "How many possible keys exist. Bigger = harder for an attacker to guess or fake.",
    "π* (mix)": "The best random mix of markings, chosen by the Stage 1 maths (Sion game) on the fit data.",
    "p̂ / ASR": "Attack success rate: fraction of attacks that worked in a test.",
    "p̄ (Clopper–Pearson)": "Honest upper limit of the true success rate given a small test (like a poll's margin).",
    "DRO g_ρ(p̄)": "Worst-case success rate if real attacks differ from the test set by up to ρ.",
    "R̄ (combined risk)": "Key-guessing chance + the worse of DRO and the smart-attacker rate, capped at 1.",
    "planner (P-LLM)": "The AI that turns your request into a program. It never sees outside data.",
    "reader (Q-LLM)": "An isolated AI that only extracts a value (e.g. a date) from outside data. No tools.",
    "m (readers)": "How many reader copies run on the same data, each with its own key. They vote.",
    "vote": "A value is accepted only if more than half of the readers agree; otherwise it is refused.",
    "Hoeffding bound": "Maths limit on how often the vote can be fooled, if each reader fails < 50 % independently.",
    "label (capability)": "Every value is tagged SYSTEM, USER or UNTRUSTED. Mixing data keeps the strictest tag.",
    "control dependency": "A tool call inside an `if` that depends on untrusted data is itself untrusted.",
    "policy (rules)": "Rule table per tool: ALLOW, DENY, or CONFIRM (ask you).",
    "Z3 proof": "Checked at startup: no rule ever ALLOWs a call that depends on untrusted data.",
    "λ̂ (conformal)": "Threshold on reader agreement: below it a risky call is denied, above it you are asked.",
    "G / C / F": "Loss terms: G = attack ran without asking, C = attack reached your approval, F = readers fooled.",
    "V_Horizon": "The final guaranteed ceiling on how often the whole system can be hijacked.",
    "input guard (DeBERTa)": "A small classifier (ProtectAI) that scores what YOU type: 'injection' or 'safe'.",
    "output guard (Qwen3Guard)": "A safety model that labels the AI's answer Safe / Controversial / Unsafe.",
    "XPIA (PyRIT)": "Microsoft's red-team tool plants an attack inside a document, then Horizon processes it.",
    "garak": "A scanner with ready-made attack probes (jailbreaks and hidden-document attacks).",
    "campaign": "The big test run (./run_campaign.sh): thousands of tests, then a report of what got through.",
}

ALL_COMMANDS = """\
[bold]Project commands[/] (normal terminal, inside the Prompt_Injection folder)
  ./horizon.sh                 this menu
  ./horizon.sh help            list of all project commands
  ./horizon.sh setup           install everything (safe to re-run)
  ./horizon.sh test            unit tests
  ./horizon.sh run quick       tests + figures + smoke test + PDF report   (pilot | full = bigger experiments)
  ./horizon.sh report          rewrite report/report.pdf from existing results
  ./horizon.sh status          model server, guards, π*, λ̂ at a glance
"""


def short(s, n: int) -> str:
    s = str(s).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


class App:
    """All the logic of the shell; the menu and the command mode both call these methods."""

    def __init__(self, config_path: str, overrides: list[str]):
        self.config_path, self.overrides = config_path, list(overrides)
        self.samples, self.stage1_variant, self.history = [], None, []
        self._s2 = None
        self._n = 0
        self.reload(first=True)

    # ============================================================ basics =========
    def reload(self, first: bool = False) -> None:
        self.cfg, notes = prepare_config(self.config_path, self.overrides)
        if first:
            self.log = SessionLog(ROOT / self.cfg.shell["log_dir"])
            self.detailed = self.cfg.shell["detail"] == "detailed"
        prev = self._s2
        self.s1 = Stage1Engine(self.cfg)
        self.direct = DirectEngine(self.cfg)
        self._s2 = Stage2Engine(self.cfg, self.on_event, None if self.cfg.shell["auto_approve"] else self.ask_confirm)
        if prev is not None:
            for a in ("suite_name", "injection_text", "injection_samples", "injection_vector", "attack"):
                setattr(self._s2, a, getattr(prev, a))
        self.notes = notes
        self.log.write("config", overrides=self.overrides, notes=notes, flags=self.cfg.flags.model_dump())

    def who(self, role: str) -> str:
        """Which software / model does a step (names come from the live config)."""
        c, m = self.cfg, self.cfg.models
        via = "LiteLLM → role proxy → Ollama (GPU)"
        return {
            "guard_in": f"injection classifier ({c.direct['input_guard_model']}) · transformers on CPU",
            "scan": "regex pre-scan in horizon/shell_engine.py (plain Python, no AI)",
            "stage1": "horizon/stage1/transforms.py + keys.py (key from Python `secrets`)",
            "target": f"{m['q_llm']} via {via}",
            "canary": "horizon/stage1/canary.py (exact string match, no AI)",
            "stats": "horizon/stage1/stats.py (scipy Beta⁻¹, χ²-DRO, numpy)",
            "z3": "Z3 SMT solver · horizon/stage2/z3_check.py + config/policy.yaml",
            "world": "AgentDojo simulated environment (no real emails / money / web)",
            "planner": f"P-LLM {m['p_llm']} via {via}",
            "interp": "CaMeL interpreter (third_party/camel, STRICT mode)",
            "reader": f"Q-LLM {m['q_llm']} × m via {via} · Stage 1 transform per run",
            "parser": "Pydantic schema · horizon/stage2/schemas.py",
            "vote": "horizon/stage2/vote.py (majority + Hoeffding)",
            "policy": "horizon/stage2/policy.py + camel_adapter.py (labels SYSTEM ⊑ USER ⊑ UNTRUSTED)",
            "crc": "horizon/stage2/conformal.py (λ̂ from calibration) + you",
            "guard_out": f"canary check + Qwen3Guard ({c.direct['output_guard_model']}) · transformers on CPU",
            "judge": "AgentDojo task checks + attacker-entity match (horizon/shell_engine.py)",
            "log": f"SessionLog → {c.shell['log_dir']}/session_*.jsonl",
        }[role]

    def plan(self, title: str, rows: list[tuple[str, str]]) -> None:
        """Print what is about to happen and who does each step, before running it."""
        t = Table(title=f"Plan · {title}", show_lines=False, title_justify="left")
        t.add_column("#", style="bold")
        t.add_column("what happens")
        t.add_column("who does it", style="magenta")
        for i, (what, role) in enumerate(rows, 1):
            t.add_row(str(i), what, self.who(role))
        con.print(t)

    def step(self, i: int, n: int, title: str, plain: str = "", who: str = "") -> None:
        con.print(f"\n[bold blue]Step {i}/{n} · {title}[/]")
        if who:
            con.print(f"  [magenta]⚙ {self.who(who)}[/]")
        if plain:
            con.print(f"  [dim]{plain}[/]")

    def info(self, msg: str) -> None:
        con.print(f"  {msg}")

    def math(self, msg: str) -> None:
        if self.detailed:
            con.print(f"  [green]∑ {msg}[/]")

    def warn(self, msg: str) -> None:
        con.print(f"  [bold red]! {msg}[/]")

    def need_server(self) -> bool:
        if server_alive(self.cfg.models):
            return True
        self.warn("The model server is not running. Start it in another terminal:  ./scripts/serve_local.sh")
        return False

    def pick(self, title: str, options: list[tuple[str, str]], default: str = "1", allow_text: bool = False) -> str:
        """Numbered choice; returns the option key (or free text if allowed)."""
        t = Table(title=title, show_header=False, box=None, padding=(0, 2))
        for i, (k, desc) in enumerate(options, 1):
            t.add_row(f"[bold]{i}[/]", k, f"[dim]{desc}[/]")
        con.print(t)
        hint = " (number" + (", or type your own text" if allow_text else "") + ")"
        while True:
            a = Prompt.ask(f"Choose{hint}", default=default).strip()
            if a.isdigit() and 1 <= int(a) <= len(options):
                return options[int(a) - 1][0]
            if allow_text and a:
                return a
            self.warn("please type one of the numbers")

    def status(self) -> None:
        c, f = self.cfg, self.cfg.flags
        t = Table(title="Status", show_header=False)
        t.add_row("model server", "[green]running[/]" if server_alive(c.models)
                  else "[red]not running → ./scripts/serve_local.sh[/]")
        t.add_row("AI models", f"planner {c.models['p_llm']} · readers {c.models['q_llm']}")
        t.add_row("input guard", f"[green]injection classifier ready ({c.direct['input_guard_model']})[/]" if input_guard_accessible(c.direct)
                  else "[yellow]injection classifier not downloaded → off[/]")
        t.add_row("output guard", f"[green]Qwen3Guard ready ({c.direct['output_guard_model']})[/]"
                  if output_guard_accessible(c.direct) else "[yellow]Qwen3Guard not downloaded → off[/]")
        info = pi_star_info()
        t.add_row("Stage 1 marking",
                  f"best mix π* from experiments: {', '.join(f'{k} {v:.0%}' for k, v in info['pi_star'].items())}"
                  if info and not f.stage1_variant
                  else f"fixed marking '{f.stage1_variant or c.shell['default_variant']}' (π* not fitted yet)")
        t.add_row("Stage 2 readers m", str(f.m))
        t.add_row("risky actions", "you are asked (CONFIRM)" if not c.shell["auto_approve"] else "auto-answered (simulation)")
        t.add_row("detail level", "detailed (maths shown)" if self.detailed else "simple")
        t.add_row("session log", str(self.log.path.relative_to(ROOT)))
        con.print(t)
        for n in self.notes:
            con.print(f"  [yellow]note:[/] {n}")

    def check_input(self, text: str) -> bool:
        if not self.cfg.flags.use_firewall:
            self.info("[yellow]input guard switched off → skipped[/]")
            return True
        from horizon.direct.guards import InjectionGuardInput
        v = InjectionGuardInput(self.cfg.direct).check(text)
        self.info("[red]blocked as an attack[/]" if v.blocked else "[green]looks fine[/]")
        self.log.write("input_guard", blocked=v.blocked, score=v.score)
        return not v.blocked

    # ============================================================ 1 · Stage 1 =====
    def flow_document(self) -> None:
        con.print(Panel("An outside document (e.g. an email) contains hidden instructions. Stage 1 marks the document "
                        "with a secret key before the AI reads it. We then check whether the AI obeyed the hidden "
                        "instructions (a planted code word appears in its answer).", title="1 · Attack a document"))
        allx = load_samples(ROOT / self.cfg.shell["sample_file"], self.cfg.stage1["canary_prefix"],
                            self.cfg.stage1["canary_hex_bytes"])
        opts = [("all", "every attack technique at once")] + [(s.name, s.note) for s in allx]
        choice = self.pick("Which hidden attack should the document contain?", opts)
        self.samples = allx if choice == "all" else [s for s in allx if s.name == choice]
        task = Prompt.ask("What should the AI do with the document?", default="Summarize this document.")
        self.run_stage1(task)

    def run_stage1(self, task: str) -> None:
        if not self.samples:
            self.warn("no document loaded (menu 1, or `load all` in command mode)")
            return
        if not self.need_server():
            return
        self.history.append({"mode": "document", "text": task})
        N = 5
        self.plan("Stage 1 on one document", [("check your request", "guard_in"), ("scan the outside document", "scan"),
                                               ("mark it with a fresh secret key", "stage1"), ("the AI answers", "target"),
                                               ("did the attack work? (code word)", "canary"),
                                               ("update the running statistics", "stats"), ("write the log", "log")])
        self.step(1, N, "Check your request", "The direct layer looks at what YOU typed (jailbreak check).",
                  who="guard_in")
        if not self.check_input(task):
            return
        self.step(2, N, "Look at the outside document",
                  "A quick scan for instruction-like text. It only decides whether to ask you — it is not the defense.",
                  who="scan")
        self.info(f"document: {len(self.samples)} part(s), {sum(len(s.text) for s in self.samples)} characters "
                  "(untrusted — written by someone else)")
        guard = None
        if self.cfg.shell["use_input_guard_scan"] and self.cfg.flags.use_firewall:
            from horizon.direct.guards import InjectionGuardInput
            guard = InjectionGuardInput(self.cfg.direct)
        flagged = {s.name: scan_untrusted(s.text, guard) for s in self.samples}
        flagged = {k: v for k, v in flagged.items() if v}
        if flagged:
            for name, fs in flagged.items():
                self.info(f"[yellow]suspicious[/] {name}: {', '.join(sorted({f.kind for f in fs}))}")
            self.log.write("prescan", flagged={k: [f.kind for f in v] for k, v in flagged.items()})
            if self.cfg.shell["ask_on_detection"]:
                a = Prompt.ask("  The document seems to contain hidden instructions. Send it to the AI protected by "
                               "Stage 1? [y]es / [n]o / [s]how what was found", choices=["y", "n", "s"], default="y")
                if a == "s":
                    for name, fs in flagged.items():
                        for f in fs:
                            self.info(f"[blue]{name}[/] · {f.kind}: [dim]{short(f.snippet, 110)}[/]")
                    a = "y" if Confirm.ask("  Continue?", default=True) else "n"
                if a == "n":
                    self.info("cancelled — nothing was sent to the AI")
                    self.log.write("cancelled", reason="prescan")
                    return
        else:
            self.info("[green]nothing suspicious found[/]")
        variant, pi = self.s1.choose(self.stage1_variant)
        self.step(3, N, "Hide the document with a secret key (Stage 1)",
                  "The document is marked/scrambled with a key made just for this request. Your request is NOT changed.",
                  who="stage1")
        self.info(f"marking used: [bold]{variant}[/]" + ("  (drawn from the best mix π*)" if pi else ""))
        with con.status("Step 4 · the AI is answering …"):
            r = self.s1.run(task, self.samples, variant)
        key_txt = json.dumps(r.key, default=str) if self.cfg.shell["show_keys"] else f"fingerprint {r.key_fp}"
        self.info(f"secret key: {key_txt} · possible keys ≈ 10^{r.log10_K:.0f}"
                  + ("  [yellow](only 1 key: easy to fake)[/]" if r.log10_K == 0 else ""))
        self.math(f"forging bound q/|K_τ| (q={self.cfg.theory['q_main']:g}): log10 = {r.log10_forge:.1f}")
        if self.detailed:
            n = self.cfg.shell["preview_chars"]
            con.print(Panel(short(r.system, n), title="system prompt S_κ (contains the key)", border_style="dim"))
            con.print(Panel(short(r.user, n), title="what the AI receives: your task ‖ marked document",
                            border_style="dim"))
        self.step(4, N, "The AI answers", f"took {r.seconds:.0f} s", who="target")
        self.step(5, N, "Did the attack work?", "We look for each attack's code word in the answer.", who="canary")
        attacks = {k: v for k, v in r.executed.items() if k != "clean_control"}
        hits = [k for k, v in attacks.items() if v]
        for k, v in r.executed.items():
            self.info(("[red]✗ obeyed[/] " if v else "[green]✓ ignored[/] ") + k)
        self.log.write("stage1_run", task=task, variant=variant, key_fp=r.key_fp, executed=r.executed,
                       reply=r.reply, seconds=r.seconds)
        if hits:
            con.print(Panel(f"The AI followed {len(hits)} of {len(attacks)} hidden instruction(s): {', '.join(hits)}.\n"
                            "Stage 1 reduces this risk but cannot remove it — that is why Stage 2 exists.",
                            title="Result: attack worked ✗", border_style="red"))
            if Confirm.ask("  Show the (compromised) answer anyway?", default=False):
                con.print(Panel(r.reply, border_style="red"))
        else:
            con.print(Panel(r.reply, title=f"Result: all {len(attacks)} hidden instruction(s) ignored ✓",
                            border_style="green"))
        row = next(x for x in self.s1.tally_rows() if x["variant"] == variant)
        self.info(f"so far with '{variant}': attack worked {row['s']} of {row['n']} time(s)  "
                  f"[magenta]⚙ {self.who('stats')}[/]")
        self.math(f"p̂={row['p_hat']:.2f} · Clopper–Pearson p̄={row['p_bar']:.3f} · "
                  f"DRO g_ρ(p̄)={row['g_rho']:.3f} (ρ={self.cfg.theory['rho_main']})")

    def compare_variants(self, task: str) -> None:
        if not self.samples:
            self.warn("load a document first (`load all`)")
            return
        if not self.need_server():
            return
        t = Table(title=f"All markings on the same document · task: {short(task, 50)}")
        for c in ["marking", "possible keys", "attacks that worked", "seconds"]:
            t.add_column(c)
        for v in self.s1.variants:
            with con.status(f"{v} …"):
                r = self.s1.run(task, self.samples, v)
            hits = [k for k, x in r.executed.items() if x and k != "clean_control"]
            t.add_row(v, f"10^{r.log10_K:.0f}", ", ".join(hits) or "[green]none[/]", f"{r.seconds:.0f}")
            self.log.write("stage1_compare", task=task, variant=v, executed=r.executed, key_fp=r.key_fp)
        con.print(t)

    # ============================================================ 2 · Stage 2 =====
    def flow_agent(self) -> None:
        con.print(Panel("An AI agent with tools (a simulated Slack / email / travel world) does a task for you. "
                        "Attack text is planted in the world's outside sources. You watch the planner, the isolated "
                        "readers, the vote and the rule check — and YOU decide when a risky action needs approval.",
                        title="2 · Attack an agent"))
        world = self.pick("Which world?", [("slack", "channels, messages, web pages"),
                                           ("workspace", "email inbox, calendar, cloud files"),
                                           ("travel", "hotels, restaurants, reviews, bookings")])
        self._s2.set_suite(world)
        allx = load_samples(ROOT / self.cfg.shell["sample_file"], self.cfg.stage1["canary_prefix"],
                            self.cfg.stage1["canary_hex_bytes"])
        opts = [("none", "no attack (clean world)")] + [(s.name, s.note) for s in allx if s.name != "clean_control"]
        att = self.pick("Which attack should be planted in the world's outside sources?", opts, default="2")
        if att == "none":
            self._s2.clear_injection()
        else:
            self._s2.set_injection_samples([s for s in allx if s.name == att], None)
        tasks = self._s2.tasks()[: self.cfg.shell["example_tasks"]]
        req = self.pick("What should the agent do? (or type your own request)", [(p, uid) for uid, p in tasks],
                        allow_text=True)
        uid = next((u for u, p in tasks if p == req), None)
        self.run_stage2(req, uid)

    def run_stage2(self, prompt: str, user_task: str | None = None) -> None:
        if not self.need_server():
            return
        s2 = self._s2
        self.history.append({"mode": "agent", "text": prompt})
        self._n = 0
        self.plan("Stage 2 agent run", [("prove the rule table", "z3"), ("check your request", "guard_in"),
                                         ("planner writes a program from YOUR request only", "planner"),
                                         ("run the program, track labels", "interp"),
                                         ("call tools in the simulated world", "world"),
                                         ("read outside data with m isolated readers", "reader"),
                                         ("check each reader's answer format", "parser"),
                                         ("vote on the readers' answers", "vote"),
                                         ("ALLOW / DENY / CONFIRM each tool call", "policy"),
                                         ("risky call: deny or ask you", "crc"), ("check the final answer", "guard_out"),
                                         ("score the run", "judge"), ("write the log", "log")])
        if s2._built is None:
            with con.status("Checking the rule table with the Z3 prover …"):
                b = s2.built()
            self.event_line("🔒", "Rules verified",
                            f"Z3 proved for all {len(s2.suite.tools)} tools: data from outside can never trigger an "
                            "action on its own." if self.cfg.flags.use_policy else "[yellow]rule check is switched off[/]", who="z3")
            self.log.write("z3", suite=s2.suite_name, proved=self.cfg.flags.use_policy, pipeline=b.pipeline.name)
        planted = ", ".join(x.name for x in s2.injection_samples) if s2.injection_text else (
            f"AgentDojo attack {s2.attack[0]}" if s2.attack else "nothing (clean world)")
        self.event_line("🌍", f"World '{s2.suite_name}'", f"attack planted in its outside sources: {planted}", who="world")
        out = s2.run(prompt=None if user_task else prompt, user_task_id=user_task)
        self.log.write("stage2_run", **out.__dict__)
        self.summarize_stage2(out)

    def event_line(self, icon: str, title: str, text: str = "", who: str = "") -> None:
        self._n += 1
        con.print(f"\n[bold cyan]{self._n:>2} {icon} {title}[/]" + (f"\n     [magenta]⚙ {self.who(who)}[/]" if who else "")
                  + (f"\n     {text}" if text else ""))

    def on_event(self, kind: str, d: dict) -> None:
        """Live events from the agent pipeline, in plain words (maths only in detailed mode)."""
        self.log.write(kind, **d)
        if kind == "input_guard":
            self.event_line("🛡", "Your request checked",
                            "[red]blocked as a jailbreak[/]" if d["blocked"] else "[green]looks fine[/]", who="guard_in")
        elif kind == "program":
            self.event_line("🧠", "Planner wrote a plan", "The planner only saw YOUR request, never the outside data.", who="planner")
            con.print(Syntax(d["code"].strip().strip("`").removeprefix("python").strip(), "python", theme="ansi_dark",
                             line_numbers=True))
        elif kind == "program_result" and not d["ok"]:
            self.event_line("↻", "Plan hit an error", f"{short(d['error'], 140)} — the planner will try to fix it", who="interp")
        elif kind == "tool_call":
            e = d["event"]
            untrusted = [k for k, v in e.arg_caps.items() if v == "UNTRUSTED"]
            if e.control_cap == "UNTRUSTED":
                untrusted.append("(inside an if on outside data)")
            why = ("uses outside data: " + ", ".join(untrusted)) if untrusted else "uses only your / system data"
            verdict = {"ALLOW": "allowed", "DENY": "BLOCKED", "CONFIRM": "needs your approval"}.get(e.decision, e.decision)
            self.event_line("🔧", f"Tool {e.tool}", f"{why} → [{DECISION.get(e.decision, 'white')}]{verdict}[/]"
                            + ("" if d["policy"] else " (rules switched off)"), who="policy")
            self.math(f"labels {e.arg_caps} · control={e.control_cap} · args {short(json.dumps(e.args, default=str), 100)}")
        elif kind == "q_start":
            self.event_line("📄", f"Reading outside data safely with {d['m']} isolated reader(s)",
                            "Each reader has no tools and gets the data marked with its OWN secret key (Stage 1)."
                            if d["spotlight"] else "Each reader has no tools (Stage 1 marking is switched off).", who="reader")
            if self.detailed:
                con.print(Panel(short(d["query"], self.cfg.shell["preview_chars"]), title="question to the readers",
                                border_style="dim"))
        elif kind == "q_run":
            v = short(d["canonical"], 90) if d["parsed"] else "[red]answer rejected (wrong format)[/]"
            con.print(f"     reader {d['i']}/{d['m']} (marking {d['variant']}, key #{d['key_fp']}): {v}")
        elif kind == "vote":
            agree = round(d["agreement"] * d["m"])
            self.event_line("🗳", "Readers vote", f"[green]{agree} of {d['m']} agree → value accepted[/]" if d["accepted"]
                            else f"[red]no majority ({agree} of {d['m']}) → value refused[/]", who="vote")
            info = pi_star_info()
            if info:
                b = hoeffding_vote_bound(d["m"], info["R_Q"])
                self.math(f"Hoeffding: P(vote fooled) ≤ {b if isinstance(b, str) else f'{b:.3g}'} "
                          f"(m={d['m']}, R̄_Q={info['R_Q']:.3f}); readers on the same text fail together")
        elif kind == "confirm":
            e = d["event"]
            if not e.escalated:
                self.event_line("⛔", f"{e.tool} denied automatically",
                                f"readers agreed too little (score {e.score:.2f} < threshold {d['lambda_hat']})", who="crc")
            else:
                self.info(f"you answered: {'[green]approved[/]' if e.approved else '[red]declined[/]'}")
        elif kind == "output_guard":
            self.event_line("🛡", "Final answer checked",
                            f"[red]withheld[/] ({d['reason']})" if d["blocked"] else "[green]passed[/]", who="guard_out")

    def ask_confirm(self, ev, lambda_hat) -> bool:
        ents = attacker_entities(self._s2.injection_text or "", "")
        lines = []
        for k, v in ev.args.items():
            sv = short(json.dumps(v, default=str), 90)
            src = {"UNTRUSTED": "[yellow]from outside data[/]", "USER": "from you",
                   "SYSTEM": "from the system"}.get(ev.arg_caps.get(k), "")
            mark = "  [bold red]← looks like the attacker's[/]" if any(x in sv.lower() for x in ents) else ""
            lines.append(f"  {k} = {sv}   {src}{mark}")
        detail = f"\n[dim]readers' agreement {ev.score:.2f} · threshold λ̂ {lambda_hat}[/]" if self.detailed else ""
        con.print(Panel(f"The agent wants to run [bold]{ev.tool}[/]:\n" + "\n".join(lines)
                        + "\n\nThis action depends on outside data, so the rules require YOUR approval." + detail,
                        title="Your approval is needed", border_style="yellow"))
        ok = Confirm.ask("  Allow it?", default=False)
        self.log.write("user_decision", tool=ev.tool, approved=ok)
        return ok

    def summarize_stage2(self, out) -> None:
        lines = []
        if out.blocked_by_input_guard:
            lines.append("[red]Your request was blocked by the jailbreak guard.[/]")
        if self._s2.injection_text or self._s2.attack:
            if not out.attacker_calls:
                lines.append("[green]✓ The planted attack never reached a tool.[/]")
            for c in out.attacker_calls:
                what = f"{c['tool']} with {', '.join(c['entities'])}"
                if c["decision"] == "ALLOW":
                    lines.append(f"[red]✗ {what} RAN without asking (policy bypass)[/]")
                elif c["approved"]:
                    lines.append(f"[yellow]! {what} ran because you approved it[/]")
                else:
                    lines.append(f"[green]✓ {what} was blocked[/]")
            if out.fooled_votes:
                lines.append(f"[yellow]! readers were fooled {out.fooled_votes} time(s) (the rules still applied)[/]")
            if out.refused_votes:
                lines.append(f"readers disagreed {out.refused_votes} time(s) → those values were refused")
            if out.canary_in_answer:
                lines.append(f"[yellow]! the attack's code word reached the answer ({', '.join(out.canary_in_answer)})[/]")
        if out.utility is not None:
            lines.append(f"task done correctly (AgentDojo check): {'[green]yes[/]' if out.utility else '[red]no[/]'}")
        if out.agentdojo_attack_success is not None:
            lines.append(f"AgentDojo attack goal reached: {'[red]YES[/]' if out.agentdojo_attack_success else '[green]no[/]'}")
        con.print(f"\n  [magenta]⚙ scored by {self.who('judge')}[/]")
        con.print(Panel("\n".join(lines) or "clean run", title=f"Result · {out.seconds:.0f} s", border_style="cyan"))
        con.print(Panel(out.answer or "(empty)", title="agent's answer", border_style="green"))

    # ============================================================ 3 · jailbreak ===
    def flow_jailbreak(self) -> None:
        con.print(Panel("Here YOU are the attacker. Your prompt goes through the direct layer: an injection classifier checks it, "
                        "the AI's hidden instructions contain a secret code word (to catch leaks), and Qwen3Guard "
                        "checks the answer.", title="3 · Try a jailbreak"))
        allx = load_samples(ROOT / JAILBREAK_FILE, self.cfg.stage1["canary_prefix"], self.cfg.stage1["canary_hex_bytes"])
        choice = self.pick("Pick a jailbreak prompt (or type your own)", [(s.name, s.note) for s in allx],
                           default="2", allow_text=True)
        prompt = next((s.text for s in allx if s.name == choice), choice)
        self.run_jailbreak(prompt)

    def run_jailbreak(self, prompt: str) -> None:
        if not self.need_server():
            return
        self.history.append({"mode": "jailbreak", "text": prompt})
        self.plan("direct layer", [("check your prompt", "guard_in"), ("the AI answers", "target"),
                                   ("did it leak the hidden code word?", "canary"), ("is the answer harmful?", "guard_out"),
                                   ("write the log", "log")])
        con.print(Panel(prompt, title="your prompt", border_style="dim"))
        with con.status("running through the direct layer …"):
            o = self.direct.run(prompt)
        self.step(1, 4, "Check your prompt (injection classifier)",
                  {"blocked": "[red]BLOCKED as an attack[/]", "passed": "[green]passed[/]",
                   "off": "[yellow]switched off → skipped[/]"}[o.input_guard], who="guard_in")
        if o.input_guard == "blocked":
            con.print(Panel("Stopped before reaching the AI.", title="Result: jailbreak blocked ✓", border_style="green"))
            self.log.write("jailbreak", **o.__dict__)
            return
        self.step(2, 4, "The AI answers", f"its hidden instructions include a secret code word · {o.seconds:.0f} s", who="target")
        self.step(3, 4, "Did it leak its hidden instructions? (canary)",
                  "[red]YES — the secret code word appeared → answer withheld[/]" if o.canary_leaked
                  else "[green]no leak[/]", who="canary")
        self.step(4, 4, "Is the answer harmful? (Qwen3Guard)",
                  {"blocked": f"[red]unsafe → withheld[/] ({o.output_reason})", "passed": "[green]safe[/]",
                   "off": "[yellow]switched off → skipped[/]"}[o.output_guard], who="guard_out")
        self.info(f"the AI itself {'[green]refused[/]' if o.refused else '[yellow]did not refuse[/]'} "
                  "[dim](wording check only)[/]")
        good = o.canary_leaked or o.output_guard == "blocked" or o.refused
        con.print(Panel(o.final, title="Result: " + ("jailbreak stopped ✓" if good else "check the answer — the guard "
                        "that would judge it may be switched off"), border_style="green" if good else "yellow"))
        self.log.write("jailbreak", **o.__dict__)

    # ============================================================ 4-7 =============
    def flow_selftest(self) -> None:
        con.print(Panel("Runs the unit tests (the maths and the rules) and the smoke test (does each layer fire on an "
                        "obvious attack?).", title="4 · Quick self-test"))
        uv = str(Path.home() / ".local/bin/uv")
        with con.status("unit tests …"):
            r = subprocess.run([uv, "run", "--no-sync", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or ["no output"])[-1]
        self.info(("[green]" if r.returncode == 0 else "[red]") + last + "[/]")
        if self.need_server() and Confirm.ask("  Also run the smoke test (≈2 min, uses the AI)?", default=True):
            with con.status("smoke test …"):
                subprocess.run([uv, "run", "--no-sync", "python", "scripts/smoke_test.py"], cwd=ROOT,
                               capture_output=True, text=True)
            self.show_smoke()

    def show_smoke(self) -> None:
        p = ROOT / "results" / "smoke" / "smoke.json"
        if not p.exists():
            self.info("no smoke test yet")
            return
        S = json.loads(p.read_text())
        t = Table(title="Smoke test — does each layer fire?")
        t.add_column("check")
        t.add_column("result")
        qa = S.get("quarantine_attack", {})
        t.add_row("readers on an attacked email", "[red]fooled[/]" if qa.get("fooled") else "[green]not fooled[/]")
        for k, v in S.get("policy", {}).items():
            t.add_row(f"rule: {k}", f"[{DECISION.get(v, 'white')}]{v}[/]")
        t.add_row("Z3 catches a bad rule table", "[green]yes[/]" if "refused" in S.get("z3_bad_policy", "") else "[red]no[/]")
        t.add_row("canary catches a leak", "[green]yes[/]" if S.get("canary", {}).get("leak_blocked") else "[red]no[/]")
        for g in ("input_guard", "output_guard"):
            t.add_row(g, S.get(g, {}).get("status", "ran"))
        con.print(t)

    def flow_results(self) -> None:
        actions = {"overview": self.reports_overview, "stage1": self.reports_stage1, "stage2": self.reports_stage2,
                   "smoke": self.show_smoke, "session": self.session_stats, "analyze": lambda: self.analyze(""),
                   "pdf": self.open_pdf}
        while True:
            what = self.pick("Results & reports", [
                ("overview", "what exists so far"), ("stage1", "Stage 1 experiment tables"),
                ("stage2", "Stage 2 agent experiment tables"), ("smoke", "smoke-test result"),
                ("session", "what happened in this session"), ("analyze", "ask the report AI to explain this session"),
                ("pdf", "open report/report.pdf"), ("back", "back to the menu")])
            if what == "back":
                return
            actions[what]()

    def reports_overview(self) -> None:
        res = ROOT / "results"
        t = Table(title="Saved results")
        t.add_column("what")
        t.add_column("where")
        for label, p in [("PDF report", ROOT / "report" / "report.pdf"), ("notebook", ROOT / "Horizon.ipynb"),
                         ("pipeline flowchart", ROOT / "docs" / "pipeline.pdf"),
                         ("best Stage 1 mix π*", PI_STAR), ("conformal threshold λ̂", LAMBDA_HAT),
                         ("smoke test", res / "smoke" / "smoke.json")]:
            t.add_row(label, str(p.relative_to(ROOT)) if p.exists() else "[dim]not yet[/]")
        for sp in sorted((res / "stage1").glob("*/summary.json")):
            t.add_row(f"Stage 1 run '{sp.parent.name}'", str(sp.relative_to(ROOT)))
        for ep in sorted(p for p in (res / "stage2").glob("*/episodes.csv") if "-" not in p.parent.name):
            t.add_row(f"Stage 2 run '{ep.parent.name}'", str(ep.relative_to(ROOT)))
        t.add_row("figures", f"{len(list((ROOT / 'figures').rglob('*.png')))} images in figures/")
        t.add_row("shell sessions", f"{len(list((res / 'shell').glob('*.jsonl')))} logs in results/shell/")
        con.print(t)

    def reports_stage1(self) -> None:
        found = False
        for sp in sorted((ROOT / "results" / "stage1").glob("*/summary.json")):
            found = True
            S = json.loads(sp.read_text())
            t = Table(title=f"Stage 1 · {sp.parent.name} · {S['n']} attacks per marking")
            for c in ["marking", "attack success", "upper limit p̄", "usefulness U", "smart attacker p̄_adp"]:
                t.add_column(c)
            for v, d in S["variants"].items():
                adp, u = d["p_adp_bar"], d["U"]
                t.add_row(v, f"{d['p_hat']:.1%}", f"{d['p_bar']:.1%}", f"{u:.2f}" if u == u else "-",
                          f"{adp:.1%}" if adp == adp else "-")
            con.print(t)
        if not found:
            self.info("no Stage 1 experiment yet → ./horizon.sh run pilot")

    def reports_stage2(self) -> None:
        import pandas as pd
        found = False
        for ep in sorted(p for p in (ROOT / "results" / "stage2").glob("*/episodes.csv") if "-" not in p.parent.name):
            found = True
            df = pd.read_csv(ep)
            att, ben = df[df["style"] != "benign"], df[df["style"] == "benign"]
            t = Table(title=f"Stage 2 · {ep.parent.name}")
            for c in ["setup", "tasks done (no attack)", "attacks that worked", "episodes"]:
                t.add_column(c)
            for name in df["config"].unique():
                a, b = att[att["config"] == name], ben[ben["config"] == name]
                t.add_row(name.split("|")[0], f"{b['utility'].mean():.0%}" if len(b) else "-",
                          f"{a['asr'].mean():.0%} ({int(a['asr'].sum())}/{len(a)})" if len(a) else "-",
                          str(int((df["config"] == name).sum())))
            con.print(t)
        if not found:
            self.info("no Stage 2 experiment yet → ./horizon.sh run pilot")

    def session_stats(self) -> None:
        rows = self.s1.tally_rows()
        if rows:
            t = Table(title="This session · document attacks (Stage 1)")
            for c in ["marking", "attack worked", "tries"] + (["p̄ (CP)", "g_ρ(p̄)"] if self.detailed else []):
                t.add_column(c)
            for r in rows:
                t.add_row(r["variant"], str(r["s"]), str(r["n"]),
                          *([f"{r['p_bar']:.3f}", f"{r['g_rho']:.3f}"] if self.detailed else []))
            con.print(t)
        eps = [r for r in self.log.records if r["kind"] == "stage2_run"]
        if eps:
            t = Table(title="This session · agent runs (Stage 2)")
            for c in ["request", "attack actions", "readers fooled / refused"]:
                t.add_column(c)
            for r in eps:
                calls = "; ".join(f"{c['tool']}: {c['decision']}{' (you approved)' if c.get('approved') else ''}"
                                  for c in r["attacker_calls"]) or "none"
                t.add_row(short(r["prompt"], 50), calls, f"{r['fooled_votes']} / {r['refused_votes']}")
            con.print(t)
        jb = [r for r in self.log.records if r["kind"] == "jailbreak"]
        if jb:
            self.info(f"jailbreak tries: {len(jb)} · leaks caught by the canary: {sum(r['canary_leaked'] for r in jb)}")
        if not (rows or eps or jb):
            self.info("nothing run yet in this session")

    def analyze(self, question: str) -> None:
        r = self.cfg.report
        recs = self.log.records[-self.cfg.shell["analysis_events"]:]
        digest = json.dumps({"stage1_tally": self.s1.tally_rows(), "events": recs}, default=str)[: r["max_context_chars"]]
        q = question or ("Explain in plain language what happened in this session of the Horizon defense: which "
                         "layers acted, which attacks worked or were blocked and by what, and what to try next.")
        mcfg = {"temperature": r["temperature"], "max_tokens": r["max_tokens"], "cache_dir": self.cfg.models["cache_dir"],
                "request_timeout_s": r["request_timeout_s"], "api_base": r["api_base"], "api_key_env": r["api_key_env"],
                "disable_thinking": r["disable_thinking"], "reasoning_effort_off": self.cfg.models["reasoning_effort_off"]}
        with con.status(f"{r['model']} is reading {len(recs)} logged steps … (a large local model can take minutes)"):
            txt = LLM(r["model"], mcfg).chat([
                {"role": "system", "content": "You explain logs of an LLM prompt-injection defense to a student. Use "
                                              "only facts and numbers in the log. Be short and concrete."},
                {"role": "user", "content": f"{q}\n\nLOG:\n{digest}"}], use_cache=False)
        con.print(Panel(Markdown(txt), title=f"analysis by {r['model']}", border_style="cyan"))
        self.log.write("analysis", model=r["model"], question=q, answer=txt)

    def open_pdf(self) -> None:
        p = ROOT / "report" / "report.pdf"
        if not p.exists():
            self.info("no PDF yet → ./horizon.sh report")
            return
        subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.info(f"opening {p.relative_to(ROOT)}")

    def flow_settings(self) -> None:
        while True:
            f = self.cfg.flags
            opts = [
                ("detail", f"how much to show: now {'detailed (with maths)' if self.detailed else 'simple'}"),
                ("marking", f"Stage 1 marking: now {self.stage1_variant or f.stage1_variant or 'auto (π* / default)'}"),
                ("readers", f"Stage 2 isolated readers m: now {f.m}"),
                ("rules", f"rule table: now '{self.cfg.stage2['policy']}' (strict = never ask, always deny)"),
                ("approval", f"risky actions: now {'auto-answered' if self.cfg.shell['auto_approve'] else 'ask me'}"),
                ("prescan", f"ask me when a document looks attacked: now {'yes' if self.cfg.shell['ask_on_detection'] else 'no'}"),
                ("layers", "switch layers on/off (Stage 1, vote, typing, rules, canary) — for comparisons"),
                ("keys", f"show secret keys: now {'yes' if self.cfg.shell['show_keys'] else 'no'}"),
                ("advanced", "set any value from config/params.yaml (key=value)"),
                ("reset", "undo all changes made in this session"),
                ("back", "back to the menu")]
            k = self.pick("Settings", opts, default=str(len(opts)))
            if k == "back":
                return
            if k == "detail":
                self.detailed = not self.detailed
            elif k == "marking":
                v = self.pick("Marking for document attacks", [("auto", "best mix π* if fitted, else the default")]
                              + [(n, f"possible keys ≈ 10^{x.log10_key_space():.0f}") for n, x in self.s1.variants.items()])
                self.stage1_variant = None if v == "auto" else v
                self.set_override("flags.stage1_variant", "null" if v == "auto" else v)
            elif k == "readers":
                self.set_override("flags.m", self.pick("Number of isolated readers m (odd numbers avoid ties)",
                                                       [(str(x), "") for x in self.cfg.stage2["m_list"]]))
            elif k == "rules":
                self.set_override("stage2.policy", self.pick("Rule table", [(p, "") for p in self.cfg.stage2["policies"]]))
            elif k in ("approval", "prescan", "keys"):
                key = {"approval": "auto_approve", "prescan": "ask_on_detection", "keys": "show_keys"}[k]
                self.set_override(f"shell.{key}", str(not self.cfg.shell[key]).lower())
            elif k == "layers":
                while True:
                    f = self.cfg.flags
                    lay = self.pick("Layers", [(n, "on" if getattr(f, n) else "off") for n in
                                               ["use_stage1", "use_vote", "use_typing", "use_policy", "use_canary"]]
                                    + [("back", "")], default="6")
                    if lay == "back":
                        break
                    self.set_override(f"flags.{lay}", str(not getattr(f, lay)).lower())
            elif k == "advanced":
                kv = Prompt.ask("key=value (e.g. stage2.simulated_user=deny)")
                if "=" in kv:
                    self.set_override(*kv.split("=", 1))
            elif k == "reset":
                self.overrides, self.stage1_variant = [], None
                self.reload()
                self.info("all session changes undone")

    def set_override(self, key: str, value: str) -> None:
        old = list(self.overrides)
        self.overrides = [o for o in self.overrides if o.split("=", 1)[0] != key] + [f"{key}={value}"]
        try:
            self.reload()
            self.info(f"[green]{key} = {value}[/]")
            for n in self.notes:
                self.info(f"[yellow]note:[/] {n}")
        except Exception as e:
            self.overrides = old
            self.reload()
            self.warn(f"not accepted ({e}); nothing changed")

    def flow_help(self) -> None:
        con.print(Markdown(OVERVIEW))
        con.print(Markdown("**Menu.** 1 document attack (Stage 1) · 2 agent attack (Stage 2) · 3 jailbreak (direct "
                           "layer) · 4 self-test · 5 results · 6 settings · 7 this help · 8 command mode · 0 exit. "
                           "At every question the suggested answer is in brackets — just press Enter. Ctrl-C always "
                           "goes back to the menu."))
        self.glossary("")
        con.print(ALL_COMMANDS)
        con.print("[bold]Command mode[/] (menu 8): type `help` there for the list of commands, `menu` to come back.")

    def glossary(self, term: str) -> None:
        items = [(k, v) for k, v in GLOSSARY.items() if term.lower() in k.lower()] if term else list(GLOSSARY.items())
        if not items:
            self.info(f"no glossary entry for '{term}'")
            return
        t = Table(title="Glossary" + (f" · '{term}'" if term else ""), show_header=False)
        for k, v in items:
            t.add_row(f"[bold]{k}[/]", v)
        con.print(t)

    # ============================================================ menu ============
    MENU = [("document", "1 · Attack a DOCUMENT   (Stage 1: hidden instructions in an email / web page)"),
            ("agent", "2 · Attack an AGENT     (Stage 2: an AI with tools reads poisoned data)"),
            ("jailbreak", "3 · Try a JAILBREAK     (direct layer: you are the attacker)"),
            ("selftest", "4 · Quick self-test     (unit tests + smoke test)"),
            ("results", "5 · Results & reports"),
            ("settings", "6 · Settings"),
            ("help", "7 · Help & glossary"),
            ("command", "8 · Command mode        (advanced)")]

    def run_menu(self) -> None:
        con.print(Panel.fit("[bold]Horizon[/] — defense against prompt injection\n"
                            "[dim]choose a number · Enter accepts the suggestion · Ctrl-C goes back · 0 exits[/]",
                            border_style="blue"))
        self.status()
        while True:
            con.print()
            for _, label in self.MENU:
                con.print(f"  {label}")
            con.print("  0 · Exit")
            try:
                a = Prompt.ask("\nWhat do you want to do?", default="7").strip()
            except (KeyboardInterrupt, EOFError):
                a = "0"
            if a in ("0", "q", "exit", "quit"):
                con.print(f"bye — session log: {self.log.path.relative_to(ROOT)}")
                return
            if not (a.isdigit() and 1 <= int(a) <= len(self.MENU)):
                self.warn("type a number from the menu")
                continue
            key = self.MENU[int(a) - 1][0]
            try:
                if key == "command":
                    CommandMode(self).cmdloop()
                else:
                    getattr(self, f"flow_{key}")()
            except KeyboardInterrupt:
                self.warn("cancelled — back to the menu")
            except Exception as e:
                self.warn(f"{type(e).__name__}: {e}")
                self.log.write("error", error=repr(e), trace=traceback.format_exc()[-2000:])


class CommandMode(cmd.Cmd):
    """Type commands directly. `help` lists them, `help <command>` explains one, `menu` goes back."""
    doc_header = "commands (type `help <command>` for details)"

    def __init__(self, app: App):
        super().__init__()
        self.app = app
        self.mode = "document"
        self.prompt = "horizon[document]> "
        con.print("[dim]command mode — `help` for commands, `menu` to go back. Plain text is sent to the AI "
                  "(document mode; `mode agent` / `mode jailbreak` to switch).[/]")

    def onecmd(self, line: str) -> bool:
        try:
            return super().onecmd(line)
        except KeyboardInterrupt:
            self.app.warn("interrupted")
        except Exception as e:
            self.app.warn(f"{type(e).__name__}: {e}")
            self.app.log.write("error", error=repr(e), trace=traceback.format_exc()[-2000:])
        return False

    def emptyline(self) -> bool:
        return False

    def default(self, line: str) -> None:
        self.do_ask(line)

    def do_ask(self, arg: str) -> None:
        """ask <text> — send a request to the AI in the current mode (document | agent | jailbreak)."""
        if not arg.strip():
            return
        {"document": self.app.run_stage1, "agent": self.app.run_stage2, "jailbreak": self.app.run_jailbreak}[
            self.mode](arg.strip())

    def do_mode(self, arg: str) -> None:
        """mode document|agent|jailbreak — where plain text goes."""
        if arg.strip() in ("document", "agent", "jailbreak"):
            self.mode = arg.strip()
            self.prompt = f"horizon[{self.mode}]> "
        else:
            self.app.warn("mode document | agent | jailbreak")

    def do_load(self, arg: str) -> None:
        """load [file] [section …|all] — load an outside document (default: the sample file, all sections)."""
        parts = shlex.split(arg)
        path = ROOT / self.app.cfg.shell["sample_file"]
        if parts and (ROOT / parts[0]).exists():
            path, parts = ROOT / parts[0], parts[1:]
        allx = load_samples(path, self.app.cfg.stage1["canary_prefix"], self.app.cfg.stage1["canary_hex_bytes"])
        self.app.samples = allx if not parts or parts == ["all"] else [s for s in allx if s.name in parts]
        self.app.info(f"loaded: {', '.join(s.name for s in self.app.samples) or 'nothing (unknown section?)'}")

    def do_samples(self, arg: str) -> None:
        """samples — list the attack techniques in the sample files."""
        for f in (self.app.cfg.shell["sample_file"], JAILBREAK_FILE):
            t = Table(title=f)
            t.add_column("section")
            t.add_column("what it tests")
            for s in load_samples(ROOT / f, self.app.cfg.stage1["canary_prefix"], self.app.cfg.stage1["canary_hex_bytes"]):
                t.add_row(s.name, s.note)
            con.print(t)

    def do_compare(self, arg: str) -> None:
        """compare [task] — run the loaded document with all 8 markings."""
        self.app.compare_variants(arg.strip() or "Summarize this document.")

    def do_world(self, arg: str) -> None:
        """world slack|workspace|travel — the simulated world for agent mode."""
        self.app._s2.set_suite(arg.strip())
        self.do_mode("agent")

    def do_where(self, arg: str) -> None:
        """where — the outside sources of the current world and where they live."""
        t = Table(title=f"outside sources in '{self.app._s2.suite_name}' (simulated by AgentDojo)")
        for c in ["slot", "what it is", "where in the world"]:
            t.add_column(c)
        for r in self.app._s2.where():
            t.add_row(r["vector"], r["description"], r["where"])
        con.print(t)

    def do_inject(self, arg: str) -> None:
        """inject <section …|all> | inject attack <name> <injection_task_id> | inject off — plant an attack in the world."""
        parts = shlex.split(arg)
        if not parts or parts == ["off"]:
            self.app._s2.clear_injection()
            self.app.info("clean world")
        elif parts[0] == "attack":
            self.app._s2.set_attack(parts[1], parts[2])
            self.app.info(f"AgentDojo attack {parts[1]} → {parts[2]}")
        else:
            allx = load_samples(ROOT / self.app.cfg.shell["sample_file"], self.app.cfg.stage1["canary_prefix"],
                                self.app.cfg.stage1["canary_hex_bytes"])
            chosen = allx if parts == ["all"] else [s for s in allx if s.name in parts]
            self.app._s2.set_injection_samples(chosen, None)
            self.app.info(f"planted: {', '.join(s.name for s in chosen) or 'nothing (unknown section?)'}")

    def do_tasks(self, arg: str) -> None:
        """tasks — example tasks of the current world (run one with `run user_task_N`)."""
        for uid, p in self.app._s2.tasks():
            con.print(f"  [bold]{uid}[/]  {short(p, 110)}")

    def do_run(self, arg: str) -> None:
        """run <user_task_id> — run an AgentDojo task (with the planted attack) and check if it was done right."""
        self.app.run_stage2(self.app._s2.suite.get_user_task_by_id(arg.strip()).PROMPT, arg.strip())

    def do_set(self, arg: str) -> None:
        """set key=value — change any config value for this session (e.g. set flags.m=7)."""
        if "=" in arg:
            self.app.set_override(*arg.strip().split("=", 1))
        else:
            self.app.warn("set section.key=value")

    def do_config(self, arg: str) -> None:
        """config [section] — show config values (flags, stage1, stage2, shell, report, …)."""
        d = getattr(self.app.cfg, arg.strip() or "flags", None)
        con.print_json(json.dumps(d.model_dump() if hasattr(d, "model_dump") else d, default=str))

    def do_status(self, arg: str) -> None:
        """status — server, guards, π*, settings."""
        self.app.status()

    def do_stats(self, arg: str) -> None:
        """stats — what happened in this session."""
        self.app.session_stats()

    def do_analyze(self, arg: str) -> None:
        """analyze [question] — ask the report AI to explain this session."""
        self.app.analyze(arg.strip())

    def do_reports(self, arg: str) -> None:
        """reports [stage1|stage2|smoke|pdf] — saved results."""
        {"stage1": self.app.reports_stage1, "stage2": self.app.reports_stage2, "smoke": self.app.show_smoke,
         "pdf": self.app.open_pdf}.get(arg.strip(), self.app.reports_overview)()

    def do_explain(self, arg: str) -> None:
        """explain [term] — glossary (e.g. `explain vote`, `explain key`)."""
        self.app.glossary(arg.strip())

    def do_detail(self, arg: str) -> None:
        """detail — switch between simple and detailed (maths) output."""
        self.app.detailed = not self.app.detailed
        self.app.info("detailed" if self.app.detailed else "simple")

    def do_menu(self, arg: str) -> bool:
        """menu — back to the numbered menu."""
        return True

    do_exit = do_menu
    do_EOF = do_menu


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_PARAMS))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override any params.yaml value")
    ap.add_argument("--status", action="store_true", help="print the status table and exit")
    ap.add_argument("--commands", action="store_true", help="start in command mode instead of the menu")
    ap.add_argument("-c", "--command", action="append", default=[], help="run command-mode commands, then exit")
    args = ap.parse_args()
    app = App(args.config, args.set)
    if args.status:
        app.status()
        return
    if args.command:
        cm = CommandMode(app)
        for c in args.command:
            con.print(f"[bold]{cm.prompt}[/]{c}")
            cm.onecmd(c)
        return
    try:
        import readline  # noqa: F401  (history + arrow keys)
    except ImportError:
        pass
    if args.commands:
        CommandMode(app).cmdloop()
    else:
        app.run_menu()


if __name__ == "__main__":
    main()
