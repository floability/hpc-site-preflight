# Discovery selection run

This trace records only documentation discovery and source selection for Purdue Anvil. It did not
build a corpus, extract profile fields, or construct a site profile.

- Run date: 2026-07-25
- Modes: simulated Anvil measurements, live web, live `gpt-5-mini`
- Maximum discovery steps: 2
- Run ID: `cea29978-1b4c-45e1-bb4e-25ce192d3ac8`

## 1. Input and fixed query plan

The login-measurement bundle produced this identity:

```json
{
  "site_id": "anvil",
  "site_name": "Anvil",
  "aliases": ["Anvil"],
  "scheduler": "slurm",
  "hostname_patterns": ["*.anvil.rcac.purdue.edu"],
  "observed_hosts": ["login03", "login03.anvil.rcac.purdue.edu"],
  "allowed_domains": ["purdue.edu"],
  "preferred_path_tokens": ["anvil"]
}
```

Python generated ten initial searches:

| Topic | Search |
| --- | --- |
| canonical | `Anvil official user guide site:purdue.edu` |
| canonical | `Anvil documentation user guide site:purdue.edu` |
| submission | `Anvil slurm submit job required options account allocation site:purdue.edu` |
| submission | `Anvil batch job script submit command project account site:purdue.edu` |
| resources | `Anvil queue partition walltime node job limits site:purdue.edu` |
| resources | `Anvil partition maximum nodes walltime CPU memory GPU site:purdue.edu` |
| storage | `Anvil policies FAQ charging accounting service units site:purdue.edu` |
| storage | `Anvil scratch purge retention storage policy site:purdue.edu` |
| networking | `Anvil compute login node network firewall TCP ports site:purdue.edu` |
| networking | `Anvil worker networking outbound compute nodes site:purdue.edu` |

## 2. Live DuckDuckGo results

Each search returned eight results from the allowed `purdue.edu` domain. One representative result
from each search is shown below.

| Search | Representative result |
| --- | --- |
| canonical 1 | [Anvil User Guide](https://docs.rcac.purdue.edu/userguides/anvil/) |
| canonical 2 | [Anvil User Guide](https://docs.rcac.purdue.edu/userguides/anvil/) |
| submission 1 | [Job Submission](https://docs.rcac.purdue.edu/userguides/anvil/jobs/) |
| submission 2 | [Anvil LAMMPS Job Submit Script](https://www.rcac.purdue.edu/knowledge/anvil/software/installing_applications/lammps/lammps_job_submit_script?all=true) |
| resources 1 | [Slurm Partitions](https://www.rcac.purdue.edu/knowledge/anvil/run/partitions?all=true) |
| resources 2 | [Slurm Partitions](https://www.rcac.purdue.edu/knowledge/anvil/run/partitions?all=true) |
| storage 1 | [Anvil Policies and FAQs](https://www.rcac.purdue.edu/knowledge/anvil/policies) |
| storage 2 | [Anvil File Systems](https://www.rcac.purdue.edu/knowledge/anvil/storage/filesystems) |
| networking 1 | [Anvil User Guide](https://www.rcac.purdue.edu/knowledge/anvil?all=true) |
| networking 2 | [Accessing Compute Nodes](https://rcac.purdue.edu/knowledge/anvil/run/access?all=true) |

The 80 result occurrences became 58 unique allowed candidates. The search results contained strong
partition, filesystem, and policy pages, but search results are only candidates until fetched.

## 3. Local ranking and bounded fetch

Local Python code ranked candidates using target-site scope, the `anvil` path token, site aliases,
and topic coverage. It fetched ten pages in discovery step 1:

| # | Fetched target-site page | Parsed sections |
| ---: | --- | ---: |
| 1 | [Anvil User Guide](https://docs.rcac.purdue.edu/userguides/anvil/) | 3 |
| 2 | [Job Submission](https://docs.rcac.purdue.edu/userguides/anvil/jobs/) | 18 |
| 3 | [Anvil Object Storage](https://docs.rcac.purdue.edu/userguides/anvil/objectstorage/) | 2 |
| 4 | [Object Storage Access](https://docs.rcac.purdue.edu/userguides/anvil/objectstorage/access/) | 8 |
| 5 | [Object Storage Security](https://docs.rcac.purdue.edu/userguides/anvil/objectstorage/acl/) | 9 |
| 6 | [Object Storage Concepts](https://docs.rcac.purdue.edu/userguides/anvil/objectstorage/concepts/) | 8 |
| 7 | [Object Storage Getting Started](https://docs.rcac.purdue.edu/userguides/anvil/objectstorage/getting-started/) | 5 |
| 8 | [Object Storage User Tools](https://docs.rcac.purdue.edu/userguides/anvil/objectstorage/usertools/) | 27 |
| 9 | [Access to Anvil](https://docs.rcac.purdue.edu/userguides/anvil/access/) | 12 |
| 10 | [Anvil Software](https://docs.rcac.purdue.edu/userguides/anvil/anvil-software/) | 4 |

All ten pages passed the domain and target-site scope checks. Link expansion again allowed object
storage to consume six of ten initial fetch slots. The strong filesystem, partition, and policy
results found by DuckDuckGo were therefore not among the fetched candidates shown to the model.

## 4. Model decision

The model received the site identity, topic labels, discovery step (`1 of 2`), and every fetched
candidate's URL, title, scope, headings, and first 1,200 characters. The prompt was 25,425
characters. Its decision contract was:

```json
{
  "source_urls": ["already-fetched target-site URLs"],
  "decision": "complete | search_more",
  "follow_up_queries": ["up to three search queries, never URLs"],
  "summary": "selection rationale",
  "unanswered_topics": ["remaining gaps"]
}
```

The live response selected all ten pages and stopped:

```json
{
  "decision": "complete",
  "follow_up_queries": [],
  "summary": "Selected core RCAC Anvil documentation pages covering the user guide, access, job submission, object storage, and software.",
  "unanswered_topics": [
    "detailed node counts and hardware specifications",
    "network topology and external data-transfer behavior",
    "storage quotas and per-user limits",
    "partition-specific queue and time limits",
    "firewall, NAT, or VPN requirements"
  ]
}
```

The summary above is shortened, but the decision and gap meanings are unchanged. Because the model
returned `complete`, the agent correctly stopped after step 1. The configured value is a maximum,
not a requirement to always execute two steps.

## 5. Validation and measured result

Python confirmed that all selected URLs had been fetched and were classified as `target_site`. No
selection-correction call was needed.

| Result | Value |
| --- | ---: |
| Configured maximum steps | 2 |
| Executed discovery steps | 1 |
| Searches | 10 |
| Fetches | 10 |
| Unique ranked candidates | 58 |
| Selected pages | 10 |
| Model calls | 1 |
| Input tokens | 6,887 |
| Output tokens | 393 |
| Total tokens | 7,280 |
| Total time | 38.18 seconds |
| Termination | `model_selected` |

## Assessment

The bounded loop behaved as implemented: the model controlled completion, and no unnecessary
second search was run after `decision: complete`. The selection quality was not satisfactory,
however. The response declared completion while listing several unresolved topics that are central
to the site profile, including partition limits and filesystem policy. It also selected every
candidate rather than the smallest useful set.

This run exposes two separate improvement targets:

1. Fetch ranking needs topic diversity so object-storage links cannot crowd out already-discovered
   filesystem, partition, and policy pages.
2. The completion decision needs a stricter criterion: unresolved core profile topics should cause
   `search_more` when authoritative candidates are likely to exist.

The raw capture is stored at
`runs/cea29978-1b4c-45e1-bb4e-25ce192d3ac8/discovery-selection.json`.
