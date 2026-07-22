# Ideal Anvil site-profile example

`site-profile-ideal.json` is a design target for an actionable, evidence-backed profile. It is not
an output from a real Anvil login session and deliberately does not validate against the current
`0.1` schema. The `0.2-draft` label records fields the current contract still needs, including
per-partition job limits and resources, storage quotas and retention conditions, and explicit
login-measurement work items.

Documented values were reviewed against official Anvil pages on 2026-07-23:

- [Job submission and partition policy](https://docs.rcac.purdue.edu/userguides/anvil/jobs/)
- [System architecture](https://docs.rcac.purdue.edu/userguides/anvil/architecture/)
- [File management](https://docs.rcac.purdue.edu/userguides/anvil/file_management/)
- [Scratch purge policy](https://docs.rcac.purdue.edu/userguides/anvil/policies/)
- [Software](https://docs.rcac.purdue.edu/userguides/anvil/anvil-software/)
- [Anvil FAQ](https://docs.rcac.purdue.edu/userguides/anvil/faqs/)

Current scheduler state, user accounts, and compute-node behavior remain empty. An ideal profile is
honest about unavailable evidence; it does not fill every field by guessing.
