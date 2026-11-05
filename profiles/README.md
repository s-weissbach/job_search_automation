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

The default daily batch runs Julia first when both of her private files exist,
then the owner, even if Julia's run fails. Set `JOB_SEARCH_PROFILE=owner` or
`JOB_SEARCH_PROFILE=julia` to run just that profile. Results and score caches stay under
`results/<profile>/`, and website synchronization includes the profile ID.

Job titles and locations can also
be edited on the website's Job Search page. `scripts/website_search_settings.py`
overlays them onto the local config at the start of every run (falling back to
the local config if the website is unreachable) and seeds the website from the
local values the first time.
