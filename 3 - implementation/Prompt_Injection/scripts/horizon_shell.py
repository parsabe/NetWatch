"""Interactive Horizon shell: give commands to the protected AI and watch every stage live.

    ./horizon.sh                      (or: uv run python scripts/horizon_shell.py [--set key=value ...])

Two modes (switch with `mode`):
  stage1  one protected call on an untrusted document: your text is the task T, the loaded outside
          source is D ⊕ δ, keyed spotlighting x = S_κ ‖ T ‖ τ_κ(D ⊕ δ); canaries show if an injection ran
  stage2  the full agent (CaMeL + Horizon) in an AgentDojo world: P-LLM plans from your prompt only,
          outside data goes through the quarantine × m (+ Stage 1), vote, labels, Z3-verified policy,
          and YOU answer every CONFIRM question
Every event is printed with its stage tag and written to results/shell/session_*.jsonl.
"""
from __future__ import annotations

import argparse
import cmd
import json
import shlex
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
from horizon.shell_engine import (LAMBDA_HAT, PI_STAR, SessionLog, Stage1Engine, Stage2Engine,  # noqa: E402
                                  attacker_entities, guards_accessible, load_samples, pi_star_info,
                                  prepare_config, scan_untrusted)
from horizon.stage2.vote import hoeffding_vote_bound  # noqa: E402

con = Console(highlight=False)
TAG = {"DIRECT": "bold magenta", "STAGE 1": "bold blue", "STAGE 2": "bold cyan", "POLICY": "bold yellow",
       "Z3": "bold green", "VOTE": "bold cyan", "MATH": "bold green", "CRC": "bold yellow", "MODEL": "bold white",
       "SOURCE": "bold blue", "RESULT": "bold white", "CONFIG": "bold white", "WARN": "bold red"}
DECISION = {"ALLOW": "green", "DENY": "red", "CONFIRM": "yellow"}

FLOW = """\
 you ─▶ [DIRECT] Prompt Guard 2 ─▶ [STAGE 2] privileged LLM (sees ONLY your prompt) ─▶ interpreter ─▶ tools
                                                                                          │
         outside sources (emails · web · chat · files) ──────────────────────────────────┘
              │
              └─▶ [STAGE 1] τ ~ π*, fresh key κ ─▶ quarantined LLM × m ─▶ [VOTE] ─▶ typed value + label
                                                                                  │
 answer ◀─ [DIRECT] canary + Llama Guard ◀─ tool call ◀─ ALLOW / DENY / ask YOU ◀─ [POLICY] (Z3-proved)"""


def say(tag: str, msg: str) -> None:
    con.print(f"[{TAG.get(tag, 'bold')}]\\[{tag}][/] {msg}")


def short(s, n: int) -> str:
    s = str(s).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


