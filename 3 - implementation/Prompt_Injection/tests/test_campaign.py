"""Campaign orchestration, offline: plan sizes, dependency scheduling with failures, merge + tally of results."""
import sys

import pandas as pd
import pytest

from horizon.config import ROOT, load_config

sys.path.insert(0, str(ROOT / "eval"))
import campaign as C  # noqa: E402

CFG = load_config()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    res = tmp_path / "results"
    monkeypatch.setattr(C, "RES", res)
    monkeypatch.setattr(C, "CAMP", res / "campaign")
    monkeypatch.setattr(C, "LOGS", res / "campaign" / "logs")
    monkeypatch.setattr(C, "STATE", res / "campaign" / "state.json")
    monkeypatch.setattr(C, "LEDGER", res / "campaign" / "ledger.jsonl")
    monkeypatch.setattr(C, "POLL_S", 0)
    monkeypatch.setattr(C, "ensure_model_server", lambda cfg, prof: None)     # no real Ollama in unit tests
    return res


def test_both_profiles_plan_at_least_min_tests():
    for name in CFG.campaign["profiles"]:
        prof = C.profile_of(CFG, name)
        cfg = load_config(None, prof["set"])
        plan = C.build_plan(cfg, prof, [])
        assert {p.name for p in plan} == set(C.ORDER)
        assert sum(p.planned or 0 for p in plan) >= prof["min_tests"], name
        for p in plan:                                   # dependencies exist and come earlier in ORDER
            assert all(C.ORDER.index(d) < C.ORDER.index(p.name) for d in p.needs), p.name


def test_scheduler_runs_in_order_and_skips_dependents_of_failures(sandbox):
    ok, bad = [sys.executable, "-c", "pass"], [sys.executable, "-c", "raise SystemExit(1)"]
    plan = [C.Phase("stage1_fit", "", "", bad, [], "document", 1, 1),
            C.Phase("calibrate", "", "", ok, ["stage1_fit"], "agent", 1, 1),
            C.Phase("ablation_none", "", "", ok, [], "agent", 1, 1),
            C.Phase("ablation_full", "", "", ok, ["stage1_fit", "calibrate"], "agent", 1, 1)]
    st = {"phases": {}}
    cfg = load_config(None, ["campaign.max_retries=0"])
    C.run_phases(plan, {"workers": 2, "set": []}, st, [], cfg)
    s = {k: v["status"] for k, v in st["phases"].items()}
    assert s == {"stage1_fit": "failed", "calibrate": "skipped", "ablation_none": "ok", "ablation_full": "skipped"}
    assert "error_tail" in st["phases"]["stage1_fit"]
    plan[0].argv = ok                                    # fixed → a re-run retries failed / skipped, keeps ok
    C.run_phases(plan, {"workers": 2, "set": []}, st, [], cfg)
    assert all(v["status"] == "ok" for v in st["phases"].values())


def test_merge_and_tally_count_every_test(sandbox):
    s1 = sandbox / "stage1" / "fit"
    s1.mkdir(parents=True)
    pd.DataFrame({"attack_id": ["a", "b", "a", "b"], "variant": ["none", "none", "base64", "base64"],
                  "A": [1, 1, 0, 1], "reply": ["x"] * 4}).to_csv(s1 / "matrix.csv", index=False)
    for ab, asr in (("none", 1), ("full", 0)):
        d = sandbox / "stage2" / f"report-{ab}"
        d.mkdir(parents=True)
        pd.DataFrame({"config": [f"{ab}|m=5", f"{ab}|m=5"], "suite": ["slack"] * 2, "user_task": ["u0"] * 2,
                      "injection_task": ["", "i0"], "style": ["benign", "important_instructions"],
                      "attack_id": ["slack/u0/benign", "slack/u0/i0/ii"], "utility": [1, 1], "asr": [0, asr],
                      "G": [0, asr], "C": [0, 0], "F": [0, 0], "alarms": [0, 0], "seconds": [1, 1]}
                     ).to_csv(d / "episodes.csv", index=False)
    t = sandbox / "testing"
    t.mkdir(parents=True)
    pd.DataFrame({"round": [0, 0], "config": ["none", "stage1"], "converter": ["none"] * 2, "attack_id": ["a", "a"],
                  "source": ["s"] * 2, "category": ["c"] * 2, "success": [1, 0], "detail": [""] * 2,
                  "answer": [""] * 2, "seconds": [1, 1]}).to_csv(t / "xpia_items__none.csv", index=False)
    C.merge_outputs()
    assert len(pd.read_csv(sandbox / "stage2" / "report" / "episodes.csv")) == 4
    df, _ = C.tally(CFG)
    assert C.total_tests(df) == 4 + 4 + 2                # Stage 1 matrix + agent episodes + XPIA
    full = df[(df["area"] == "Stage 2") & (df["config"] == "full")].iloc[0]
    assert (full["attacks"], full["got_through"], full["benign_ok"]) == (1, 0, 1)
    s1n = df[(df["group"] == "document attacks (fit)") & (df["config"] == "none")].iloc[0]
    assert s1n["got_through"] == 2 and s1n["asr_cp_hi"] <= 1


def test_failed_phase_is_retried(sandbox, tmp_path):
    flag = tmp_path / "second_try"
    flaky = [sys.executable, "-c", f"import pathlib,sys; p=pathlib.Path({str(flag)!r}); "
             "sys.exit(0) if p.exists() else (p.touch(), sys.exit(1))"]
    plan = [C.Phase("detect", "", "", flaky, [], "guard", 1, 1)]
    st = {"phases": {}}
    C.run_phases(plan, {"workers": 1, "set": []}, st, [], load_config(None, ["campaign.max_retries=1"]))
    assert st["phases"]["detect"]["status"] == "ok" and st["phases"]["detect"]["retries"] == 1
