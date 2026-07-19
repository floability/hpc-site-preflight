# Schemas

`site-info.schema.json` and `site-profile.schema.json` define checked external contracts. Their
Pydantic models under `src/hpc_site_preflight/` must remain equivalent. Other Pydantic contracts
remain authoritative until their JSON Schema files are added.

The files under `measurement-fields/` are reviewed design catalogs, not JSON Schema documents.
They define the field vocabulary from which later Pydantic contracts and exported schemas will
be built.
