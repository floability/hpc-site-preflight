# Schemas

`site-info.schema.json` defines the external site-information contract. Its Pydantic model under
`src/hpc_site_preflight/site_info/` must remain equivalent. Other Pydantic contracts remain
authoritative until their JSON Schema files are added.

The files under `measurement-fields/` are reviewed design catalogs, not JSON Schema documents.
They define the field vocabulary from which later Pydantic contracts and exported schemas will
be built.
