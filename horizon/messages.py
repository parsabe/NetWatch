"""Telegram message texts in plain language: every message can be understood without knowing the maths.

Every message has the same parts, in this order:
    TITLE                one line: what this is about
    verdict              one line: good / bad, and whether YOU need to do anything
    What happened        everyday words, no symbols
    Results              "X of Y attacks got through (Z %, likely between A and B)"
    Next                 what happens now
    Details              settings and symbols for the record (optional, at the very end, can be skipped)
Changes (method upgrades, adopted settings, repairs) are written as: Change · was · now · why · checked.
"""
from __future__ import annotations

import json
from pathlib import Path

from horizon.config import ROOT

SEP = "━━━━━━━━━━━━━━━━━━━━"
RES = ROOT / "results"
NOTHING_TO_DO = "👍 Nothing to do — this is just an update."

# ------------------------------------------------------------------------------------------- glossary
# key → (plain name, what it means). Values are read from the live config, never written here.
PARAMS: dict[str, tuple[str, str]] = {
    "models.q_llm": ("AI model under attack", "the model that reads the untrusted documents"),
    "models.alt_victim": ("second AI model", "a different model, to check that results carry over"),
    "models.attacker": ("attacker AI model", "the model that writes and improves the attacks"),
    "models.parallel_requests": ("requests the GPU handles at once", "more is faster, but needs more GPU memory"),
    "models.request_timeout_s": ("maximum wait for one AI answer (s)", "including waiting in line"),
    "direct.device": ("where the guard models run", "auto = GPU if enough memory is free, else CPU"),
    "campaign.max_retries": ("retries per failed step", "each retry uses safer settings"),
    "stage1.variants": ("marking methods tested", "ways of marking untrusted text before the AI reads it"),
    "stage1.alpha": ("error level", "0.05 means every range is 95 % sure"),
    "stage1.attack_budget": ("tries of the learning attacker", "per document"),
    "stage1.adaptive_early_stop": ("learning attacker stops at first success", ""),
    "stage1.adaptive_transfer_examples": ("attacks shown to the learning attacker as examples", ""),
    "stage1.n_adaptive_targets": ("documents the learning attacker works on", ""),
    "stage1.n_adaptive_eval_keys": ("re-tests of each best attack with a new secret key", ""),
    "stage1.v1_mean_bound": ("rate used in the risk score", "cp = safe upper estimate · empirical = observed"),
    "stage1.rho_from_data": ("attack-shift measured from data", "true = measured, false = hand-picked"),
    "stage1.holm": ("correction for many comparisons", "Holm"),
    "theory.q_main": ("key guesses assumed for the attacker", "used for the chance of guessing the secret key"),
    "theory.rho_main": ("how much real attacks may differ from tested ones", "hand-picked value"),
    "theory.eta_main": ("allowed drop in answer quality", "a defence may not lower quality by more than this"),
    "stage2.alpha_user": ("allowed chance that a user is fooled", "into approving a dangerous action"),
    "stage2.user_mistake_rate": ("assumed user mistake rate", "how often a user wrongly approves when asked"),
    "stage2.harm_rate": ("weight of a fooled vote", "in the total risk score"),
    "stage2.crc_loss_bound": ("margin used for the threshold", "a = real maximum · one = assume 1 (original)"),
    "stage2.crc_group_by": ("threshold per attack type", "style = every attack type is covered on its own"),
    "stage2.crc_min_group": ("smallest attack type handled on its own", "smaller types are merged"),
    "stage2.loss_ci": ("margin of the total risk score", "bonferroni = proven · none = original"),
    "stage2.U_min": ("minimum task success", "every setup must reach this"),
    "stage2.leakage_cap_bits": ("most information a setup may leak (bits)", ""),
    "stage2.side_channel_mode": ("leak through decisions", "trace = counted · fixed = assumed constant"),
    "stage2.attacks": ("agent attack types", ""),
    "stage2.adaptive_budget": ("tries of the learning attacker (agent)", "one try = one agent task"),
    "stage2.parser": ("how strictly read values are checked", "strict = short safe strings only"),
    "stage2.policy": ("rules for actions that use untrusted data", ""),
    "flags.m": ("independent readers per value", "they vote; the majority wins"),
    "direct.input_guard_threshold": ("input guard blocks above", "chance that a prompt is an attack"),
    "direct.output_guard_block": ("output guard blocks", "these verdicts"),
    "redteam.xpia_configs": ("defence setups tested", ""),
    "redteam.xpia_converters": ("disguises of the attack text", ""),
    "redteam.garak_probes_direct": ("garak jailbreak probes", ""),
    "redteam.garak_probes_indirect": ("garak hidden-document probes", ""),
    "autotune.n_start": ("attacks per candidate in round 1", "doubles every round"),
    "autotune.n_max": ("most attacks per candidate", ""),
    "autotune.max_rounds": ("rounds at most", ""),
    "autotune.min_risk_gain": ("how much safer a new method must be", "to be added"),
    "autotune.max_adopt": ("new methods that may be added", ""),
    "autotune.require_proven": ("only formulas with a proof", ""),
    "campaign.min_tests": ("tests needed for a complete run", ""),
}

