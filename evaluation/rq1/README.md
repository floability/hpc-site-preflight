# RQ1 evaluation

This directory compares the 60 completed profiles with the three reviewed
ground-truth tables.

## Pretest

`pretest/random-selection.csv` records one profile selected per site with random
seed `20260730`. The field-level comparisons verify named-array matching for
Slurm options, partitions, storage, and HTCondor submit attributes. The
HTCondor adapter also derives flat resource summaries from order-independent
CPU and GPU groups.

## Outcomes

- `correct_value`: a populated value matches ground truth.
- `correct_abstention`: the profile is empty where ground truth is absent.
- `incorrect_value`: the profile populated the field with the wrong value.
- `false_abstention`: the profile is empty where ground truth has a value.
- `unsupported_value`: the profile populated a field ground truth marks absent.

Precision is correct populated values divided by all populated values. Recall is
correct populated values divided by all ground-truth values. Abstention accuracy
is correct abstentions divided by all ground-truth absent fields.
The measured walltime sentinel `-1` means unlimited and is a populated value,
not an abstention.

## Matching

- `pattern_any` accepts any reviewed scheduler-syntax alternative and treats
  placeholders as values.
- `required_subset` requires every ground-truth array value while allowing
  additional observed values.
- `canonical_set` requires the same factual set after applying the frozen aliases
  in `ground-truth/normalization-aliases.json`.

`tables/matching-sensitivity.csv` compares these reviewed rules with strict
case-and-whitespace-normalized exact matching.

## Weighting

Site-level tables give equal weight to each model-by-retrieval configuration.
BM25 and full-corpus runs have weight 1. Each of the three expanded repetitions
has weight 1/3. This prevents expanded retrieval from dominating aggregate RQ1
results.

## Outputs

`tables/all-field-comparisons.csv` is the complete audit trail. The other CSV
files aggregate it by run, site, evidence class, section, field error, citation,
conflict, and measurement-gap recovery. Matching LaTeX tables are provided for
the main site, evidence-class, and citation results.

Every figure is saved as PDF for the paper and PNG for quick inspection.
Citation validity in these outputs is mechanical: the chunk exists, belongs to
the target site, uses the stored URL, and contains the quoted text. Semantic
entailment still requires manual review.
