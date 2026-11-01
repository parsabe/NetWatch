# RQ2 · VectorDB Flame: secure vector RAG and retrieval integrity

> How can Retrieval-Augmented Generation (RAG) vector pipelines be shielded against corpus poisoning, indirect
> instruction delivery and unauthorized data exfiltration?

Planned mechanism (from the proposal): Ed25519 attestation of every indexed chunk and its embedding (unsigned chunks
are dropped at retrieval), zero-trust attribute-based access control, outlier detection in the embedding space,
context sanitisation, and cryptographic honeytokens HMAC_K(chunk_id ∥ salt) that reveal exfiltration.

Work for RQ2 lives on the `RQ2-VectorDB-Flame` branch only and is never merged into another branch.
Status: not started. Planned layout, following RQ1 (`3 - implementation/Prompt_injection/` on
`RQ1-Prompt-Injection`): code package, `config/`, `eval/` runners, `tests/`, `cluster/` job scripts, and a report
with tables and charts.