# plain names of the campaign steps and defence setups
PHASES = {
    "autotune": "Fine-tuning (search for better defence settings)",
    "stage1_fit": "Stage 1, part 1 — test every marking method, choose the best mix",
    "stage1_report": "Stage 1, part 2 — final test on unseen documents",
    "stage1_altmodel": "Stage 1 on a second AI model",
    "calibrate": "Stage 2 — set the 'ask the user' threshold",
    "agent_adaptive": "Agent against a learning attacker",
    "detect": "Guards on known attack prompts",
    "pyrit": "Multi-turn jailbreak attackers (PyRIT)",
    "garak": "Jailbreak scanner (garak)",
}
SETUPS = {"none": "no defence", "stage1": "Stage 1 only", "stage2": "Stage 2 only", "stage1+2": "Stage 1 + 2",
          "full": "full Horizon"}


def phase_name(n: str) -> str:
    if n in PHASES:
        return PHASES[n]
    kind, _, setup = n.partition("_")
    if kind == "ablation":
        return f"Agent attacks — {SETUPS.get(setup, setup)}"
    if kind == "xpia":
        return f"Hidden-document attacks (PyRIT) — {SETUPS.get(setup, setup)}"
    return n


# ----------------------------------------------------------------------------- method baseline (changes)
# Every formula / method choice that differs from the original pipeline:
#   key, original value, plain name, what it was, what it is now ({now} = current value), why, how it was checked.
METHOD_CHANGES = [
    ("stage1.v1_mean_bound", "empirical", "How Stage 1 scores a defence",
     "used the attack success rate as observed",
     "uses a safe upper estimate of it (the rate is at most this high, 95 % sure)",
     "on a small sample the observed rate can be lucky; the rest of the pipeline already used the safe estimate",
     "solver result identical to brute force; the new score is never lower than the old one"),
    ("stage1.rho_from_data", False, "How different real attacks may be from the tested ones",
     "a hand-picked number", "measured from the data: how different the attack sources are from each other",
     "a hand-picked number is an assumption a reviewer can question",
     "unit test; on real data it is 0.56, which makes Stage 1 much more cautious"),
    ("stage1.holm", False, "Comparing defences against each other",
     "28 pairwise tests, each judged on its own", "corrected for doing many tests at once (Holm)",
     "with 28 tests, some look significant by pure chance",
     "unit test; datamark_dynamic still clearly beats every method except base64"),
    ("stage1.attack_budget", 10, "Strength of the learning attacker",
     "10 tries per document", "{now} tries per document",
     "reviewers expect a defence to be tested against strong attackers",
     "it stops early once it succeeds, so the extra cost is small; every try is logged"),
    ("stage1.adaptive_early_stop", False, "Learning attacker stops after its first success",
     "kept trying after it had already won", "stops, and spends its tries on documents that still resist",
     "tries are better used where the defence still holds", "tested in the runner"),
    ("stage1.adaptive_transfer_examples", 0, "Learning attacker starts from attacks that worked elsewhere",
     "started from scratch every time", "is shown up to {now} attacks that already beat this defence elsewhere",
     "a standard way to make an attacker stronger",
     "examples come only from the training documents, never from the final test documents"),
    ("stage2.crc_loss_bound", "one", "Safety margin of the 'ask the user' threshold",
     "assumed the worst mistake costs 1", "uses the real worst case (the user mistake rate)",
     "the old margin was 10× too big: with few cases it could never ask the user and always refused",
     "simulation: users are still fooled less often than allowed (3.0 % vs 5 %)"),
    ("stage2.crc_group_by", "none", "'Ask the user' threshold per attack type",
     "one threshold for all attack types together", "the strictest threshold over all attack types",
     "one shared threshold only protects on average; a rare attack type could do worse",
     "unit test: the result equals the strictest per-type threshold"),
    ("stage2.loss_ci", "none", "Error margin of the total risk score",
     "three separate 95 % margins added up", "three margins that are 95 % sure together (Bonferroni)",
     "three separate 95 % margins are together only guaranteed to be 85 % sure",
     "simulation: correct in 99.98 % of cases"),
    ("stage2.side_channel_mode", "fixed", "Information leaking through the agent's decisions",
     "assumed to be zero", "counted: up to 2 bits per decision",
     "zero was a placeholder; the sequence of decisions itself can reveal information",
     "unit test: 10 decisions = 20 bits"),
    ("campaign.profiles.cluster.parallel_requests", 8, "Requests the GPU handles at once (cluster)",
     "8", "{now}", "8 did not fit the 16 GB GPU: very slow, time-outs and crashes",
     "now fully on the GPU, about 25 tokens per second each, no crashes"),
    ("models.request_timeout_s", 300, "Maximum wait for one AI answer",
     "300 s", "{now} s", "with several steps sharing the GPU, waiting in line alone could take longer than 300 s",
     "no time-outs in the second run"),
]


