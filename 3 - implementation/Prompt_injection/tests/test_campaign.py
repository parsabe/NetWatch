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
    monkeypatch.setattr(C, "REPAIRS", tmp_path / "repairs.yaml")             # never touch config/repairs.yaml
    monkeypatch.setattr(C, "ensure_model_server", lambda cfg, prof: None)     # no real Ollama in unit tests
    return res


def test_every_profile_plans_at_least_min_tests():
    for name in CFG.campaign["profiles"]:
        prof = C.profile_of(CFG, name)
        cfg = load_config(None, prof["set"])
        plan = C.build_plan(cfg, prof, [])
        assert {p.name for p in plan} == set(C.ORDER) - set(prof["skip"]), name
        assert sum(p.planned or 0 for p in plan) >= prof["min_tests"], name
        for p in plan:                                   # dependencies exist and come earlier in ORDER
            assert all(C.ORDER.index(d) < C.ORDER.index(p.name) for d in p.needs), p.name
            assert all(C.ORDER.index(d) < C.ORDER.index(p.name) for d in p.wants), p.name


def bounded_plan(extra_set=()):
    prof = C.profile_of(CFG, "bounded")
    cfg = load_config(None, [*prof["set"], *extra_set])
    return C.build_plan(cfg, prof, []), prof


def test_bounded_profile_stays_between_2000_and_4000_tests():
    """THE budget: 2,000 ≤ certain tests and at most 4,000 in all, every phase sized, and nothing left unknown."""
    plan, prof = bounded_plan()
    firm, upper = C.budget(plan)
    assert prof["min_tests"] == 2000 and prof["max_tests"] == 4000
    assert 2000 <= firm <= upper <= 4000, (firm, upper)
    assert all(p.planned is not None for p in plan), [p.name for p in plan if p.planned is None]
    assert C.budget_problems(plan, prof) == []


def test_budget_is_enforced_when_a_setting_blows_it_up():
    for extra in (["redteam.garak_prompt_cap=500"],            # garak at its old size
                  ["stage1.pilot_n=500", "stage1.pilot_clean=150"]):
        plan, prof = bounded_plan(extra)
        assert any("above max_tests" in m for m in C.budget_problems(plan, prof)), extra


def test_bounded_profile_skips_autotune_and_no_phase_depends_on_it():
    plan, _ = bounded_plan()
    names = {p.name for p in plan}
    assert "autotune" not in names and "stage1_altmodel" not in names
    assert all("autotune" not in p.needs for p in plan)


def test_failed_autotune_does_not_skip_stage1_fit(sandbox):
    """The cascade that sank the real campaign: autotune failed → stage1_fit skipped → 12 more phases skipped."""
    ok, bad = [sys.executable, "-c", "pass"], [sys.executable, "-c", "raise SystemExit(1)"]
    plan = [C.Phase("autotune", "", "", bad, [], "document", None, None, exclusive=True),
            C.Phase("stage1_fit", "", "", ok, [], "document", 1, 1, wants=["autotune"]),
            C.Phase("calibrate", "", "", ok, ["stage1_fit"], "agent", 1, 1)]
    st = {"phases": {}}
    C.run_phases(plan, {"workers": 2, "set": []}, st, [], load_config(None, ["campaign.max_retries=0"]))
    s = {k: v["status"] for k, v in st["phases"].items()}
    assert s == {"autotune": "failed", "stage1_fit": "ok", "calibrate": "ok"}


def test_stage1_fit_waits_for_autotune_to_finish(sandbox, tmp_path):
    log = tmp_path / "events"
    def job(name):
        return [sys.executable, "-c", f"import time; open({str(log)!r},'a').write('{name}+\\n'); time.sleep(0.4); "
                                      f"open({str(log)!r},'a').write('{name}-\\n')"]
    plan = [C.Phase("autotune", "", "", job("autotune"), [], "document", None, None),
            C.Phase("stage1_fit", "", "", job("stage1_fit"), [], "document", 1, 1, wants=["autotune"])]
    st = {"phases": {}}
    C.run_phases(plan, {"workers": 2, "set": []}, st, [], CFG)
    assert log.read_text().split() == ["autotune+", "autotune-", "stage1_fit+", "stage1_fit-"]


def test_garak_tests_are_counted_once_per_output_not_once_per_detector(sandbox):
    t = sandbox / "testing"
    t.mkdir(parents=True)
    pd.DataFrame([{"target": "bare", "family": "direct", "probe": "dan.Dan_11_0", "detector": "dan.DAN", "fails": 3, "n": 10},
                  {"target": "bare", "family": "direct", "probe": "dan.Dan_11_0", "detector": "mitigation.Prefixes",
                   "fails": 5, "n": 10}]).to_csv(t / "garak_items.csv", index=False)
    df, _ = C.tally(CFG)
    g = df[df["phase"] == "garak"].iloc[0]
    assert (g["tests"], g["got_through"]) == (10, 5)         # 10 outputs, flagged by the strictest detector


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


