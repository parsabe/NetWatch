# KS-RB: Keyed Spotlighting with Robust Risk Bounds — experiment code

## Files

| File | What it does | Slide step |
|---|---|---|
| `transforms.py` | All spotlighting variants + key-space sizes \|K\| | Defense, Step 1 |
| `data.py` | Builds attack samples with a random canary per sample, grouped by attack category | Test corpus |
| `llm.py` | One `chat()` function for any OpenAI-compatible endpoint (Ollama, vLLM, OpenAI…) | — |
| `experiment.py` | Static run: ASR on attacks + utility on clean docs, per defense | Step 2a, utility |
| `adaptive.py` | Attacker-LLM loop with budget B, fresh key every try | Step 3 |
| `stats.py` | Clopper–Pearson, DRO closed form, ρ between two mixes, combined risk, choice rule | Steps 1–5 |
| `analyze.py` | Summary table, ASR per category, DRO curves, risk-vs-utility plot | Results |

## Setup

```bash
pip install -r requirements.txt
# local model via Ollama (free, runs on a laptop/GPU):
ollama pull llama3.1:8b
export LLM_BASE_URL=http://localhost:11434/v1 LLM_API_KEY=ollama LLM_MODEL=llama3.1:8b
# optional: a stronger attacker model for adaptive.py
export ATTACKER_MODEL=qwen2.5:14b
```

## Run

```bash
python stats.py                     # sanity check: reproduces the slide numbers
python experiment.py --tasks data/tasks.jsonl --n-attacks 1000 --out results/static.jsonl
for t in none datamark datamark_dyn substitution base64; do
  python adaptive.py --tasks data/tasks.jsonl --transform $t --targets 100 --budget 10 \
      --out results/adaptive_$t.jsonl
done
python analyze.py --static results/static.jsonl --adaptive-dir results --rho 0.1 --eta 0.05
```

Runs append line by line, so an interrupted run resumes where it stopped.

## Data format

`data/tasks.jsonl`, one clean task per line:

```json
{"id": "t0", "task": "Summarize the email.", "document": "...", "reference": "expected answer"}
```

`data/tasks_example.jsonl` has 3 toy examples. For the thesis, fill it with a few
hundred real tasks (see datasets in the chat answer) and convert their attacks into
`TEMPLATES` in `data.py`, keeping the attack **category** labels.

## Notes

- **Success check:** the injection asks the model to print a random canary
  (`CANARY-3F494E`); success = the canary appears in the output. Automatic, no judge needed.
- **Forging term:** with `--attempts 1000`, fixed transforms (|K| = 1) get risk 1 by
  construction — an attacker who knows the scheme can always forge them. Also report
  `--attempts 0` to see the "model obeys anyway" part on its own.
- **Measuring a realistic ρ:** label attacks in two datasets with the same category
  names, then `stats.rho_between_mixes(test_labels, other_labels)`. Categories that
  appear only in the other dataset give ρ = ∞ — a sign your test set is missing an
  attack type.
- **Utility:** `token_f1` is a simple baseline; swap in ROUGE-L or an LLM judge.
- Fix the model version and temperature 0, and log both in the thesis.
