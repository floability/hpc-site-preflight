# Architecture

HPC Site Preflight separates evidence acquisition from trusted decisions.

```text
Existing profile ─┐
Login measurement ├─> SiteEvidenceBundle ─> Validator ─> Reconciler ─> SiteProfile
Documentation AI ─┤                                               │
Approved pilots ──┘                                               v
Backpack requirements ─────────────────────────────────────> PreflightPlanner
                                                               │
                                             execution plan or early failure
```

## Components

- `site_info`: target-site identity and documentation scope.
- `profiles`: lookup, storage, freshness, and compilation.
- `measurements`: simulated and measured login-node evidence.
- `documentation`: bounded discovery agent, its tools, and the extraction pipeline.
- `probes`: simulated and measured predefined pilot results.
- `evidence`: normalized evidence, conflicts, and reconciliation.
- `backpack`: portable workflow requirement loading.
- `preflight`: deterministic compatibility checks and remediation.
- `reporting`: trace and performance artifacts.

The Phase D documentation modules and their execution order are described in
[DOCUMENTATION_WORKFLOW.md](DOCUMENTATION_WORKFLOW.md).

## Dependency direction

Low-level evidence providers must not import the planner. The planner consumes only normalized profile and backpack models. Provider-specific SDK types must not escape their adapter modules.
