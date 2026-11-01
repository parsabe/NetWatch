I read the whole RQ1 section (Stage 1, Stage 2, the final Horizon formula) and the existing code in `3 - implementation/Prompt_Injection/`. Most of Stage 1 is already there. The Stage 1 minimax solver and all of Stage 2 are still missing. Below is a guide in the order I'd build it, with the datasets to use and the places where the math changes what the code has to do.

---

## 0. What exists and what's missing

| Component in the method                                                                                                         | Status                               |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Spotlighting variants + key sizes                                                                                               | K                                    |
| Canary-based A(δ,κ), clean utility U_τ ([experiment.py](3%20-%20implementation/Prompt_Injection/experiment.py))                 | ✅ done                              |
| Clopper–Pearson, DRO g*ρ, combined risk R̄*τ, the τ\* choice rule ([stats.py](3%20-%20implementation/Prompt_Injection/stats.py)) | ✅ done                              |
| Adaptive attacker with budget B ([adaptive.py](3%20-%20implementation/Prompt_Injection/adaptive.py))                            | ✅ done (PAIR-style; GCG is missing) |
| **V₁(ρ,η) mixed strategy π\*** (needs a per-attack × per-variant matrix)                                                        | ❌                                   |
| **Stage 2 entirely**: P-LLM, interpreter, Q-LLM pool, voting, typed values, capabilities, Z3 policy, CRC, V_Horizon             | ❌                                   |
| Real data (only 3 toy tasks so far)                                                                                             | ❌                                   |

---

## 1. Data to download

Use **different sources for different roles**. That separation is what lets you measure ρ and keep the reported bounds honest.

**Stage 1 (single LLM call, clean utility plus attacks):**