def _get(cfg, dotted: str):
    """Config value at any depth, e.g. 'stage1.alpha' or 'campaign.profiles.cluster.parallel_requests'."""
    sec, *path = dotted.split(".")
    cur = getattr(cfg, sec, None)
    if cur is not None and not isinstance(cur, dict):
        cur = cur.model_dump() if hasattr(cur, "model_dump") else vars(cur)
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return ", ".join(map(str, v)) if len(v) <= 8 else ", ".join(map(str, v[:8])) + f", … ({len(v)} total)"
    return str(v)


def params(cfg, keys: list[str], extra: dict[str, tuple[str, str]] | None = None) -> list[str]:
    """'• plain name = value (meaning) [config key]' for each key (dotted config key or an `extra` value)."""
    out = []
    for k in keys:
        if extra and k in extra:
            v, meaning = extra[k]
            out.append(f"• {k} = {v}" + (f" ({meaning})" if meaning else ""))
            continue
        label, meaning = PARAMS.get(k, (k, ""))
        out.append(f"• {label} = {_fmt(_get(cfg, k))}" + (f" ({meaning})" if meaning else "") + f" [{k}]")
    return out


def render(title: str, verdict: str, what: str, results: list[str] | None = None, nxt: str | None = None,
           details: list[str] | None = None) -> str:
    parts = [title, SEP, verdict, f"📘 What happened\n{what}"]
    if results:
        parts.append("📊 Results\n" + "\n".join(results))
    if nxt:
        parts.append(f"➡️ Next\n{nxt}")
    if details:
        parts.append("🔎 Details (for the record — you can skip this)\n" + "\n".join(details))
    return "\n\n".join(parts)


def change_block(change: str, was: str, now: str, why: str, checked: str) -> str:
    return f"🔁 Change: {change}\n   was: {was}\n   now: {now}\n   why: {why}\n   checked: {checked}"


def got_through(s: int, n: int, alpha: float) -> str:
    """'12 of 160 attacks got through (7.5 %, likely between 4.0 % and 12.7 %)' — Clopper–Pearson range."""
    from horizon.stage1.stats import cp_interval
    if not n:
        return "no attacks tested"
    lo, hi = cp_interval(int(s), int(n), alpha)
    return f"{s} of {n} attacks got through ({s / n:.1%}, likely between {lo:.1%} and {hi:.1%})"


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _last_error(ph: dict, n: int = 1) -> str:
    lines = [ln.strip() for ln in (ph.get("error_tail") or "").splitlines() if ln.strip()]
    return "\n".join(ln[-300:] for ln in lines[-n:]) or "(no error text in the log)"


def _lane(lane: str | None) -> str:
    return f" · part {lane} of 2" if lane else ""


# ------------------------------------------------------------------------------------------- messages
def campaign_start(cfg, profile: str, lane: str | None, job: str, done: int, planned: int, phases: list[str],
                   resumed: bool) -> str:
    return render(
        f"▶️ Test run {'continued' if resumed else 'started'}{_lane(lane)}",
        NOTHING_TO_DO,
        f"Cluster job {job} {'picked up where the last one stopped' if resumed else 'started'} "
        f"(plan '{profile}'). It runs every experiment in the right order. You get a message after each important "
        "step, when a group of tests is done, if something goes wrong, and at the end.",
        [f"tests planned: at least {planned:,}", f"tests already done earlier: {done:,}", "",
         "Steps in this job, in order:", *[f"{i}. {phase_name(p)}" for i, p in enumerate(phases, 1)]],
        "Next message: a list of what we changed compared with the original method (sent once).",
        [*params(cfg, ["models.q_llm", "models.attacker", "models.alt_victim", "models.parallel_requests",
                       "campaign.min_tests"])])


