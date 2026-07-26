# Development figures

These Graphviz sources track the high-level project architecture as it evolves. Detailed stages,
schemas, and validation rules belong in the surrounding prose documentation.

## Figure inventory

| Source | Purpose | Scope |
| --- | --- | --- |
| `problem.dot` | Explain why scheduler observations alone are insufficient | Paper motivation |
| `construct.dot` | Show measured, documented, and pilot evidence becoming a site profile | Paper construction figure |
| `pipeline.dot` | Show the complete package-to-preflight vision | Current construction plus planned preflight |
| `proposed-pipeline.dot` | Trace the complete proposed evidence and preflight pipeline | Full development roadmap |
| `discovery-agent-loop.svg` | Show the bounded search, fetch, selection, and follow-up loops | Documentation agent |
| `discovery-agent-loop-paper.svg` | Summarize the bounded discovery loop with minimal detail | Paper figure |

## Visual conventions

- Solid nodes are implemented concepts; dashed nodes are planned work.
- Purple marks bounded AI, teal marks deterministic work, and red marks failure.
- Labels carry the meaning so color is not the only indicator.

`problem.dot` is conceptual and therefore does not use implementation-status styling.

## Maintenance rules

When the implementation changes:

1. Compare the figure with `MILESTONES.md`, `docs/ARCHITECTURE.md`, and `docs/PIPELINE.md`.
2. Use **site profile** for the machine-readable output; reserve **policy** for documented rules.
3. Distinguish observed configuration from documented or enforced policy.
4. Mark a node solid only when that path is implemented and tested end to end.
5. Do not add timings, limits, or outcomes unless the paper or repository evidence supports them.
6. Keep each figure to roughly four to seven nodes and move implementation details into prose.
7. Update the review-date comment at the top of every changed `.dot` file.
8. Keep Graphviz source as the maintained form; generate SVG or PDF only when needed by the paper.

## Render and validate

With Graphviz installed, render each source to a temporary file:

```bash
dot -Tsvg docs/figures/problem.dot -o /tmp/hpc-preflight-problem.svg
dot -Tsvg docs/figures/construct.dot -o /tmp/hpc-preflight-construct.svg
dot -Tsvg docs/figures/pipeline.dot -o /tmp/hpc-preflight-pipeline.svg
dot -Tsvg docs/figures/proposed-pipeline.dot -o /tmp/hpc-preflight-proposed-pipeline.svg
```

Graphviz parsing during rendering is the syntax validation step. Inspect the generated SVGs before
using them in the paper because a valid graph can still have an unclear layout.
