# Migration from `hpc-site-policy-agent`

The existing repository is the reference implementation for documentation discovery and extraction.

Adapt these capabilities:

- provider abstraction and rate-limit handling;
- bounded search/fetch/finish loop;
- approved-domain validation;
- target-site and sibling-site classification;
- readable page extraction;
- persistent content-hashed corpus;
- heading-aware chunks and table preservation;
- BM25 and expanded-query retrieval;
- schema-constrained extraction;
- evidence validation;
- JSONL provenance logging.

Do not migrate:

- its top-level CLI;
- its repository layout;
- its final candidate policy as the trusted final profile;
- assumptions that documentation is the only evidence source.

Keep the migrated behavior inside `DocumentationPipeline`, with model and web implementations
supplied through their small provider interfaces. The rest of the project remains independent of
provider-specific and agent-internal details.
