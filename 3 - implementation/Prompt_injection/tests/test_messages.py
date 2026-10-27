"""Every Telegram message is in plain language: verdict first, the same sections, no jargon outside Details."""
from horizon import messages as M
from horizon.config import load_config
from horizon.notify import MAX_CHARS, chunks

CFG = load_config()
ROW = {"variant": "kd", "n": 40, "s": 4, "p_hat": 0.1, "p_bar": 0.2, "U": 0.8, "log10_K": 9.6, "forging": 1e-7,
       "R": 0.3, "feasible": True}
INC = {**ROW, "variant": "datamark_dynamic", "s": 9, "p_hat": 0.225, "log10_K": 4.0, "forging": 0.1, "R": 0.5}


def _all():
    d = {"incumbent": "datamark_dynamic", "incumbent_R": 0.5, "adopted": ["kd"], "adopted_specs": {"kd": {"base": "x"}},
         "rounds": [{"n_attacks": 40}], "final": [ROW, INC]}
    est = {"adopt": {"stage2.loss_ci": "bonferroni"}, "empirical_only": {"stage2.loss_ci": "none"},
           "table": [{"choice": "stage2.loss_ci", "option": o, "coverage": 0.99, "width": 0.1}
                     for o in ("none", "bonferroni")]}
    return [M.campaign_start(CFG, "cluster", "A", "j", 1, 2, ["stage1_fit"], False), M.method_changes(CFG),
            M.autotune_round(CFG, 1, 40, ["kd", "datamark_dynamic"], [ROW, INC], ["kd"]),
            M.autotune_defence(CFG, d, False), M.autotune_estimators(CFG, est, {"stage2.loss_ci": "none"}),
            M.autotune_models(CFG, [{"model": "m", "variant": "none", "asr": 0.3, "U": 0.5}]),
            M.stage1_fit(CFG), M.calibrate(CFG), M.stage1_report(CFG),
            *[M.group(CFG, g, ["• row"], *M.GROUP_INFO[g]) for g in M.GROUP_INFO],
            M.single(CFG, "agent_adaptive", ["• r"]), M.single(CFG, "stage1_altmodel", ["• r"]),
            M.failure("x", {"retries": 2, "error_tail": "boom"}), M.paused(5, "A"), M.finished("complete", 9, 1, 5, None),
            *[M.job_alert(CFG, k, "j", "node", "B") for k in M.ALERTS],
            M.smoke_finished(0, {"total_tests": 9, "attacks": 5, "attacks_got_through": 1, "phases": {"detect": "ok"}}),
            M.smoke_finished(1, {}), M.smoke_finished(0, {"phases": {"a": "ok"}}, {"garak": ["direct.device=cpu"]}),
            M.repair_retry(CFG, "garak", {"retries": 1, "error_tail": "Read timed out"},
                           CFG.campaign["repair_ladder"][0]["set"]),
            M.repair_kept(CFG, "garak", {"retries": 2, "repair": CFG.campaign["repair_ladder"][1]["set"]})]


def test_repair_messages_show_before_after():
    text = M.repair_retry(CFG, "detect", {"retries": 1, "error_tail": "CUDA out of memory"},
                          ["models.request_timeout_s=1800", "direct.device=cpu"])
    assert f"was: {CFG.models['request_timeout_s']}" in text and "now: 1800" in text
    assert "where the guard models run" in text and "now: cpu" in text and "CUDA out of memory" in text


def test_smoke_verdict():
    assert "passed" in M.smoke_finished(0, {"phases": {"a": "ok", "b": "ok"}})
    assert "had problems" in M.smoke_finished(0, {"phases": {"a": "ok", "b": "failed"}})
    assert "Action needed" in M.smoke_finished(1, {})


def test_every_message_has_the_layout_and_fits():
    for text in _all():
        assert M.SEP in text and "📘 What happened" in text
        verdict = text.split(M.SEP, 1)[1].strip().splitlines()[0]          # first line says: act or not
        assert "Nothing to do" in verdict or "Action needed" in verdict or verdict.startswith("⚠️"), verdict
        main = text.split("🔎 Details", 1)[0]                           # symbols only in the Details section
        assert not any(j in main for j in ("ASR", "R̄", "λ̂", "π*", "coverage", "BEFORE:")), main
        assert all(len(c) <= MAX_CHARS for c in chunks(text))


def test_parameters_are_explained_and_read_from_config():
    text = M.stage1_fit(CFG)
    assert f"key guesses assumed for the attacker = {CFG.theory['q_main']:g}" in text and "[theory.q_main]" in text
    assert all(k in M.PARAMS for k in ["stage1.alpha", "stage2.alpha_user", "flags.m"])


def test_change_blocks_show_before_after_why_tested():
    text = M.autotune_defence(CFG, {"incumbent": "datamark_dynamic", "incumbent_R": 0.5, "adopted": ["kd"],
                                    "adopted_specs": {"kd": {}}, "rounds": [{"n_attacks": 40}],
                                    "final": [ROW, INC]}, False)
    for part in ("🔁 Change:", "was:", "now:", "why:", "checked:"):
        assert part in text
    orig = load_config(None, ["stage2.loss_ci=none", "stage2.crc_loss_bound=one"])
    assert "Error margin of the total risk score" not in M.method_changes(orig)   # unchanged: not reported
    assert "Error margin of the total risk score" in M.method_changes(CFG)
