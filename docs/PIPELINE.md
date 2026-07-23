# Pipeline

1. Receive an optional backpack and a supplied or newly captured login-measurement bundle.
2. Look up local or remote site profiles.
3. Validate the structured site, storage, platform, and scheduler facts.
4. Build the initial measurement-backed partial profile.
5. Discover official target-site documentation with bounded AI assistance.
6. Build and persist a scoped corpus.
7. Select extraction context using full-corpus, BM25, or one-call AI-expanded BM25.
8. Extract submission, network, and operational groups with constrained calls.
9. Validate schema, citations, quotes, field context, and site scope.
10. Apply accepted documentation findings deterministically.
11. Run approved pilots or load simulated pilot results.
12. Reconcile all evidence into a candidate site profile.
13. Compare the backpack with the profile.
14. Produce an execution plan or early-failure report.

The documentation-only evaluation command stops after Step 9 and emits the corpus plus accepted,
rejected, and unresolved documentation findings.

See [DOCUMENTATION_WORKFLOW.md](DOCUMENTATION_WORKFLOW.md) for the implemented Phase D file and
artifact flow.