def test_repair_ladder_escalates_and_keeps_what_worked(sandbox, tmp_path, monkeypatch):
    """Retry k adds ladder steps 1..k; the settings that made the phase pass are kept and used next time."""
    sent = []
    monkeypatch.setattr(C, "notify", sent.append)
    seen = tmp_path / "argv"
    # passes only once it is given the second ladder step (models.parallel_requests=1)
    picky = [sys.executable, "-c", f"import sys; open({str(seen)!r},'a').write(' '.join(sys.argv[1:])+'\\n'); "
             "sys.exit(0 if 'models.parallel_requests=1' in sys.argv else 1)"]
    plan = [C.Phase("garak", "", "", picky, [], "garak", 1, 1)]
    st = {"phases": {}}
    C.run_phases(plan, {"workers": 1, "set": []}, st, [], CFG)
    runs = seen.read_text().splitlines()
    ladder = CFG.campaign["repair_ladder"]
    assert len(runs) == 3 and runs[0] == ""
    assert all(kv in runs[1] for kv in ladder[0]["set"]) and "parallel_requests=1" not in runs[1]
    assert all(kv in runs[2] for kv in ladder[1]["set"])
    assert st["phases"]["garak"]["status"] == "ok"
    kept = C.load_repairs()["garak"]
    assert kept == list({kv.split("=")[0]: kv for kv in [*ladder[0]["set"], *ladder[1]["set"]]}.values())
    assert sum(m.startswith("🔧 Step failed, trying again") for m in sent) == 2 and any(m.startswith("✅ Step works now: Jailbreak scanner") for m in sent)
    st2 = {"phases": {}}                                  # next run (lanes A and B) starts with the kept settings
    C.run_phases(plan, {"workers": 1, "set": []}, st2, [], CFG)
    assert st2["phases"]["garak"]["status"] == "ok" and st2["phases"]["garak"]["retries"] == 0


def test_exclusive_phase_runs_alone(sandbox, tmp_path):
    """An exclusive phase never overlaps another phase (it needs the GPU alone)."""
    log = tmp_path / "events"

    def job(name):
        return [sys.executable, "-c", f"import time; open({str(log)!r},'a').write('{name}+\\n'); time.sleep(0.3); "
                                      f"open({str(log)!r},'a').write('{name}-\\n')"]
    plan = [C.Phase("autotune", "", "", job("autotune"), [], "document", 1, 1, exclusive=True),
            C.Phase("detect", "", "", job("detect"), [], "guard", 1, 1),
            C.Phase("ablation_none", "", "", job("ablation_none"), [], "agent", 1, 1)]
    st = {"phases": {}}
    C.run_phases(plan, {"workers": 3, "set": []}, st, [], CFG)
    ev = log.read_text().split()
    assert ev[:2] == ["autotune+", "autotune-"]           # nothing started while it ran
    assert all(v["status"] == "ok" for v in st["phases"].values())


def test_lanes_split_phases_and_read_cross_lane_dependencies(sandbox, monkeypatch):
    ok = [sys.executable, "-c", "pass"]
    lanes = {"A": ["stage1_fit"], "B": ["detect", "stage1_altmodel"]}
    cfg = load_config(None, ["campaign.max_retries=0"])
    cfg.campaign["lanes"] = lanes
    plan = [C.Phase("stage1_fit", "", "", ok, [], "document", 1, 1),
            C.Phase("detect", "", "", ok, [], "guard", 1, 1),
            C.Phase("stage1_altmodel", "", "", ok, ["stage1_fit"], "document", 1, 1)]
    monkeypatch.setitem(C.LANE, "name", "B")
    stB = {"phases": {}}
    # lane A has not run: B does its own phase and leaves the cross-lane dependent pending (stops via STOP)
    import threading
    timer = threading.Timer(3.0, lambda: C.STOP.__setitem__("flag", True))   # B would wait for A forever
    timer.start()
    C.run_phases(plan, {"workers": 2, "set": []}, stB, [], cfg)
    timer.cancel()
    C.STOP["flag"] = False
    assert stB["phases"]["detect"]["status"] == "ok"
    assert "stage1_fit" not in stB["phases"]                     # not B's phase
    assert stB["phases"]["stage1_altmodel"]["status"] != "ok"
    # lane A finishes stage1_fit → B can run the dependent phase
    monkeypatch.setitem(C.LANE, "name", "A")
    stA = {"phases": {}}
    C.run_phases(plan, {"workers": 2, "set": []}, stA, [], cfg)
    C.save_state(stA)
    assert C.state_path("A").exists() and stA["phases"]["stage1_fit"]["status"] == "ok"
    monkeypatch.setitem(C.LANE, "name", "B")
    C.run_phases(plan, {"workers": 2, "set": []}, stB, [], cfg)
    assert stB["phases"]["stage1_altmodel"]["status"] == "ok"
    merged = C.merged_state(stB, cfg)
    assert {k: v["status"] for k, v in merged["phases"].items()} == {
        "stage1_fit": "ok", "detect": "ok", "stage1_altmodel": "ok"}


def test_milestones_notify_once(sandbox, monkeypatch):
    sent = []
    monkeypatch.setattr(C, "notify", sent.append)
    st = {"phases": {"detect": {"status": "ok"}, "pyrit": {"status": "ok"}, "garak": {"status": "failed",
                                                                                           "retries": 2}}}
    C.notify_progress(st, "detect", CFG)
    C.notify_progress(st, "pyrit", CFG)
    C.notify_progress(st, "garak", CFG)
    groups = [m for m in sent if m.startswith("✅ Finished: guards and jailbreak")]
    assert len(groups) == 1 and any(m.startswith("❌ Step failed: Jailbreak scanner") for m in sent)
    C.notify_progress(st, "detect", CFG)
    assert len([m for m in sent if m.startswith("✅ Finished: guards and jailbreak")]) == 1