def method_changes(cfg) -> str:
    blocks = []
    for key, orig, name, was, now, why, checked in METHOD_CHANGES:
        cur = _get(cfg, key)
        if cur is None or cur == orig:
            continue
        blocks.append(change_block(name, was, now.format(now=_fmt(cur)), why, checked))
    if "value_tamper" in (_get(cfg, "stage2.attacks") or []):
        blocks.append(change_block("New agent attack type: changing data values",
                                   "6 attack types that inject instructions",
                                   "+ 1 type that changes data values instead (value_tamper)",
                                   "defences like ours block injected instructions well; the known weak spot is "
                                   "wrong values taken from the data", "tested on 4 AgentDojo task sets"))
    return render(f"🧮 What we changed compared with the original method ({len(blocks)} changes)",
                  NOTHING_TO_DO,
                  "These are all places where this run works differently from the original code. Every change "
                  "can be switched back in config/params.yaml.",
                  ["\n\n".join(blocks) or "No changes: the original methods are used."],
                  "The experiments start now.",
                  ["• full backup of the code before the changes: ~/0_backups/"])


def autotune_round(cfg, rnd: int, n_attacks: int, alive: list[str], scores: list[dict], kept: list[str]) -> str:
    by = {r["variant"]: r for r in scores}
    al = cfg.stage1["alpha"]
    top = sorted((by[a] for a in alive if a in by), key=lambda r: (not r["feasible"], r["R"]))
    rows = [f"{i}. {r['variant']} — risk score {r['R']:.2f}\n   {got_through(int(r['s']), int(r['n']), al)}\n"
            f"   answer quality {r['U']:.2f}" + ("" if r["feasible"] else " ✗ too low, cannot be chosen")
            for i, r in enumerate(top, 1)]
    dropped = [a for a in alive if a not in kept]
    last = kept == alive
    return render(
        f"🔧 Fine-tuning · round {rnd} done",
        NOTHING_TO_DO,
        f"We are looking for a safer way to mark untrusted text. Each candidate was attacked with the same "
        f"{n_attacks} attacks. The safer half goes on to the next round, where it gets twice as many attacks.",
        ["Ranking (lower risk score = safer; answer quality 0–1, higher = better):",
         *rows[:10], *([f"… and {len(rows) - 10} more"] if len(rows) > 10 else [])],
        "That was the last round — the decision follows in the next message." if last else
        f"Go on: {', '.join(kept)}\nDropped: {', '.join(dropped) or '—'}",
        ["• risk score = chance of guessing the secret key + attack success rate, both as safe upper estimates "
         "(R̄ = min{1, q/|K| + g_ρ(p̄)})",
         *params(cfg, ["theory.q_main", "theory.rho_main", "theory.eta_main", "autotune.n_start", "autotune.n_max",
                       "autotune.max_rounds"])])


def autotune_defence(cfg, d: dict, dry_run: bool) -> str:
    al = cfg.stage1["alpha"]
    by = {r["variant"]: r for r in d["final"]}
    inc = by.get(d["incumbent"], {})
    blocks = []
    for a in d["adopted"]:
        r = by[a]
        blocks.append(change_block(
            f"new marking method '{a}' added to the menu",
            f"best existing method '{d['incumbent']}': risk score {inc.get('R', float('nan')):.2f}, "
            f"{got_through(int(inc.get('s', 0)), int(inc.get('n', 0)), al)}",
            f"'{a}': risk score {r['R']:.2f}, {got_through(int(r['s']), int(r['n']), al)}",
            f"safer by {inc.get('R', 0) - r['R']:.2f} in risk score, and answer quality stays good enough "
            f"({r['U']:.2f})",
            f"{len(d['rounds'])} rounds, the last with {d['rounds'][-1]['n_attacks']} attacks; statistically not "
            f"worse than '{d['incumbent']}'"))
    verdict = (f"✅ {len(blocks)} new, safer method(s) added." if blocks
               else f"👍 No change: no candidate was clearly safer than '{d['incumbent']}'.")
    return render(
        "🔧 Fine-tuning · result" + (" (test only — nothing was added)" if dry_run else ""),
        verdict + " Nothing to do.",
        "We tested new ways of marking untrusted text (longer secret markers, secret-key delimiters, a reminder "
        "after the text). Better ones are ADDED to the menu; nothing is removed. Later, Stage 1 decides how often "
        "each method is used.",
        blocks or [f"best existing method '{d['incumbent']}' stays the reference "
                   f"(risk score {inc.get('R', float('nan')):.2f})"],
        "Stage 1 now tests every method on all attacks and chooses the best mix.",
        params(cfg, ["autotune.min_risk_gain", "autotune.max_adopt"]))


