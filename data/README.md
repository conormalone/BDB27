# data

Raw data lives on the **USB drive** — never in this repo (it's gitignored).

- `raw/` → symlink to `/mnt/project_data/conor_downloads/bdb27/`. Drop the 2027 BDB files there.
- If the USB isn't mounted, `raw/` will dangle — mount it first (see the `usb-drive-mount` runbook; `scripts/backup.sh` also checks).

Expected 2027 BDB files:
- `combine_tracking.csv`
- `combine_results.csv`
- `player_play.csv`
- `game_tracking_2023.csv`, `game_tracking_2024.csv`, `game_tracking_2025.csv`
