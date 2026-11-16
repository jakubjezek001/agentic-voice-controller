# data

Private working folder (git-ignored, mounted into Docker as `/data`), except this file and
`config.example.toml`: the defaults of every setting, copied to `config.toml` on first run. See the main
README for the layout. `conversations/` holds the spoken sessions with the agent. Back it up if your voice bank matters to you; delete `cache/` to
free disk space (models download again on next use).
