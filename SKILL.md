# HPC Site Preflight — LLM Quick Context

Read `AGENTS.md` before modifying this repository. It is the authoritative guide.

This project constructs evidence-backed HPC site profiles from existing profiles, login measurements, official documentation, and approved pilot results. It then performs deterministic preflight checks for portable workflow packages.

Key boundaries:

- AI assists documentation discovery and extraction only.
- Measurements, probes, validation, reconciliation, and final planning are deterministic.
- Live and replay modes must share the same interfaces.
- One `RunTracker` records time, tokens, retries, tool calls, artifacts, and failures by stage.
- Unimplemented features must fail explicitly; never fabricate output.
- Follow one milestone at a time from `MILESTONES.md`.