# plain names of the formula choices the estimator check looks at, and of their options
CHOICES = {
    "stage2.crc_loss_bound": "Safety margin of the 'ask the user' threshold",
    "stage2.loss_ci": "Error margin of the total risk score",
    "stage2.vote_bound": "Error margin of the majority vote",
    "stage1.v1_mean_bound": "Rate used in the Stage 1 risk score",
}
OPTIONS = {"a": "real worst case", "one": "assume 1 (original)", "bonferroni": "Bonferroni (proven)",
           "bernstein": "Bernstein (proven)", "none": "original, no proof", "cp": "safe upper estimate",
           "empirical": "observed rate", "hoeffding": "Hoeffding", "chernoff": "Chernoff", "exact": "exact"}


def autotune_estimators(cfg, est: dict, before: dict) -> str:
    tab = {}
    for r in est["table"]:
        tab.setdefault(r["choice"], []).append(r)
    emp = est.get("empirical_only") or {}
    blocks, switched = [], 0
    for choice, rows in tab.items():
        new, old = est["adopt"].get(choice), before.get(choice)
        use = new if new is not None else old
        lines = []
        for r in sorted(rows, key=lambda r: r["width"]):
            tag = " ✔ used" if r["option"] == use else (" — narrower, but no proof → not used"
                                                        if emp.get(choice) == r["option"] else "")
            lines.append(f"   '{r['option']}' — {OPTIONS.get(r['option'], r['option'])}: right {r['coverage']:.0%} "
                         f"of the time, width {r['width']:.3g}{tag}")
        name = CHOICES.get(choice, choice)
        if new is not None and new != old:
            switched += 1
            blocks.append(change_block(name, f"{old} ({OPTIONS.get(str(old), old)})",
                                       f"{new} ({OPTIONS.get(str(new), new)})",
                                       "narrower, still right in every test, and mathematically proven",
                                       "\n" + "\n".join(lines)))
        else:
            blocks.append(f"• {name}: keep '{old}' — {OPTIONS.get(str(old), old)}\n" + "\n".join(lines))
    return render("🔧 Fine-tuning · checked our error-margin formulas",
                  (f"🔁 {switched} formula(s) switched to a more precise proven one." if switched else
                   "✅ No change: the formulas we use are correct and the best proven option.") + " Nothing to do.",
                  f"Every result we report comes with an error margin ('the true value is at most …'). There are "
                  f"several formulas for such a margin. We made {cfg.autotune['sim_trials']} fake data sets where we "
                  "know the true answer and checked each formula:\n"
                  "• right: how often the margin really contains the true value (must be at least 95 %)\n"
                  "• width: how big the margin is (smaller = more precise)",
                  blocks, "Settings in use are saved in config/autotuned.yaml and used by every analysis.",
                  params(cfg, ["autotune.require_proven", "stage1.alpha"]))


def autotune_models(cfg, rows: list[dict]) -> str:
    res = [f"• {r['model']} with {SETUPS.get(r['variant'], r['variant'])}: {r['asr']:.0%} of attacks got through, "
           f"answer quality {r['U']:.2f}" for r in rows if "asr" in r]
    res += [f"• {r['model']}: {r['status']}" for r in rows if "status" in r]
    return render("🧪 Fine-tuning · AI models compared (for information only)", NOTHING_TO_DO,
                  "The same short test on every listed AI model: once without defence, once with the best method.",
                  res, "The model under attack is what we study, so it is never switched automatically.",
                  params(cfg, ["models.q_llm"]))


def stage1_fit(cfg) -> str:
    s = _json(RES / "stage1" / "fit" / "summary.json")
    ps = _json(RES / "stage1" / "pi_star.json")
    al = cfg.stage1["alpha"]
    res = []
    for t, v in s.get("variants", {}).items():
        res.append(f"• {t}: {got_through(v['s'], v['n'], al)}; answer quality {v['U']:.2f}"
                   + (f"\n   learning attacker: {got_through(v['s_a'], v['n_a'], al)}" if v.get("n_a") else ""))
    mix = ", ".join(f"{k} {v:.0%}" for k, v in sorted((ps.get("pi_star") or {}).items(), key=lambda x: -x[1]))
    details = params(cfg, ["theory.q_main", "theory.eta_main", "stage1.rho_from_data", "stage1.v1_mean_bound",
                           "stage1.attack_budget", "stage1.n_adaptive_targets", "stage1.alpha"])
    if ps:
        details = [f"• chosen mix π* = {mix}; worst-case risk V1 = {ps['V1_fit']:.3f}; single best method "
                   f"τ* = {ps.get('tau_star')}; ρ = {ps['rho']:.3g} ({ps.get('rho_source', 'config')})", *details]
    return render("🧩 Stage 1, part 1 done — best defence mix chosen",
                  (f"✅ Safest mix: {mix}." if mix else "⚠️ No mix was written — check the Stage 1 log.")
                  + " Nothing to do.",
                  "Every attack document went through every marking method, each time with a new secret key. "
                  "Clean documents measured answer quality (0–1, higher = better). Then the safest mix of methods "
                  "was chosen: safe even against an attacker who knows the mix but not the key.",
                  res or ["(no results file found)"],
                  "Stage 2 uses this mix. Next: the same test on documents that were not used for any choice.",
                  details)


