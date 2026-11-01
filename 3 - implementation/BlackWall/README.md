# RQ3 · BlackWall: autonomous oversight and anti-rogue containment

> How can an autonomous cognitive supervisor continuously predict, detect and mathematically constrain rogue agent
> trajectories (goal drift, deceptive alignment, harmful actions) using parametric knowledge and hardware-rooted
> cryptographic guarantees?

Planned mechanism (from the proposal): probe the agent's activations into a latent manifold, keep it inside a safe set
S = {z | h(z) ≥ 0} with a control barrier function (intervene when ḣ(z) < −γ·h(z)), and, if steering fails, an
authenticated shutdown through cgroups and key revocation in a TEE.

Work for RQ3 lives on the `RQ3-BlackWall` branch only and is never merged into another branch.
Status: not started. Planned layout, following RQ1 (`3 - implementation/Prompt_injection/` on
`RQ1-Prompt-Injection`): code package, `config/`, `eval/` runners, `tests/`, `cluster/` job scripts, and a report
with tables and charts.
