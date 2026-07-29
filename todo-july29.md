# July 29 implementation plan

Finish the HTCondor measurement and profile contracts without expanding the preflight scope.

## Milestone A — HTCondor measurement and profile shape

1. **Standalone HTCondor login measurement — Completed**
   - Produce one standard-library JSON collector that can run directly on an HTCondor login node.
   - Collect common site facts, generic non-temporary storage locations, scheduler commands, pool
     totals, CPU groups, and GPU groups.
   - Use one bounded `condor_status` ClassAd query and retain at most three example machines per
     group.

2. **Live measurement integration — Completed**
   - Use the same measurement shape for `login-measurement build` and live `profile build`.
   - Keep Slurm and HTCondor detection independent; collect both when both supported schedulers are
     visible.
   - Preserve partial results when an optional command or ClassAd value is unavailable.

3. **Site-profile schema revision — Completed**
   - Move scheduler version into the selected scheduler section while keeping `scheduler_type` at
     the top level.
   - Use Slurm options and HTCondor submit attributes, with syntax-validated unmapped candidates.
   - Remove non-general fields, software, and inline field evidence; use a compact section-status
     map and an evidence ID included in the evidence filename.
   - Add Slurm `maximum_nodes_per_job` and `shared_nodes`.

**Test:** Build and validate one simulated Slurm profile and one simulated HTCondor profile.

## Milestone B — Notre Dame live profile

4. **Review real Notre Dame measurement — Incomplete**
   - Capture real output, compare it with the contract, and add only generally useful missing facts.

5. **Enable live HTCondor profile construction — Incomplete**
   - Verify scheduler-scoped discovery, retrieval, typed extraction, and reconciliation.
   - Implement only gaps that prevent a partial evidence-backed HTCondor profile.

6. **Test and improve HTCondor profile quality — Incomplete**
   - Run the three retrieval modes with a live model and compare correctness, coverage, and cost.

**Test:** Construct a partial Notre Dame HTCondor profile with valid citations and no UGE leakage.

## Milestone C — Notre Dame preliminary evaluation

7. **Create Notre Dame ground truth — Incomplete**
   - Generate the evaluation CSV and prefill measurement-derived fields.

**Test:** Compare the generated profile with the reviewed CSV using case-insensitive text matching.

## Milestone D — Slurm regression

8. **Apply shared changes to Slurm and Anvil — Incomplete**
   - Update Anvil fixtures, extraction, evidence, and profile output for the revised contracts.

9. **Test and iterate on Anvil — Incomplete**
   - Repeat the frozen-corpus evaluation and fix regressions before adding site-specific fields.

**Test:** Reproduce all Anvil retrieval modes and compare with the previous evaluation artifacts.

## Milestone E — Stampede3 portability check

10. **Validate a second Slurm site — Incomplete**
    - Use Stampede3 login measurements and pilot results without code changes.

**Test:** Build a valid partial Stampede3 profile through the same pipeline.