def calibrate(cfg) -> str:
    d = _json(RES / "stage2" / "lambda_hat.json")
    m, a_user = cfg.flags.m, cfg.stage2["alpha_user"]
    res = [f"threshold = {d.get('lambda_hat')} (from {d.get('n')} risky actions)"]
    res += [f"• {k}: {v['lambda_hat']} (from {v['n']} actions)" for k, v in (d.get("per_style") or {}).items()]
    return render("🎚 Stage 2 — 'ask the user' threshold set",
                  f"✅ Threshold = {d.get('lambda_hat')}. Nothing to do.",
                  f"The agent did tasks while under attack. For every risky action, {m} independent readers voted, "
                  "and we recorded how much they agreed (0–1). The threshold decides: agreement at or above it → ask "
                  "the user; below it → refuse automatically. It is set so that a user is fooled into approving a "
                  f"dangerous action at most {a_user:.0%} of the time, for every attack type.",
                  res, "All later agent tests use this threshold.",
                  [f"• threshold λ̂; one for all types together: {d.get('lambda_hat_pooled')}; "
                   f"margin constant B = {d.get('loss_bound_B')}",
                   *params(cfg, ["stage2.alpha_user", "stage2.user_mistake_rate", "stage2.crc_loss_bound",
                                 "stage2.crc_group_by"])])


def stage1_report(cfg) -> str:
    s = _json(RES / "stage1" / "report" / "summary.json")
    al = cfg.stage1["alpha"]
    res = [f"• {t}: {got_through(v['s'], v['n'], al)}; answer quality {v['U']:.2f}"
           for t, v in s.get("variants", {}).items()]
    r = s.get("report") or {}
    if r:
        res.append(f"Highest chance that one AI call is fooled, handed to Stage 2: {r.get('R_Q')}")
    return render("📄 Stage 1, part 2 done — final numbers on unseen documents", NOTHING_TO_DO,
                  "The same tests on documents that were never used to choose anything. These are the honest final "
                  "Stage 1 numbers.",
                  res or ["(no results file found)"], "These numbers go into the paper.",
                  [f"• worst-case risk V1 at the chosen mix = {r.get('V1_at_pi_star_fit')}; R̄_Q = {r.get('R_Q')}",
                   *params(cfg, ["stage1.alpha", "theory.q_main"])] if r else params(cfg, ["stage1.alpha"]))


GROUP_NAMES = {"direct layer (guards · PyRIT · garak)": "guards and jailbreak attackers",
               "AgentDojo ablations": "agent attacks with parts of Horizon on and off",
               "PyRIT XPIA (all defence setups)": "hidden-document attacks against every defence setup"}


def group(cfg, name: str, rows: list[str], keys: list[str], what: str) -> str:
    return render(f"✅ Finished: {GROUP_NAMES.get(name, name)}", NOTHING_TO_DO, what, rows or ["(no results)"],
                  "'Got through' = the attack reached its goal. 'Likely between' = where the true rate lies, "
                  "95 % sure.", params(cfg, keys))


GROUP_INFO = {
    "direct layer (guards · PyRIT · garak)": (
        ["direct.input_guard_threshold", "direct.output_guard_block", "redteam.garak_probes_direct",
         "redteam.garak_probes_indirect"],
        "Jailbreak and injection prompts against the two guards (one checks what goes in, one checks what comes "
        "out), plus automatic attackers that try over several turns (PyRIT) and a jailbreak scanner (garak)."),
    "AgentDojo ablations": (
        ["stage2.attacks", "flags.m", "stage2.parser", "stage2.policy", "stage2.U_min"],
        "The same agent attacks with parts of Horizon switched off and on: no defence · Stage 1 only · "
        "Stage 2 only · Stage 1 + 2 · full Horizon. This shows what each part adds."),
    "PyRIT XPIA (all defence setups)": (
        ["redteam.xpia_configs", "redteam.xpia_converters"],
        "PyRIT hides each attack (sometimes disguised) in a document; every defence setup then processes it."),
}


