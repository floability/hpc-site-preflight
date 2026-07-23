# Pipeline

1. Receive an optional backpack and required site descriptor.
2. Look up local or remote site profiles.
3. Load simulated measurements or obtain measured login-node evidence.
4. Discover official target-site documentation with bounded AI assistance.
5. Build and persist a scoped corpus.
6. Select extraction context using full-corpus, BM25, or one-call AI-expanded BM25.
7. Extract submission, network, and operational groups with constrained calls.
8. Validate schema, citations, quotes, field context, and site scope.
9. Classify unresolved fields by required next action.
10. Run approved pilots or load simulated pilot results.
11. Reconcile all evidence into a candidate site profile.
12. Compare the backpack with the profile.
13. Produce an execution plan or early-failure report.

The documentation-only evaluation command stops after Step 8 and emits the corpus plus accepted,
rejected, and unresolved documentation findings.

See [DOCUMENTATION_WORKFLOW.md](DOCUMENTATION_WORKFLOW.md) for the implemented Phase D file and
artifact flow.
