# TODO

- [ ] Create remote repo (GitHub) and push existing commits.
- [ ] No hard-coded keys etc. Audit the codebase for hard-coded secrets/IDs/paths before going public; move anything sensitive to `secrets.env` (already gitignored).
- [ ] Set up on Raspberry Pi and have it run automatically every morning (cron `python run.py` ~07:30; ensure Python deps, `secrets.env`, and Withings token file are present on the Pi).
