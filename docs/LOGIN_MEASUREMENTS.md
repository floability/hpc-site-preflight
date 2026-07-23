# Login measurements

`login-measurements.json` is the single versioned site input. Version `0.6` groups directly
addressable identity, storage, Slurm, and HTCondor values while keeping unknown values null.

```json
{
  "site_facts": {
    "site_name": "Anvil",
    "fqdn": "login03.anvil.rcac.purdue.edu",
    "documentation_domains": ["purdue.edu"]
  },
  "storage": {
    "scratch": {
      "observed_path": "/anvil/scratch/x-mislam11",
      "path_pattern": "/anvil/scratch/{username}"
    }
  },
  "detected_schedulers": ["slurm"],
  "slurm": {
    "partitions": [
      {"name": "shared", "node_count": 250, "cpus_per_node": 128}
    ]
  }
}
```

## Facts used by the initial profile

- User facts identify `{username}`, `{account}`, and `{group}` components in measured paths.
- Storage facts provide exact paths, filesystem type, login readability and writability, and
  available bytes.
- Login networking provides DNS resolution, fixed-target outbound HTTPS, local TCP bind, and local
  loopback results.
- Scheduler facts provide the scheduler, submit command, resources, accounts, and visible
  configuration.

The compiler keeps exact paths in the evidence report and writes only supported patterns such as
`/anvil/scratch/{username}` to the site profile. It always creates home, project, data, and scratch
profile entries; missing roles remain `null` and request a future login measurement.

Login measurements never claim compute-node behavior. Compute storage access, compute networking,
cross-node TCP connectivity, and verified TCP port ranges require documentation or an approved
pilot.

The Anvil simulated input at `examples/simulate/anvil/login-measurements.json` and captured example
at `examples/simulate/anvil-real/login-measurement.json` show the current `0.6` contract.

## Standalone collector prototype

The collector does not create a separate site descriptor. Copy
`src/hpc_site_preflight/measurements/capture.py` to a login node and give it only the name people
use for the site:

```bash
python3 collect_site_inputs.py --short-site-name Anvil
```

From this repository:

```bash
python3 scripts/collect_site_inputs.py --short-site-name Anvil
```

It writes one directly addressable `login-measurements.json` prototype with:

- flat `site_facts` fields for identity, documentation hints, user context, OS, architecture, CPU
  count, and available memory;
- `storage.home`, `storage.tmp`, `storage.scratch`, and `storage.project`, each with a measured
  path, username-generalized path pattern, access values, permission string, filesystem type, and
  available bytes;
- `detected_schedulers`, followed by a `slurm` and `htcondor` object when that scheduler is
  detected, or `null` when it is absent;
- a Slurm partition array populated from `sinfo -h -o "%P %D %m %c %G"`, where each object has a
  `name` field.

Repeat `--keyword WORD` for plain-language documentation hints. Use
`--documentation-domain DOMAIN` only when the documentation lives outside the domain inferred from
the login hostname. HTCondor currently records only detected commands and submit-command
availability. The script creates no test files and submits no jobs.

Software discovery is intentionally deferred to a later iteration.

The package validates this same `0.6` document. Simulated site mode requires it. Live site mode
loads it when supplied or captures and saves it when absent.
