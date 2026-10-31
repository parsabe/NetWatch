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

## 3b · Smoke test first, two GPUs, Telegram

```bash
qsub cluster/horizon_smoke.pbs                                   # whole machinery at tiny size in ~/0_smoke (1–3 h)
qsub -W depend=afterok:<smoke-id> cluster/horizon_campaign.pbs   # real run starts only if the smoke test passed
qsub -v LANE=A cluster/horizon_campaign.pbs                      # two GPUs: lane A = Horizon chain …
qsub -v LANE=B cluster/horizon_campaign.pbs                      # … lane B = guards, PyRIT, garak, baselines, 2nd model
```

Self-repair: a failed phase is retried up to `campaign.max_retries` times, and retry k adds steps 1..k of
`campaign.repair_ladder` (longer timeouts, guards on the CPU, then one request at a time with the phase running
alone). Settings that made a phase pass are kept in `config/repairs.yaml`; after a passed smoke test the file is copied
home, so lanes A and B start with them. Each retry and each kept repair is a Telegram message (BEFORE/AFTER/WHY/TESTED).
Delete `config/repairs.yaml` to undo. Code bugs cannot be repaired this way: the smoke test then fails and the lanes
are cancelled.

Lanes (`campaign.lanes`) keep separate state files (`results/campaign/state-A.json`, `state-B.json`); a phase that
needs another lane's phase waits for it. Do not run a lane job next to a single (no-lane) job.

Telegram milestones (start/resume, autotune, π*, λ̂, finished groups, failures after retries, pause, finish): put
`TELEGRAM_BOT_TOKEN=…` in `~/.config/horizon/telegram.env` (chmod 600), send the bot one message, then
`uv run python -m horizon.notify --check`. Never commit the token.

## 4 · Bring the results home

```bash
rsync -av <user>@<cluster>:~/horizon/results/ results/
rsync -av <user>@<cluster>:~/horizon/report/campaign/ report/campaign/
./horizon.sh report        # on the laptop: the colibri model writes the full analysed report (incl. the campaign)
```

## Sizes (config `campaign.profiles` in `config/params.yaml`)

| profile | tests | meant for |
|---|---|---|
| `bounded` (default) | 2 702 certain, ≤ 3 038 (hard range 2 000–4 000) | the real run: every phase explicitly sized, the campaign refuses to start outside the range |
| `smoke` | ≈ 400 | check that every phase runs (≈ 3 h on the laptop) |
| `laptop` | ≈ 16 000 | the RTX 4060 laptop (≈ 2 days) |
| `cluster` | ≈ 57 000 (+ garak) | everything at full size; 4 phases side by side |

`./run_campaign.sh --plan --profile <name>` prints the exact numbers and a time estimate from the speed
measured on the laptop. If fewer than the profile's `min_tests` finish, extra XPIA rounds (same attacks, fresh
secret keys) run until the target is reached, never above `max_tests`; the report says so.

The cluster profile assumes a large GPU (`parallel_requests: 8`). On a smaller GPU lower it, e.g.
`./run_campaign.sh --set models.parallel_requests=4 --workers 2`.
