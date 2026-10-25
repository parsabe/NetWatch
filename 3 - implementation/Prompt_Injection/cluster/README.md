# Running the Horizon test campaign on a university GPU cluster

One command does everything: `./run_campaign.sh`. It starts the local model server, runs every experiment
in dependency order (several at a time), keeps going after time limits, and finishes with
`report/campaign/campaign_report.pdf`: what ran, in which order, how long each phase took, which phases failed
and why, and for every defence configuration how many attacks got through vs. were blocked (with 95 %
Clopper–Pearson intervals and example cases).

## 1 · Upload

Copy the `Prompt_Injection` folder **without** `.venv/`, `results/`, `report/`, `mlartifacts/`:

```bash
rsync -av --exclude .venv --exclude results --exclude report --exclude mlartifacts \
      "Prompt_Injection/" <user>@<cluster>:~/horizon/
```

`data/raw/` holds the downloaded datasets (incl. HackAPrompt); copying it saves the downloads.
`third_party/camel-prompt-injection` is the pinned CaMeL code.

## 2 · Prepare once (login node, needs internet)

```bash
cd ~/horizon
./run_campaign.sh --prepare
```

Installs uv + Python 3.11 + the locked packages (incl. PyRIT, garak), Ollama, pulls Qwen3-8B, downloads the
two guard models (DeBERTa injection classifier, Qwen3Guard) and any missing data, then prints the plan.
HackAPrompt is gated: run `hf auth login` first, or copy `data/raw/hackaprompt/` from the laptop.
If your home quota is small, set `HF_HOME` and `OLLAMA_MODELS` to a scratch folder before `--prepare` and
in the job script.

## 3 · Run

```bash
./run_campaign.sh --plan                       # check what will run (no GPU needed)
qsub cluster/horizon_campaign.pbs              # PBS Pro (TU Freiberg); check the queue name (-q) at the top
qstat -u $USER                                 # is it running?
# (SLURM clusters: sbatch cluster/horizon_campaign.sbatch)
```

No SLURM? On any GPU machine: `./run_campaign.sh --profile cluster` (in `tmux` / `screen`).

The job gets a warning 15 min before its time limit, pauses cleanly and submits the next part itself.
Every runner is resumable, so nothing finished is repeated. To watch progress:

```bash
tail -f results/campaign/ledger.jsonl          # start / finish of every phase
tail -f results/campaign/logs/<phase>.log      # one phase in detail
```

## 4 · Bring the results home

```bash
rsync -av <user>@<cluster>:~/horizon/results/ results/
rsync -av <user>@<cluster>:~/horizon/report/campaign/ report/campaign/
./horizon.sh report        # on the laptop: the colibri model writes the full analysed report (incl. the campaign)
```

## Sizes (config `campaign.profiles` in `config/params.yaml`)

| profile | tests | meant for |
|---|---|---|
| `smoke` | ≈ 400 | check that every phase runs (≈ 3 h on the laptop) |
| `laptop` | ≈ 16 000 | the RTX 4060 laptop (≈ 2 days) |
| `cluster` | ≈ 57 000 (+ garak) | everything at full size; 4 phases side by side |

`./run_campaign.sh --plan --profile <name>` prints the exact numbers and a time estimate from the speed
measured on the laptop. If fewer than `campaign.min_tests` (5 000) tests finish, extra XPIA rounds (same
attacks, fresh secret keys) run until the target is reached; the report says so.

The cluster profile assumes a large GPU (`parallel_requests: 8`). On a smaller GPU lower it, e.g.
`./run_campaign.sh --set models.parallel_requests=4 --workers 2`.
