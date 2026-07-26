# Implementation gaps

This document records proposed behavior that appears in design discussions or figures but is not
yet implemented. Revisit each gap through `MILESTONES.md` and promote it to `TODO.md` only when it
becomes immediate work.

## Topic-driven discovery follow-up

**Status:** Implemented

The model now selects fetched pages and decides whether another bounded search is useful. Discovery
defaults to two steps, accepts at most three follow-up queries per step, and passes every result
through the existing domain, fetch, and target-site scope checks. Model-generated URLs and commands
remain unavailable. `--max-discovery-steps 1` preserves the original one-step behavior.

The offline acceptance test exposes a policy page only to the follow-up query and verifies that the
second model decision can select it.

## Planning references

- `MILESTONES.md` is the authoritative implementation sequence.
- `TODO.md` is the prioritized immediate improvement queue.
- This file holds deferred differences between the current implementation and proposed design.
