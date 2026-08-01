# RQ1/RQ2 108-run analysis

This analysis uses the complete balanced matrix of 108 successful profile
builds: three sites, four models, three retrieval strategies, and three
repetitions. Four earlier failed attempts remain outside the matrix and are not
included.

## Inputs and traceability

- Run artifacts: `evaluation/profile-runs/`, one directory per successful run.
- Run metadata: `evaluation/profile-run-log.csv` and
  `evaluation/profile-matrix-manifest.json`.
- Performance tracking: `evaluation/performance-runs/profile-matrix/<run-id>/`.
- Ground truth: `evaluation/ground-truth/completed-*-site-profile-ground-truth.csv`.
- Field comparison: `evaluation/rq1/evaluate_profile.py` and
  `evaluation/rq1/run_rq1_analysis.py`.
- Editable analysis: `evaluation/notebooks/rq1-rq2-108-run-analysis.ipynb`.

The normalized dataset retains model, site, retrieval, repetition, corpus
fingerprint, provider-reported input/output/total tokens, model calls,
documentation-analysis latency, total profile latency, rejected and unresolved
findings, conflicts, and all RQ1 quality metrics. Citation checks use accepted
documentation findings and verify chunk identity, target-site scope, source
URL, and quote containment against each frozen corpus. Evidence reports provide
the measurement, documentation, and pilot source links used by the completion
waterfall.

## Table 1: Results by site (RQ1)

| Site | Precision | Recall | Abstention accuracy | Field accuracy | Citation integrity |
|---|---:|---:|---:|---:|---:|
| Anvil | 0.979 | 0.874 | 0.969 | 0.895 | 1.000 |
| Stampede3 | 0.957 | 0.904 | 0.934 | 0.910 | 1.000 |
| Notre Dame CRC | 0.992 | 0.771 | 0.986 | 0.783 | 1.000 |

The system is conservative: precision and abstention accuracy exceed recall at
all sites. Notre Dame CRC has the highest precision but lowest recall and field
accuracy, indicating that it leaves many expected values unresolved rather
than filling them incorrectly. Citation integrity is mechanically perfect for
all 5,592 accepted citations; this does not by itself establish semantic
entailment.

## RQ2 summary

Gemini 3.6 Flash has the highest model-level field accuracy (0.875). GPT-5.6
Terra is 0.009 lower (0.866) but uses about 33% fewer tokens and is about 2.9
times faster. GPT-5 mini is both the least accurate and most variable model in
this experiment.

BM25 has the highest retrieval-level accuracy (0.868). Full-corpus retrieval
uses 2.18 times as many tokens and 2.43 times as much extraction latency as
BM25, while its field accuracy is 0.007 lower. LLM-expanded BM25 has the largest
pooled repetition SD (0.028), compared with 0.021 for BM25 and 0.017 for full
corpus.

These results do not support complete quality equivalence across models: the
observed model-level range is 0.034 in field accuracy. Retrieval differences
are smaller (range 0.009), and the more expensive strategies do not improve
aggregate quality on these frozen corpora.

## Outputs

- Requested CSV and LaTeX tables are under `tables/`.
- Normalized runs and completion-path intermediates are also under `tables/`.
- Paper-ready PDF figures are under `figures/`.
- Figure dimensions, colors, labels, exports, and LaTeX table printing are
  editable in the executed notebook.
