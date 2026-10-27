"""Download all datasets into data/raw/ and write the data card (data/README.md + data/data_card.json).

Gated sources are skipped unless --include-gated is given (ask the author first).
Every download is pinned to the commit / revision it resolved to, which is recorded.

    uv run python scripts/download_data.py [--only bipia llmail_inject] [--include-gated]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from horizon.config import ROOT  # noqa: E402

RAW = ROOT / "data" / "raw"
CARD_JSON = ROOT / "data" / "data_card.json"

SOURCES = {
    # ---- Stage 1 -------------------------------------------------------------
    "bipia": dict(kind="git", url="https://github.com/microsoft/BIPIA.git", license="MIT",
                  role="Stage 1: clean documents with reference answers (email/table/code) + attack categories",
                  note="WebQA and Summarization subsets need separate source download (license); not used."),
    "llmail_inject": dict(kind="hf", repo="microsoft/llmail-inject-challenge", license="MIT",
                          files=["data/labelled_unique_submissions_phase2.json", "data/scenarios.json",
                                 "data/objectives_descriptions.json", "data/levels_descriptions.json",
                                 "data/system_prompt.json", "data/emails_for_fp_tests.json"],
                          role="Stage 1: human adaptive injections written against spotlighting-style defenses",
                          note="Only the labelled phase-2 submissions (≈69 MB); the 1.6 GB raw dumps are not fetched."),
    # ---- Direct / jailbreak layer: attacks -------------------------------------
    "harmbench": dict(kind="url", license="MIT",
                      url="https://raw.githubusercontent.com/centerforaisafety/HarmBench/main/data/behavior_datasets/harmbench_behaviors_text_all.csv",
                      repo_api="https://api.github.com/repos/centerforaisafety/HarmBench/commits/main",
                      role="Direct layer: harmful behaviors (attacks)",
                      note="Original CSV from the authors' GitHub (the walledai HF mirror is gated)."),
    "jbb_behaviors": dict(kind="hf", repo="JailbreakBench/JBB-Behaviors", license="MIT",
                          role="Direct layer: harmful (attacks) and benign behaviors (FPR)"),
    "deepset_prompt_injections": dict(kind="hf", repo="deepset/prompt-injections", license="Apache-2.0",
                                      role="Direct layer: prompt injections (labelled)"),
    "gandalf_ignore_instructions": dict(kind="hf", repo="Lakera/gandalf_ignore_instructions", license="MIT",
                                        role="Direct layer: human 'ignore instructions' attacks"),
    "hackaprompt": dict(kind="hf", repo="hackaprompt/hackaprompt-dataset", license="MIT", gated=True,
                        role="Direct layer: HackAPrompt competition submissions"),
    "tensor_trust": dict(kind="hf", repo="qxcv/tensor-trust", license="see source repo (not stated on HF card)",
                         role="Direct layer: Tensor Trust hijacking/extraction attacks"),
    # ---- Direct / jailbreak layer: benign (false-positive rate) ----------------
    "xstest": dict(kind="hf", repo="natolambert/xstest-v2-copy", license="CC-BY-4.0",
                   role="Direct layer: benign look-alike prompts (FPR)",
                   note="Public copy of XSTest v2 (the walledai mirror is gated)."),
    "alpaca": dict(kind="hf", repo="tatsu-lab/alpaca", license="CC-BY-NC-4.0",
                   role="Direct layer: ordinary benign instructions (FPR)"),
}


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and ".git" not in f.parts)


def fetch(name: str, spec: dict) -> dict:
    dest = RAW / name
    entry = {"name": name, "license": spec["license"], "role": spec["role"], "note": spec.get("note", ""),
             "download_date": dt.date.today().isoformat(), "local_path": str(dest.relative_to(ROOT))}
    if spec["kind"] == "git":
        if not dest.exists():
            subprocess.run(["git", "clone", "--depth", "1", "-q", spec["url"], str(dest)], check=True)
        rev = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        entry.update(source=spec["url"], version=rev)
    elif spec["kind"] == "hf":
        from huggingface_hub import HfApi, snapshot_download
        info = HfApi().dataset_info(spec["repo"])
        snapshot_download(spec["repo"], repo_type="dataset", revision=info.sha, local_dir=dest,
                          allow_patterns=spec.get("files"))
        entry.update(source=f"https://huggingface.co/datasets/{spec['repo']}", version=info.sha)
    elif spec["kind"] == "url":
        dest.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(spec["repo_api"]) as r:
            rev = json.load(r)["sha"]
        url = spec["url"].replace("/main/", f"/{rev}/")
        urllib.request.urlretrieve(url, dest / Path(url).name)
        entry.update(source=url, version=rev)
    entry["size_bytes"] = _dir_size(dest)
    entry["files"] = sorted(str(f.relative_to(dest)) for f in dest.rglob("*")
                            if f.is_file() and ".git" not in f.parts and ".cache" not in f.parts)[:50]
    return entry


def write_card(entries: dict) -> None:
    CARD_JSON.write_text(json.dumps(entries, indent=2))
    lines = ["# Data card", "",
             "Generated by `scripts/download_data.py`; splits by `scripts/make_splits.py`. Raw data is never committed "
             "(`data/raw/` is git-ignored).", "",
             "| Dataset | Role | Source | Version (commit/revision) | License | Size | Downloaded |",
             "|---|---|---|---|---|---|---|"]
    for e in entries.values():
        if e.get("skipped"):
            lines.append(f"| {e['name']} | {e['role']} | {e.get('source', '')} | — | {e['license']} | — | "
                         f"SKIPPED: {e['skipped']} |")
            continue
        lines.append(f"| {e['name']} | {e['role']} | {e['source']} | `{e['version'][:12]}` | {e['license']} | "
                     f"{e['size_bytes'] / 1e6:.1f} MB | {e['download_date']} |")
    lines += ["", "AgentDojo (Stage 2) ships its suites inside the `agentdojo` package; the version is pinned in "
              "`uv.lock` and the benchmark version in `config/params.yaml` (`stage2.benchmark_version`).", "",
              "## Notes"]
    lines += [f"- **{e['name']}**: {e['note']}" for e in entries.values() if e.get("note")]
    split_card = ROOT / "data" / "splits" / "splits.json"
    if split_card.exists():
        sp = json.loads(split_card.read_text())
        brief = {k: {kk: vv for kk, vv in v.items() if not kk.endswith("_ids")} for k, v in sp["splits"].items()}
        lines += ["", "## Splits", "", f"seed = {sp['seed']}, fit_fraction = {sp['fit_fraction']}; full id lists in "
                  "`data/splits/splits.json`.", "", "```json", json.dumps(brief, indent=1), "```"]
    (ROOT / "data" / "README.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--include-gated", action="store_true")
    args = ap.parse_args()
    entries = json.loads(CARD_JSON.read_text()) if CARD_JSON.exists() else {}
    for name, spec in SOURCES.items():
        if args.only and name not in args.only:
            continue
        if spec.get("gated") and not args.include_gated:
            entries[name] = {"name": name, "license": spec["license"], "role": spec["role"],
                             "source": f"https://huggingface.co/datasets/{spec.get('repo')}",
                             "skipped": "gated (accept terms on HF, then rerun with --include-gated)"}
            print(f"[skip] {name}: gated")
            continue
        print(f"[get ] {name}")
        try:
            entries[name] = fetch(name, spec)
        except Exception as e:
            entries[name] = {"name": name, "license": spec["license"], "role": spec["role"],
                             "skipped": f"download failed: {type(e).__name__}: {str(e)[:120]}"}
            print(f"[fail] {name}: {e}")
    write_card(entries)
    print(f"data card → {CARD_JSON.relative_to(ROOT)}, data/README.md")


if __name__ == "__main__":
    main()