- **BIPIA** (Microsoft, github `microsoft/BIPIA`): indirect injection embedded in email, table, code, and QA tasks, with reference answers. This should be your main source for `tasks.jsonl` (document, task, y\*) and for U_τ.
- **Open-Prompt-Injection** (Liu et al., USENIX Sec '24, github `liu00222/Open-Prompt-Injection`): a clean attack taxonomy (naive, escape-character, context-ignoring, fake-completion, combined). Use these as your **category labels** for P.
- **LLMail-Inject** (Microsoft SaTML 2025 challenge, on HuggingFace): real adaptive human attacks written _against spotlighting-style defenses_. This is the best fit for your threat model. Use it as the **shifted distribution Q** to measure a realistic ρ, and as a second adaptive set.
- **HackAPrompt** and **Tensor Trust** (both human-written attacks): more distribution-shift sources. Hold out at least one entire source as Q, never used for tuning.

**Stage 2 (agent with tools):**

- **AgentDojo** (`pip install agentdojo`, ETH SPY Lab): **this is the benchmark you need**. CaMeL was evaluated on it, so you get a direct baseline comparison. It provides workspace, Slack, travel, and banking suites with tools, user tasks, injection tasks, and automatic utility and security checks.
- **InjecAgent** (UIUC): a second tool-agent IPI benchmark, useful for generalization.

Check the exact repo and HF paths when you download. Store every dataset raw and read-only in its own folder, and write converter scripts that map each one into your common `{id, task, document, reference, category, source}` format.

**Splits (important):** split everything into **dev / calibration / test**.

- Choose π\*, τ\*, m, and the policy on **dev**.
- Calibrate λ̂ (CRC) on **calibration**.
- Compute every Clopper–Pearson bound you report on **test only**.

If you select π\* on the same data you compute the bound on, the bound is optimistically biased, and a reviewer will catch it.

---

## 2. Models and infrastructure

- **Target and quarantined models:** 2–3 open-weight models (e.g., Llama-3.1-8B, Qwen2.5-7B, Mistral) plus one API model as a reference point. Pin the version, use temperature 0, and log both.
- **Serve them with vLLM, not Ollama**, for the big runs. Stage 2 multiplies every extraction by m (5–15), and AgentDojo has hundreds of security cases, which comes to tens of thousands of calls. vLLM batching is 10–20× faster.
- **Attacker model:** a separate, stronger model, so the attacker isn't the defender.
- **Cache every call** (prompt hash → output) on disk. Reruns and ablations then cost almost nothing, and the cache doubles as your audit log.
- Keep the existing pattern of appending JSONL line by line so runs can resume.

**Sample-size planning** (from your own Bound 2, with s = 0 and α = 0.05):

| Target claim p̄ ≤ | n needed |
| ---------------- | -------- |
| 5%               | ~60      |
| 1%               | ~300     |
| 0.5%             | ~600     |
| 0.1%             | ~3000    |

Decide which claim you want to make before you run, and size n from this table.

---

## 3. Stage 1: what to add

### 3a. Collect a paired matrix, not per-defense averages

The variance term in V₁ (Var*P(L*π)) and your claim that variants fail on disjoint subsets both require running **every variant τ on the same attack x**. Store:

```
A[x, τ] ∈ {0,1}   (or the success rate over several keys per (x,τ))
```

Use the same attack IDs across all variants. This is the key data structure for the rest of Stage 1.

### 3b. Solve V₁ as a convex program

You don't need a game solver. With the matrix A:

- E*P[L*π] = πᵀ μ (linear)
- Var*P(L*π) = πᵀ Σ π, where Σ is the covariance of A's columns. sqrt(πᵀΣπ) is a norm, so it's convex.
- The forging term Σ π*τ q/|K*τ| is linear.
- The utility constraint Σ π*τ U*τ ≥ U_none − η is linear.
- The max of convex functions is convex.

So you get **minimize a convex function over a simplex with one linear constraint**: about 10 lines of `cvxpy` or `scipy.optimize`. Sion's theorem justifies the min-max swap in the thesis, but the solver doesn't need it. Report π\*, V₁, and the worst-case reweighting w\* (from the DRO dual, it's proportional to 1 + c(L − E[L])).

### 3c. Points to state in the thesis

- **E and Var in V₁ are empirical, not Clopper–Pearson bounded**, so V₁ has no finite-sample guarantee as written. Fix it either by using a variance-sensitive concentration bound (e.g., empirical Bernstein) or by stating it as a plug-in estimate.
- Fixed transforms (|K| = 1) with q ≥ 1 give R̄ = 1 automatically. As the README already says, also report the q = 0 view.
- The ρ validity condition ρ ≤ (1−p̄)/(2p̄) holds for small p̄ but should be checked in code.
- **Measure ρ** between the P categories and the LLMail-Inject/HackAPrompt categories (`rho_between_mixes` already does this). Don't just pick ρ = 0.1.

### 3d. Adaptive attacker

PAIR-style is already done. Add **GCG** only if you use an open-weight target, since it needs gradients (e.g., via the `nanogcg` package). For each attack x, run the optimized δ\* against **fresh keys** to get p̄^adp.

---

## 4. Stage 2: build order

Build it as a small Python library, testing each layer on its own before connecting it to AgentDojo.

**Step 1: Typed values and capabilities (start here, it's pure software).**
An immutable `Value(data, type, cap)` class with cap ∈ {USER, SYSTEM, UNTRUSTED}. **Taint propagation:** any value computed from an UNTRUSTED input is UNTRUSTED (cap = the max of its inputs). Unit-test it thoroughly.

**Step 2: Parsers / grammar.**
One strict parser per type (ISO date, email, amount, enum, bool, IBAN, …). Use pydantic or regex plus a semantic check. Anything that fails to parse is rejected, and the rejection counts as F = 0 (the attack was blocked).

**Step 3: Q-LLM pool + voting.**

- For each extraction, sample τ ~ π\*, draw m independent keys, transform, and query m times.
- **Canonicalize before voting** (normalize dates, case, whitespace). Without this, honest runs disagree and everything escalates.
- ⚠️ **Voting only works for typed, finite outputs.** For free-text extraction (summaries) you need a different approach, or the P-LLM program should avoid it. Say this explicitly as a scope limit.

**Step 4: P-LLM + interpreter.**

- Either reuse CaMeL's open-source code (`google-research/camel-prompt-injection`) or write a restricted interpreter for a small Python subset using `ast` with an allowlisted node set.
- ⚠️ **Control-flow taint:** if the program branches on an UNTRUSTED value (`if amount > 100: send(...)`), untrusted data has chosen which tool runs. Your noninterference statement only covers _arguments_. Fix: taint the program counter, so tool calls inside a branch on UNTRUSTED inherit the taint. CaMeL handles this with its dependency graph.

**Step 5: Policy + Z3.**
Write policies as data (rules per tool), then encode them in Z3 and check at startup:

- **Noninterference:** for each critical tool, assert "∃ arg with cap = UNTRUSTED ∧ Policy = ALLOW". The expected result is **UNSAT**.
- **Consistency:** no input gets both ALLOW and DENY, so UNSAT is expected.
- **Completeness:** every input gets some decision, checked by asserting the negation and expecting UNSAT.

Put these as a proof table in the thesis. The solver checks the _policy_, not the LLM, so the guarantee is deterministic.

**Step 6: CRC for CONFIRM.**

- Define a score s(x) per CONFIRM event, e.g., 1 − vote agreement, a tool risk level, or anomaly signals.
- Escalate when s ≥ λ. The loss L_i(λ) = 1[attack i is not escalated and causes harm] is monotone in λ, which CRC requires.
- λ̂ = the largest λ with (n/(n+1))·R̂_n(λ) + 1/(n+1) ≤ α_user.
- With α_user = 1% you need **n ≥ 99 calibration points just for feasibility**; plan for 500 or more.

**Step 7: Estimate a and h for the final formula.**

- **a (wrong-approval rate):** needs humans. Run a small user study (even 10–20 people approving or rejecting CONFIRM dialogs), or use an LLM-simulated approver and label it clearly as a proxy.
- **h_S,θ:** run the corrupted values through the real tool in AgentDojo's sandbox and count how often harm happens.

**Step 8: QIF leakage B.**
Easy and rigorous thanks to the parsers: min-entropy leakage ≤ log₂ |output space of the type|. A bool leaks 1 bit, an enum of k values leaks log₂ k, a date leaks about 15 bits. Compute it per extraction and sum along the program.

---

## 5. Two places where the method needs a fix before you code

1. **Hoeffding independence (Theorem 1).** The keys are independent, but the _same attack x on the same model_ makes the m failures correlated. The marginal rate R̄_Q = 0.15 can average over attacks where p(x) = 0.9, and for those attacks voting makes things worse. The theorem holds **per attack, given x**. In code:
   - estimate p(x) over many keys for each attack;
   - report the share of attacks with p(x) ≥ ½ (voting can't save these);
   - plot **empirical P(vote fooled) vs. the Hoeffding curve** against m. That plot is a strong figure for the thesis.

2. **L_π in the final V_Horizon formula.** As written, L*π(x) is built from Clopper–Pearson constants that don't depend on x. That makes Var_P(L*π) = 0, so the DRO term disappears. For implementation, use the per-attack loss ℓ_θ(x) = G + aC + hF inside E and Var, and apply the CP bounds to the aggregate rates separately. It's worth correcting the .tex too.

---

## 6. Experiments to run (maps to the RQ1 claims)

1. **Stage 1 table:** ASR, p̄, g*ρ, p̄^adp, R̄*τ, U_τ per variant; π\* and V₁ against the best single τ\* (this tests the claim that mixing beats the best single variant).
2. **ρ sweep:** V₁ and R̄ as ρ grows, with the measured ρ marked.
3. **Hoeffding validation:** empirical vs. theoretical as m = 1, 3, 5, 9, 15 (plus call cost).
4. **Stage 2 ablation on AgentDojo:** no defense → spotlighting only → CaMeL baseline → +voting → +types → +policy → full Horizon. Report utility, targeted ASR, and confirmation rate for each.
5. **Baselines:** no defense, Hines static spotlighting, original CaMeL, a detector (e.g., PromptGuard), and if possible a fine-tuned defense such as StruQ or SecAlign.
6. **Adaptive attacks against the full Horizon pipeline**, not just Stage 1.
7. **Cost:** latency and tokens per task for each m. Reviewers will ask about this.

---

## 7. Suggested timeline

1. **Week 1:** dataset converters + splits, vLLM up, call cache.
2. **Week 2:** full Stage 1 matrix runs, then the π\* solver.
3. **Weeks 3–4:** Stage 2 Steps 1–3 (values, parsers, voting) with unit tests, then the Hoeffding experiment.
4. **Weeks 5–6:** interpreter + policy + Z3, integrated with AgentDojo.
5. **Week 7:** CRC calibration, a/h estimation, QIF, V_Horizon.
6. **Week 8:** ablations, baselines, adaptive attacks against the full system, figures.

I'd start with the paired A[x, τ] matrix on BIPIA, since π\*, V₁, and the variance claims all depend on it. I can also turn this into a shareable page if you want to bring it to your supervisor meeting.
