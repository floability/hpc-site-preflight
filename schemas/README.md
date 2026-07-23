# Schemas

`site-profile.schema.json` is a checked external contract. Its Pydantic model under
`src/hpc_site_preflight/` must remain equivalent. The structured login-measurement Pydantic model
is authoritative until its JSON Schema file is added.

The files under `measurement-fields/` are reviewed design catalogs, not JSON Schema documents.
They define the field vocabulary from which later Pydantic contracts and exported schemas will
be built.
