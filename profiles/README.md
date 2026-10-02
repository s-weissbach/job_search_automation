# Additional candidate profiles

Each non-owner profile keeps its personal inputs outside Git:

```text
profiles/<profile>/config.yaml
profiles/<profile>/cv_compressed.yaml
```

Copy the matching `config.example.yaml`, then run a profile manually with:

```bash
JOB_SEARCH_PROFILE=julia scripts/run_perry_local.sh
```

The default owner run automatically starts Julia's isolated run when both of
her private files exist. Results and score caches stay under
`results/<profile>/`, and website synchronization includes the profile ID.