class HorizonShell(cmd.Cmd):
    intro = ""
    doc_header = "commands (type `help <command>`)"

    def __init__(self, config_path: str, overrides: list[str]):
        super().__init__()
        self.config_path, self.overrides = config_path, list(overrides)
        self.samples = []
        self.stage1_variant: str | None = None
        self.mode = "stage1"
        self._s1 = self._s2 = None
        self.history: list[dict] = []
        self.reload(first=True)

    # ============================================================ setup / config ==
    def reload(self, first: bool = False) -> None:
        self.cfg, notes = prepare_config(self.config_path, self.overrides)
        if first:
            self.mode = self.cfg.shell["default_mode"]
            self.log = SessionLog(ROOT / self.cfg.shell["log_dir"])
        prev = self._s2
        self._s1 = Stage1Engine(self.cfg)
        self._s2 = Stage2Engine(self.cfg, self.on_event, None if self.cfg.shell["auto_approve"] else self.ask_confirm)
        if prev is not None:                                # keep the world and the planted injection
            self._s2.suite_name = prev.suite_name
            self._s2.injection_text, self._s2.injection_samples = prev.injection_text, prev.injection_samples
            self._s2.injection_vector, self._s2.attack = prev.injection_vector, prev.attack
        for n in notes:
            say("CONFIG", f"[yellow]{n}[/]")
        self.log.write("config", overrides=self.overrides, notes=notes, flags=self.cfg.flags.model_dump())
        self.prompt = f"horizon[{self.mode}{'·' + self._s2.suite_name if self.mode == 'stage2' else ''}]> "

    def preloop(self) -> None:
        con.print(Panel.fit("[bold]Horizon — interactive defense shell[/]\n"
                            "indirect injection = Stage 1 + Stage 2 · direct injection / jailbreak = [DIRECT] layer",
                            border_style="blue"))
        con.print(FLOW, style="dim")
        self.do_status("")
        con.print("\nTry:  [bold]load all[/]  →  [bold]Summarize this document[/]      (stage1 mode)\n"
                  "      [bold]mode stage2[/] → [bold]where[/] → [bold]inject exfiltration_email[/] → "
                  "[bold]Read the messages in the general channel and summarize them[/]\n"
                  "      [bold]help[/] for everything · [bold]reports[/] for saved results · [bold]exit[/] to quit\n")

    def onecmd(self, line: str) -> bool:
        try:
            return super().onecmd(line)
        except KeyboardInterrupt:
            say("WARN", "interrupted")
        except Exception as e:                               # the shell never dies on a failed command
            say("WARN", f"{type(e).__name__}: {e}")
            if "Connection" in type(e).__name__ or "server" in str(e).lower():
                say("WARN", "is the model server up?  ./scripts/serve_local.sh")
            self.log.write("error", error=repr(e), trace=traceback.format_exc()[-2000:])
        return False

    def emptyline(self) -> bool:
        return False

    def default(self, line: str) -> None:
        """Anything that is not a command is a request to the AI."""
        self.do_ask(line)

    def do_status(self, arg: str) -> None:
        """status — model server, models, guards, π*, λ̂, flags."""
        c = self.cfg
        t = Table(show_header=False, box=None, pad_edge=False)
        t.add_row("model server", "[green]up[/]" if server_alive(c.models) else "[red]down (./scripts/serve_local.sh)[/]")
        t.add_row("models", f"P-LLM {c.models['p_llm']} · Q-LLM {c.models['q_llm']}")
        t.add_row("direct layer", ("Prompt Guard 2 + Llama Guard [green]available[/]" if guards_accessible(c.direct)
                                   else "Prompt Guard 2 / Llama Guard [yellow]gated[/] → only canary check"))
        info = pi_star_info()
        t.add_row("Stage 1 mix", f"π* = {json.dumps({k: round(v, 3) for k, v in info['pi_star'].items()})} "
                  f"(R̄_Q = {info['R_Q']:.3f}, chosen on {info.get('chosen_on')})" if info and not c.flags.stage1_variant
                  else f"fixed variant '{c.flags.stage1_variant or c.shell['default_variant']}'")
        t.add_row("conformal λ̂", json.loads(LAMBDA_HAT.read_text())["lambda_hat"].__repr__() if LAMBDA_HAT.exists()
                  and c.flags.use_conformal else "off → every CONFIRM is asked")
        f = c.flags
        t.add_row("flags", " ".join(f"{k}={'on' if v is True else 'off' if v is False else v}"
                                    for k, v in f.model_dump().items()))
        t.add_row("samples", f"{c.shell['sample_file']} ({len(self.samples)} loaded)")
        t.add_row("session log", str(self.log.path.relative_to(ROOT)))
        con.print(t)

    def do_config(self, arg: str) -> None:
        """config [section] — show the current config (flags, stage1, stage2, shell, report …)."""
        sec = arg.strip() or "flags"
        data = getattr(self.cfg, sec, None)
        data = data.model_dump() if hasattr(data, "model_dump") else data
        if data is None:
            say("WARN", f"no section '{sec}'")
            return
        con.print_json(json.dumps(data, default=str))
        if self.overrides:
            say("CONFIG", f"session overrides: {self.overrides}")

    def do_set(self, arg: str) -> None:
        """set <key>=<value> — override any params.yaml key for this session, e.g. `set flags.m=7`,
        `set flags.stage1_variant=base64`, `set flags.use_policy=false`, `set stage2.policy=strict`."""
        if "=" not in arg:
            say("WARN", "usage: set section.key=value")
            return
        key = arg.split("=", 1)[0].strip()
        old = list(self.overrides)
        self.overrides = [o for o in self.overrides if o.split("=", 1)[0] != key] + [arg.strip()]
        try:
            self.reload()
            say("CONFIG", f"{arg.strip()} applied")
        except Exception as e:
            self.overrides = old
            self.reload()
            say("WARN", f"rejected ({e}); config unchanged")

    def do_unset(self, arg: str) -> None:
        """unset <key> — drop a session override."""
        self.overrides = [o for o in self.overrides if o.split("=", 1)[0] != arg.strip()]
        self.reload()

    def do_mode(self, arg: str) -> None:
        """mode stage1|stage2 — where your plain-text requests go."""
        if arg.strip() not in ("stage1", "stage2"):
            say("WARN", "mode stage1 | mode stage2")
            return
        self.mode = arg.strip()
        self.prompt = f"horizon[{self.mode}{'·' + self._s2.suite_name if self.mode == 'stage2' else ''}]> "
        say("CONFIG", f"mode = {self.mode}")

    # ============================================================ outside sources ==
    def _pick(self, arg: str):
        parts = shlex.split(arg) if arg else []
        path = Path(self.cfg.shell["sample_file"])
        if parts and (Path(parts[0]).exists() or (ROOT / parts[0]).exists()):
            path = Path(parts[0]) if Path(parts[0]).exists() else ROOT / parts[0]
            parts = parts[1:]
        path = path if path.is_absolute() else ROOT / path
        allx = load_samples(path, self.cfg.stage1["canary_prefix"], self.cfg.stage1["canary_hex_bytes"])
        names = parts or ["all"]
        if names == ["all"]:
            return allx, path
        chosen = [s for s in allx if s.name in names]
        missing = set(names) - {s.name for s in chosen}
        if missing:
            say("WARN", f"unknown section(s) {sorted(missing)}; see `samples`")
        return chosen, path

    def do_samples(self, arg: str) -> None:
        """samples [file] — list the sections (attack techniques) of the sample outside-source file."""
        allx, path = self._pick(arg + " all" if arg else "")
        t = Table(title=f"outside-source samples · {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
        t.add_column("section")
        t.add_column("what it tests")
        for s in allx:
            t.add_row(s.name, s.note)
        con.print(t)

    def do_load(self, arg: str) -> None:
        """load [file] [section …|all] — load untrusted outside-source text for stage1 mode.
        A fresh canary word is planted in every section; `load all` loads every section."""
        self.samples, path = self._pick(arg)
        say("SOURCE", f"loaded {len(self.samples)} section(s) from {path.name}: "
                      f"{', '.join(s.name for s in self.samples)}  ({sum(len(s.text) for s in self.samples)} chars)")
        self.log.write("load", file=str(path), sections=[s.name for s in self.samples],
                       canaries={s.name: s.canary for s in self.samples})

    def do_show(self, arg: str) -> None:
        """show — print the loaded outside-source text (canaries highlighted)."""
        for s in self.samples:
            con.print(Panel(s.text.replace(s.canary, f"[reverse]{s.canary}[/]"), title=s.name, subtitle=s.note,
                            border_style="blue"))

    def do_variant(self, arg: str) -> None:
        """variant <name>|pi_star|auto — spotlighting variant for stage1 mode (auto = config / π*)."""
        a = arg.strip()
        if a in ("", "auto", "pi_star"):
            self.stage1_variant = None
        elif a in self._s1.variants:
            self.stage1_variant = a
        else:
            say("WARN", f"variants: {', '.join(self._s1.variants)}")
            return
        say("CONFIG", f"stage1 variant = {self.stage1_variant or 'auto (config / π*)'}")

    # ============================================================ the AI ==========
    def do_ask(self, arg: str) -> None:
        """ask <text> — send a request to the protected AI (same as typing plain text)."""
        if not arg.strip():
            return
        if not server_alive(self.cfg.models):
            say("WARN", "model server is down → ./scripts/serve_local.sh")
            return
        self.history.append({"mode": self.mode, "text": arg})
        (self._ask_stage1 if self.mode == "stage1" else self._ask_stage2)(arg.strip())

    def _direct_input(self, text: str) -> bool:
        if not self.cfg.flags.use_firewall:
            say("DIRECT", "input guard off (gated or disabled) → your prompt passes unchecked")
            return True
        from horizon.direct.guards import PromptGuardInput
        v = PromptGuardInput(self.cfg.direct).check(text)
        say("DIRECT", f"Prompt Guard 2 on your prompt: {'[red]BLOCKED[/]' if v.blocked else '[green]pass[/]'} "
                      f"(score {v.score})")
        self.log.write("input_guard", blocked=v.blocked, score=v.score)
        return not v.blocked

    # ---------------------------------------------------------------- stage 1 --
    def _ask_stage1(self, task: str) -> None:
        if not self.samples:
            say("WARN", "no outside source loaded → `load all` (or `load <file> <section>`)")
            return
        if not self._direct_input(task):
            return
        say("STAGE 1", f"your text = task T (trusted, never transformed). Outside source D⊕δ = "
                       f"{len(self.samples)} section(s), {sum(len(s.text) for s in self.samples)} chars (untrusted)")
        pg = None
        if self.cfg.shell["use_prompt_guard_scan"] and self.cfg.flags.use_firewall:
            from horizon.direct.guards import PromptGuardInput
            pg = PromptGuardInput(self.cfg.direct)
        flagged = {}
        for s in self.samples:
            f = scan_untrusted(s.text, pg)
            if f:
                flagged[s.name] = f
        if flagged:
            t = Table(title="pre-scan of the outside source (indicator only — not part of Horizon's guarantees)")
            t.add_column("section")
            t.add_column("suspected injection")
            for name, fs in flagged.items():
                t.add_row(name, "; ".join(sorted({f.kind for f in fs})))
            con.print(t)
            self.log.write("prescan", flagged={k: [f.kind for f in v] for k, v in flagged.items()})
            if self.cfg.shell["ask_on_detection"]:
                ans = Prompt.ask("[yellow]Injected instructions found in the outside source.[/] Continue with "
                                 "keyed spotlighting anyway?", choices=["y", "n", "s"], default="y")
                if ans == "s":
                    for name, fs in flagged.items():
                        for f in fs:
                            con.print(f"  [blue]{name}[/] · {f.kind}: [dim]{short(f.snippet, 110)}[/]")
                    ans = Prompt.ask("Continue?", choices=["y", "n"], default="y")
                if ans == "n":
                    say("RESULT", "cancelled by you — nothing was sent to the model")
                    self.log.write("cancelled", reason="prescan")
                    return
        else:
            say("STAGE 1", "pre-scan found nothing suspicious")
        variant, pi = self._s1.choose(self.stage1_variant)
        if pi:
            say("STAGE 1", "τ ~ π*: " + ", ".join(f"{k} {v:.2f}" for k, v in pi.items()) + f" → drew [bold]{variant}[/]")
        else:
            say("STAGE 1", f"τ = [bold]{variant}[/] (fixed)")
        with con.status("[MODEL] answering …"):
            r = self._s1.run(task, self.samples, variant)
        key_txt = json.dumps(r.key, default=str) if self.cfg.shell["show_keys"] else f"fingerprint {r.key_fp}"
        say("STAGE 1", f"fresh key κ ~ Unif(K_τ): {key_txt}   ·  log10|K_τ| = {r.log10_K:.1f}")
        say("MATH", f"forging bound q/|K_τ| with q = {self.cfg.theory['q_main']:g}: log10 = {r.log10_forge:.1f}"
                    + ("  → |K| = 1: an attacker who knows τ forges it trivially" if r.log10_K == 0 else ""))
        n = self.cfg.shell["preview_chars"]
        con.print(Panel(short(r.system, n), title="S_κ  (system prompt with this request's key)", border_style="dim"))
        con.print(Panel(short(r.user, n), title="T ‖ τ_κ(D ⊕ δ)  (only the document part is transformed)",
                        border_style="dim"))
        t = Table(title=f"[STAGE 1] attack indicator A(δ,κ) per section · {r.seconds:.1f}s")
        t.add_column("section")
        t.add_column("canary in answer?")
        for name, hit in r.executed.items():
            t.add_row(name, "[red]YES — injection executed[/]" if hit else "[green]no[/]")
        con.print(t)
        hits = [k for k, v in r.executed.items() if v]
        self.log.write("stage1_run", task=task, variant=variant, key_fp=r.key_fp, executed=r.executed,
                       reply=r.reply, seconds=r.seconds)
        if hits:
            con.print(Panel(f"Stage 1 did NOT stop: {', '.join(hits)}.\nThe model followed instructions from the "
                            "outside source although it was spotlighted.", title="⚠ injection executed",
                            border_style="red"))
            if Confirm.ask("Show the (compromised) answer anyway?", default=False):
                con.print(Panel(r.reply, title="model answer (compromised)", border_style="red"))
        else:
            con.print(Panel(r.reply, title="model answer", border_style="green"))
        row = next(x for x in self._s1.tally_rows() if x["variant"] == variant)
        say("MATH", f"session tally for {variant}: s={row['s']}/n={row['n']} · p̂={row['p_hat']:.2f} · "
                    f"Clopper–Pearson p̄={row['p_bar']:.3f} · g_ρ(p̄)={row['g_rho']:.3f} (ρ={self.cfg.theory['rho_main']})")

    def do_compare(self, arg: str) -> None:
        """compare [task] — run the same task on the loaded source with ALL 8 variants (one fresh key each)."""
        task = arg.strip() or (self.history[-1]["text"] if self.history else "Summarize this document.")
        if not self.samples:
            say("WARN", "load an outside source first (`load all`)")
            return
        t = Table(title=f"compare all variants · task: {short(task, 60)}")
        for c in ["variant", "log10|K|", "executed sections", "seconds"]:
            t.add_column(c)
        for v in self._s1.variants:
            with con.status(f"[MODEL] {v} …"):
                r = self._s1.run(task, self.samples, v)
            hits = [k for k, x in r.executed.items() if x]
            t.add_row(v, f"{r.log10_K:.1f}", ", ".join(hits) or "[green]none[/]", f"{r.seconds:.0f}")
            self.log.write("stage1_compare", task=task, variant=v, executed=r.executed, key_fp=r.key_fp)
        con.print(t)

    # ---------------------------------------------------------------- stage 2 --
    def on_event(self, kind: str, d: dict) -> None:
        """Live events from the pipeline (horizon.pipeline listener)."""
        self.log.write(kind, **d)
        n = self.cfg.shell["preview_chars"]
        if kind == "input_guard":
            say("DIRECT", f"Prompt Guard 2: {'[red]BLOCKED[/]' if d['blocked'] else '[green]pass[/]'} (score {d['score']})")
        elif kind == "program":
            say("STAGE 2", "privileged LLM wrote this program from YOUR prompt only (it never sees outside data):")
            con.print(Syntax(d["code"].strip().strip("`").removeprefix("python").strip(), "python",
                             theme="ansi_dark", line_numbers=True))
        elif kind == "program_result":
            if not d["ok"]:
                say("STAGE 2", f"[yellow]interpreter stopped:[/] {short(d['error'], 160)} → P-LLM may repair the program")
        elif kind == "tool_call":
            e = d["event"]
            labels = ", ".join(f"{k}={v}" for k, v in e.arg_caps.items()) or "no args"
            say("STAGE 2", f"tool call [bold]{e.tool}[/]({short(json.dumps(e.args, default=str), 120)})")
            say("POLICY", f"labels: {labels} · control={e.control_cap} → "
                          f"[{DECISION.get(e.decision, 'white')}]{e.decision}[/]"
                          + ("" if d["policy"] else " (policy disabled)"))
        elif kind == "q_start":
            say("STAGE 2", f"outside data needed → quarantined LLM × {d['m']} (no tools, schema {d['schema']}), "
                           f"{len(d['spans'])} untrusted span(s)")
            say("STAGE 1", "each run spotlights the untrusted spans with its OWN τ ~ π* and fresh κ"
                if d["spotlight"] else "Stage 1 off → untrusted spans sent unmarked")
            con.print(Panel(short(d["query"], n), title="query from the program (untrusted parts get spotlighted)",
                            border_style="dim"))
        elif kind == "q_run":
            v = d["canonical"] if d["parsed"] else f"[red]rejected by parser[/] ({short(d['error'], 64)})"
            con.print(f"      run {d['i']}/{d['m']}: τ={d['variant']} κ#{d['key_fp']} → {short(v, 110)}")
        elif kind == "vote":
            ok = "[green]accepted[/]" if d["accepted"] else "[red]REFUSED (no majority → alarm)[/]"
            say("VOTE", f"{ok} · agreement s(x)={d['agreement']:.2f} · {len(d['counts'])} distinct answer(s)")
            info = pi_star_info()
            if info:
                b = hoeffding_vote_bound(d["m"], info["R_Q"])
                say("MATH", f"Hoeffding: P(vote fooled) ≤ exp(−2m(½−R̄_Q)²) = "
                            f"{b if isinstance(b, str) else f'{b:.3g}'} (m={d['m']}, R̄_Q={info['R_Q']:.3f}); "
                            "runs on the same text are correlated → compare with the empirical rate")
        elif kind == "confirm":
            e = d["event"]
            if not e.escalated:
                say("CRC", f"CONFIRM for {e.tool}: s(x)={e.score:.2f} < λ̂={d['lambda_hat']} → [red]DENY[/] (not asked)")
            else:
                say("CRC", f"you answered: {'[green]approved[/]' if e.approved else '[red]declined[/]'}")
        elif kind == "output_guard":
            say("DIRECT", "output guard: " + ("[red]BLOCKED[/] (" + d["reason"] + ")" if d["blocked"] else "[green]pass[/]")
                + f" · canary {'on' if d['canary'] else 'off'} · Llama Guard {'on' if d['llama_guard'] else 'off'}")

    def ask_confirm(self, ev, lambda_hat) -> bool:
        """Policy said CONFIRM and s(x) ≥ λ̂: a human decides (Slide 39)."""
        ents = attacker_entities(self._s2.injection_text or "", "")
        rows = []
        for k, v in ev.args.items():
            sv = short(json.dumps(v, default=str), 90)
            mark = " [red]← attacker entity[/]" if any(x in sv.lower() for x in ents) else ""
            rows.append(f"  {k} = {sv}  [{ev.arg_caps.get(k)}]{mark}")
        con.print(Panel(f"The agent wants to call [bold]{ev.tool}[/]\n" + "\n".join(rows)
                        + f"\ncontrol dependency: {ev.control_cap} · vote agreement s(x) = {ev.score:.2f}"
                        + (f" · λ̂ = {lambda_hat}" if lambda_hat is not None else " · conformal off"),
                        title="[CRC] CONFIRM — this call depends on UNTRUSTED data", border_style="yellow"))
        ok = Confirm.ask("Approve?", default=False)
        self.log.write("user_decision", tool=ev.tool, approved=ok)
        return ok

    def _ask_stage2(self, prompt: str, user_task: str | None = None) -> None:
        s2 = self._s2
        if s2._built is None:
            with con.status("[Z3] proving noninterference for every tool × label × role …"):
                b = s2.built()
            n_tools = len(s2.suite.tools)
            say("Z3", f"policy '{self.cfg.stage2['policy']}' PROVED for all {n_tools} tools of '{s2.suite_name}': "
                      "UNTRUSTED-dependent call ⇒ never ALLOW" if self.cfg.flags.use_policy
                else "[yellow]policy disabled (use_policy=false) — no proof[/]")
            self.log.write("z3", suite=s2.suite_name, proved=self.cfg.flags.use_policy, tools=n_tools,
                           pipeline=b.pipeline.name)
        inj = (f"built-in attack {s2.attack[0]} → {s2.attack[1]}" if s2.attack else
               f"{len(s2.injection_samples)} sample section(s) in {s2.injection_vector or 'every source'}"
               if s2.injection_text else "none (clean world)")
        say("SOURCE", f"world '{s2.suite_name}' · injected into outside sources: {inj}")
        out = s2.run(prompt=None if user_task else prompt, user_task_id=user_task)
        self.log.write("stage2_run", **out.__dict__)
        t = Table(title=f"[RESULT] Stage 2 episode · {out.seconds:.0f}s", show_header=False)
        if out.blocked_by_input_guard:
            t.add_row("input guard", "[red]blocked your prompt[/]")
        if out.utility is not None:
            t.add_row("AgentDojo utility (task done?)", "[green]yes[/]" if out.utility else "[red]no[/]")
        if out.agentdojo_attack_success is not None:
            t.add_row("AgentDojo attack success", "[red]YES[/]" if out.agentdojo_attack_success else "[green]no[/]")
        for c in out.attacker_calls:
            res = ("[red]EXECUTED (ALLOW) — policy bypass G[/]" if c["decision"] == "ALLOW" else
                   "[red]executed after YOUR approval — C[/]" if c["approved"] else
                   "[green]blocked[/]")
            t.add_row(f"attacker call {c['tool']}", f"{c['decision']} · {', '.join(c['entities'])} → {res}")
        if s2.injection_text or s2.attack:
            if not out.attacker_calls:
                t.add_row("attacker calls", "[green]none reached a tool[/]")
            t.add_row("votes fooled (F) / refused", f"{out.fooled_votes} / {out.refused_votes}")
            t.add_row("canary in final answer", ", ".join(out.canary_in_answer) or "[green]none[/]")
        con.print(t)
        con.print(Panel(out.answer or "(empty)", title="agent answer", border_style="green"))

    def do_suite(self, arg: str) -> None:
        """suite <slack|workspace|travel> — the AgentDojo world used in stage2 mode."""
        self._s2.set_suite(arg.strip())
        self.do_mode("stage2")

    def do_tasks(self, arg: str) -> None:
        """tasks — list this world's user tasks (run one with `run user_task_N`) and injection goals."""
        t = Table(title=f"user tasks · {self._s2.suite_name}")
        t.add_column("id")
        t.add_column("prompt")
        for uid, p in self._s2.tasks():
            t.add_row(uid, short(p, 110))
        con.print(t)
        t = Table(title="injection tasks (attacker goals, for `inject attack <name> <id>`)")
        t.add_column("id")
        t.add_column("goal")
        for iid, g in self._s2.injection_tasks():
            t.add_row(iid, short(g, 110))
        con.print(t)

    def do_where(self, arg: str) -> None:
        """where — show the OUTSIDE SOURCES of this world: every slot an attacker can write to, and where it lives."""
        t = Table(title=f"outside sources (injection vectors) · {self._s2.suite_name} · simulated by AgentDojo")
        for c in ["vector", "what it is", "location in the simulated environment"]:
            t.add_column(c)
        for r in self._s2.where():
            t.add_row(r["vector"], r["description"], r["where"])
        con.print(t)
        say("SOURCE", "the agent only reaches these through tool calls; every returned value is labelled "
                      "UNTRUSTED and only the quarantined LLM reads it")

    def do_inject(self, arg: str) -> None:
        """inject [file] <section …|all> [--vector ID] — plant sample text into the outside sources (stage2).
        inject attack <attack_name> <injection_task_id> — use an AgentDojo attack instead.
        inject off — clean world."""
        parts = shlex.split(arg)
        if not parts or parts == ["off"]:
            self._s2.clear_injection()
            say("SOURCE", "injection removed — clean world")
            return
        if parts[0] == "attack":
            self._s2.set_attack(parts[1], parts[2])
            say("SOURCE", f"AgentDojo attack '{parts[1]}' with goal {parts[2]}")
            return
        vector = None
        if "--vector" in parts:
            i = parts.index("--vector")
            vector = parts[i + 1]
            parts = parts[:i] + parts[i + 2:]
        samples, path = self._pick(" ".join(shlex.quote(p) for p in parts))
        self._s2.set_injection_samples(samples, vector)
        say("SOURCE", f"planted {len(samples)} section(s) ({', '.join(s.name for s in samples)}) into "
                      f"{vector or 'every outside source'} of '{self._s2.suite_name}'")
        self.log.write("inject", sections=[s.name for s in samples], vector=vector,
                       canaries={s.name: s.canary for s in samples})

    def do_run(self, arg: str) -> None:
        """run <user_task_id> — run an AgentDojo user task (stage2) with the current injection; reports utility."""
        if not arg.strip():
            say("WARN", "run user_task_0 (see `tasks`)")
            return
        if not server_alive(self.cfg.models):
            say("WARN", "model server is down → ./scripts/serve_local.sh")
            return
        prompt = self._s2.suite.get_user_task_by_id(arg.strip()).PROMPT
        say("STAGE 2", f"user task {arg.strip()}: {prompt}")
        self.mode = "stage2"
        self._ask_stage2(prompt, user_task=arg.strip())

    # ============================================================ analysis ========
    def do_stats(self, arg: str) -> None:
        """stats — what happened in this session (per stage)."""
        rows = self._s1.tally_rows()
        if rows:
            t = Table(title="Stage 1 · this session (A = some injection executed; CP / DRO per Slides 16, 19)")
            for c in ["variant", "s", "n", "p̂", "p̄ (CP)", "g_ρ(p̄)", "log10|K|"]:
                t.add_column(c)
            for r in rows:
                t.add_row(r["variant"], str(r["s"]), str(r["n"]), f"{r['p_hat']:.2f}", f"{r['p_bar']:.3f}",
                          f"{r['g_rho']:.3f}", f"{r['log10_K']:.1f}")
            con.print(t)
        eps = [r for r in self.log.records if r["kind"] == "stage2_run"]
        if eps:
            t = Table(title="Stage 2 · this session")
            for c in ["prompt", "attacker calls (decision)", "fooled / refused votes", "answer canary"]:
                t.add_column(c)
            for r in eps:
                calls = "; ".join(f"{c['tool']}:{c['decision']}{'+approved' if c.get('approved') else ''}"
                                  for c in r["attacker_calls"]) or "none"
                t.add_row(short(r["prompt"], 50), calls, f"{r['fooled_votes']} / {r['refused_votes']}",
                          ", ".join(r["canary_in_answer"]) or "-")
            con.print(t)
        if not rows and not eps:
            say("RESULT", "nothing run yet")

    def do_analyze(self, arg: str) -> None:
        """analyze [question] — ask the report model (config: report.model) to analyse this session's log."""
        r = self.cfg.report
        recs = self.log.records[-self.cfg.shell["analysis_events"]:]
        digest = json.dumps({"stage1_tally": self._s1.tally_rows(), "events": recs}, default=str)[: r["max_context_chars"]]
        q = arg.strip() or ("Analyse this session of the Horizon prompt-injection defense: which stages fired, "
                            "which injections were executed or blocked and by which layer, and what this suggests.")
        mcfg = {"temperature": r["temperature"], "max_tokens": r["max_tokens"], "cache_dir": self.cfg.models["cache_dir"],
                "request_timeout_s": r["request_timeout_s"], "api_base": r["api_base"], "api_key_env": r["api_key_env"],
                "disable_thinking": r["disable_thinking"], "reasoning_effort_off": self.cfg.models["reasoning_effort_off"]}
        llm = LLM(r["model"], mcfg)
        with con.status(f"[MODEL] {r['model']} is analysing {len(recs)} events …"):
            txt = llm.chat([{"role": "system", "content": "You analyse logs of an LLM prompt-injection defense for a "
                             "thesis. Use only facts and numbers present in the log. Be concise and concrete."},
                            {"role": "user", "content": f"{q}\n\nLOG:\n{digest}"}], use_cache=False)
        con.print(Panel(Markdown(txt), title=f"analysis by {r['model']}", border_style="cyan"))
        self.log.write("analysis", model=r["model"], question=q, answer=txt)

    def do_reports(self, arg: str) -> None:
        """reports [stage1|stage2|smoke|pdf] — saved experiment results, figures and the PDF report."""
        what = arg.strip()
        res = ROOT / "results"
        if what in ("", "pdf"):
            t = Table(title="saved results")
            t.add_column("item")
            t.add_column("path")
            for label, p in [("report PDF", ROOT / "report" / "report.pdf"), ("notebook", ROOT / "Horizon.ipynb"),
                             ("π* (Stage 1 → 2)", PI_STAR), ("λ̂ (conformal)", LAMBDA_HAT),
                             ("smoke test", res / "smoke" / "smoke.json"), ("MLflow", res / "mlflow.db")]:
                t.add_row(label, str(p.relative_to(ROOT)) if p.exists() else "[dim]not yet[/]")
            for sp in sorted((res / "stage1").glob("*/summary.json")):
                t.add_row(f"Stage 1 run {sp.parent.name}", str(sp.relative_to(ROOT)))
            for ep in sorted((res / "stage2").glob("*/episodes.csv")):
                t.add_row(f"Stage 2 run {ep.parent.name}", str(ep.relative_to(ROOT)))
            t.add_row("figures", f"{len(list((ROOT / 'figures').rglob('*.png')))} PNG in figures/")
            t.add_row("shell sessions", f"{len(list((res / 'shell').glob('*.jsonl')))} in results/shell/")
            con.print(t)
        if what == "stage1":
            for sp in sorted((res / "stage1").glob("*/summary.json")):
                S = json.loads(sp.read_text())
                t = Table(title=f"Stage 1 · {sp.parent.name} (n={S['n']})")
                for c in ["variant", "p̂", "p̄", "U", "p̄_adp", "log10|K|"]:
                    t.add_column(c)
                for v, d in S["variants"].items():
                    t.add_row(v, f"{d['p_hat']:.3f}", f"{d['p_bar']:.3f}", f"{d['U']:.3f}",
                              f"{d['p_adp_bar']:.3f}" if d['p_adp_bar'] == d['p_adp_bar'] else "-", f"{d['log10_K']:.1f}")
                con.print(t)
        if what == "stage2":
            import pandas as pd
            for ep in sorted((res / "stage2").glob("*/episodes.csv")):
                df = pd.read_csv(ep)
                agg = df.assign(kind=df["style"].where(df["style"] == "benign", "attack")).groupby(
                    ["config", "kind"])[["utility", "asr", "G", "C", "F"]].mean().round(3)
                con.print(Panel(agg.to_string(), title=f"Stage 2 · {ep.parent.name}"))
        if what == "smoke":
            p = res / "smoke" / "smoke.json"
            con.print_json(p.read_text()) if p.exists() else say("RESULT", "no smoke test yet")

    def do_history(self, arg: str) -> None:
        """history — your requests in this session."""
        for i, h in enumerate(self.history, 1):
            con.print(f"{i:3d} [{h['mode']}] {h['text']}")

    def do_flow(self, arg: str) -> None:
        """flow — print the pipeline map again."""
        con.print(FLOW, style="dim")

    def do_exit(self, arg: str) -> bool:
        """exit — leave (the session log stays in results/shell/)."""
        say("RESULT", f"session log: {self.log.path.relative_to(ROOT)}")
        return True

    do_quit = do_exit
    do_EOF = do_exit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_PARAMS))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("-c", "--command", action="append", default=[], help="run these shell commands, then exit")
    args = ap.parse_args()
    sh = HorizonShell(args.config, args.set)
    if args.command:
        sh.preloop()
        for c in args.command:
            con.print(f"[bold]{sh.prompt}[/]{c}")
            sh.onecmd(c)
        return
    try:
        import readline  # noqa: F401  (history + line editing)
    except ImportError:
        pass
    sh.cmdloop()


if __name__ == "__main__":
    main()
