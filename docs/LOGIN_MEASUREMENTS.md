# Login measurements

`login-measurements.json` is versioned evidence collected from a login node. Version `0.2` keeps
each fact as a flat observation because every value needs its own status, timestamp, acquisition
method, command ID, and source reference.

```json
{
  "path": "/facts/storage/filesystems/scratch/path",
  "status": "observed",
  "value": "/anvil/scratch/mockuser",
  "observed_at": "2026-07-19T12:00:00Z",
  "method": "collector_function",
  "command_id": "resolve_reviewed_path",
  "source_reference": "simulated evidence; not a real observation"
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

The Anvil simulated input at
`examples/simulate/anvil/login-measurements.json` is the current complete `0.2` example.