def single(cfg, n: str, rows: list[str]) -> str:
    if n == "stage1_fit":
        return stage1_fit(cfg)
    if n == "calibrate":
        return calibrate(cfg)
    if n == "stage1_report":
        return stage1_report(cfg)
    if n == "agent_adaptive":
        return render("🗡 Done: agent against a learning attacker", NOTHING_TO_DO,
                      "An attacker AI rewrote its attack after every failed try, against full Horizon. This is the "
                      "strongest attack in the whole run.", rows,
                      "Compare with 'Agent attacks — full Horizon': if the numbers are similar, Horizon also holds "
                      "against an attacker who learns.",
                      params(cfg, ["stage2.adaptive_budget", "models.attacker"]))
    if n == "stage1_altmodel":
        return render("🔁 Done: Stage 1 on a second AI model", NOTHING_TO_DO,
                      "The final Stage 1 test on a different AI model, with the same chosen mix.", rows,
                      "If the methods rank the same way, the results are not specific to one model.",
                      params(cfg, ["models.alt_victim"]))
    if n == "autotune":
        return ""                                         # autotune sends its own detailed messages
    return render(f"✅ Done: {phase_name(n)}", NOTHING_TO_DO, "", rows)


def failure(n: str, ph: dict) -> str:
    k = ph.get("retries", 0)
    return render(f"❌ Step failed: {phase_name(n)}",
                  "⚠️ Action needed: this step and the steps that need it will not run. The other steps continue.",
                  f"The step failed {k + 1} time(s)" + (", also with safer settings." if k else "."),
                  ["Last error:", _last_error(ph, 3)],
                  "Fix the cause, then submit again — finished work is kept, nothing is repeated.",
                  [f"• step id: {n}", f"• full log: results/campaign/logs/{n}.log"])


def _repair_blocks(cfg, before: list[str], after: list[str], why: str, checked: str) -> list[str]:
    old = dict(kv.split("=", 1) for kv in before)
    blocks = []
    for kv in after:
        k, v = kv.split("=", 1)
        was = old.get(k, _fmt(_get(cfg, k)))
        if was != v:
            blocks.append(change_block(PARAMS.get(k, (k, ""))[0], was, v, why, checked))
    return blocks


def repair_retry(cfg, n: str, ph: dict, nxt: list[str]) -> str:
    """A step failed; the next attempt adds the next repair_ladder step (safer settings)."""
    k, mx = ph.get("retries", 0), cfg.campaign["max_retries"]
    ladder = cfg.campaign.get("repair_ladder") or []
    step = ladder[k - 1] if 0 < k <= len(ladder) else {}
    return render(f"🔧 Step failed, trying again with safer settings ({k} of {mx})",
                  "👍 Nothing to do yet — this happens automatically.",
                  f"Step: {phase_name(n)}\nLast error: {_last_error(ph)}"
                  + ("\nThis try runs the step alone, with nothing else on the GPU." if step.get("alone") else ""),
                  _repair_blocks(cfg, ph.get("repair", []), nxt, step.get("why", "safer settings"),
                                 "this try — the next message says whether it worked"),
                  "If the step now works, these settings are kept for it.",
                  [f"• step id: {n}", f"• full log: results/campaign/logs/{n}.log"])


def repair_kept(cfg, n: str, ph: dict) -> str:
    """A step passed only with repair settings; they are kept for every later run of it."""
    return render(f"✅ Step works now: {phase_name(n)}",
                  "👍 Nothing to do — it was fixed automatically.",
                  f"It worked after {ph.get('retries', 0)} retry(s) with safer settings. Those settings are kept for "
                  "this step only.",
                  _repair_blocks(cfg, [], ph.get("repair", []), "the step failed with the original values",
                                 "the step worked with them"),
                  "The big runs (part A and B) use these settings for this step.",
                  ["• saved in config/repairs.yaml (delete the file to undo)", f"• step id: {n}"])


def paused(total: int, lane: str | None) -> str:
    return render(f"⏸ Paused before the time limit{_lane(lane)}", NOTHING_TO_DO,
                  "A cluster job may run 24 hours. This one stopped cleanly 15 minutes before that and queued the "
                  "next job.", [f"tests done so far: {total:,}"],
                  "The next job continues where this one stopped; nothing is repeated.")


def finished(status: str, total: int, through: int, attacks: int, lane: str | None) -> str:
    good = status == "complete"
    return render(f"🏁 Test run {'complete' if good else 'finished with problems'}{_lane(lane)}",
                  "👍 Nothing to do." if good else
                  "⚠️ Action needed: some steps did not finish — see the earlier ❌ messages.",
                  "All steps of this job have ended.",
                  [f"tests done: {total:,}", f"attacks that got through: {through:,} of {attacks:,}"],
                  "Full report: report/campaign/campaign_report.pdf")


