# Evaluation workspace

This directory separates frozen inputs, raw experiment outputs, and analysis.
The profile-build matrix is complete, but the 108-run analysis has not been run.

## Raw dataset

- `site-inputs/`: one frozen corpus, login measurement, and pilot result per site.
- `ground-truth/`: reviewed field-level ground-truth tables.
- `profile-runs/`: 108 valid profile builds.
- `failed-profile-runs/`: four retained failed attempts; these are not evaluation runs.
- `performance-runs/profile-matrix/`: tracker output for the 112 matrix attempts.
- `performance-runs/other/`: preserved tracker output from earlier development runs.
- `profile-run-log.csv`: authoritative ledger containing 108 completed rows and four failed-attempt rows.

The completed matrix contains three sites, four models, three retrieval modes,
and three repetitions of every configuration. Each site uses one frozen corpus
fingerprint across all 36 runs.

## Existing analysis material

`rq1/`, `rq2/`, `notebooks/`, and the combined reports contain the earlier
analysis. They have not been regenerated for the 108-run matrix. Run new
analysis only after the evaluation plan is finalized.

## Running or resuming profiles

`run_profile_matrix.py` skips completed configuration keys in
`profile-run-log.csv`. It supports filters for `--site`, `--model`, `--mode`,
and `--rep`.