# ------------------------------------------------------------------------------------------- job script alerts
# Sent from cluster/*.pbs via `python -m horizon.messages ...`, so shell-level problems use the same layout.
ALERTS = {
    "no_gpu": ("❌ No GPU found",
               "⚠️ Action needed: everything would run on the CPU (very slow).",
               "Before starting, the job checks the GPU. This cluster node showed none.",
               "Check the queue (-q) and 'ngpus=1' at the top of the job script, then submit again."),
    "server_failed": ("❌ The AI model server did not start",
                      "⚠️ Action needed: the job stopped here.",
                      "The model server must answer before any test can run. It shut down instead.",
                      "Look at results/logs/ollama.log, fix the cause, submit again — finished work is kept."),
    "not_on_gpu": ("⚠️ The AI model does not fully fit on the GPU",
                   "⚠️ Tests continue, but every answer will be slow.",
                   "Part of the model was moved to the CPU because the GPU memory was full.",
                   "If it stays slow: lower the requests handled at once, then submit again."),
    "resubmit_failed": ("⚠️ Could not queue the next job automatically",
                        "⚠️ Action needed: submit the next job by hand (command below).",
                        "The job paused before its 24 h limit and tried to queue the next one, which failed.",
                        "Finished work is kept; the next job continues where this one stopped."),
}


def job_alert(cfg, kind: str, job: str, host: str, lane: str | None) -> str:
    title, verdict, what, nxt = ALERTS[kind]
    results = [f"job: {job}", f"cluster node: {host}"]
    if kind == "resubmit_failed":
        results.append(f"command: qsub {f'-v LANE={lane} ' if lane else ''}cluster/horizon_campaign.pbs")
    details = params(cfg, ["models.q_llm", "models.parallel_requests"]) if kind == "not_on_gpu" else None
    return render(title + _lane(lane), verdict, what, results, nxt, details)


def smoke_finished(rc: int, summary: dict, repairs: dict[str, list[str]] | None = None) -> str:
    phases = summary.get("phases", {})
    icon = {"ok": "✅", "failed": "❌", "skipped": "⏭"}
    plain = {"ok": "worked", "failed": "failed", "skipped": "skipped (needs a failed step)"}
    good = rc == 0 and bool(phases) and all(s == "ok" for s in phases.values())
    rows = [f"{icon.get(s, '•')} {phase_name(n)}: {plain.get(s, s)}" for n, s in phases.items()]
    rows += [f"🔧 fixed automatically: {phase_name(n)}" for n in (repairs or {})]
    if not summary:
        rows = ["The test stopped before it could write its summary."]
    return render("🧪 Trial run " + ("passed" if good else "had problems"),
                  "✅ All good — the big run starts now. Nothing to do." if good else
                  "⚠️ Action needed: the big run was cancelled automatically, because the trial did not pass.",
                  "Before the big run, the whole pipeline ran once at a tiny size (in a copy, ~/0_smoke), "
                  "to catch problems early.",
                  rows,
                  ("Parts A and B of the big run start now" + (", with the automatic fixes." if repairs else "."))
                  if good else
                  "Fix the cause of the ❌ steps, then submit the trial and parts A and B again.",
                  [f"• exit code {rc} (0 = all worked · 2 = some steps failed · 3 = paused)",
                   f"• tests: {summary.get('total_tests', 0):,}; attacks that got through: "
                   f"{summary.get('attacks_got_through', 0):,} of {summary.get('attacks', 0):,}",
                   "• logs: horizon_smoke.log, ~/0_smoke/results/campaign/logs/"])


def main(argv: list[str]) -> int:
    """python -m horizon.messages alert <kind>  |  python -m horizon.messages smoke <exit code> <summary.json>"""
    import os
    import socket

    from horizon.config import load_config
    from horizon.notify import send
    if argv[:1] == ["alert"] and len(argv) == 2 and argv[1] in ALERTS:
        text = job_alert(load_config(), argv[1], os.environ.get("PBS_JOBID", "local"),
                         socket.gethostname().split(".")[0], os.environ.get("LANE") or None)
    elif argv[:1] == ["smoke"] and len(argv) == 3:
        import yaml
        try:
            repairs = yaml.safe_load((ROOT / "config" / "repairs.yaml").read_text()) or {}
        except Exception:
            repairs = {}
        text = smoke_finished(int(argv[1]), _json(Path(argv[2])), repairs)
    else:
        print(main.__doc__)
        return 2
    return 0 if send(text) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
