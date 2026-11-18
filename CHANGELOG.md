# Changelog

## 2.2.0 - 2026-11-18

### Fixed

- Downloaded titles are sanitized before becoming filenames; `/`, `:`, `?`
  and other invalid characters can no longer break paths or escape the
  output directory.
- `--dry-run` no longer creates output directories.
- Two sources that map to the same output name are reserved in memory, so
  `--simplify` can no longer make two ffmpeg jobs write the same file.
- Spotify URLs now resolve through Spotify's public embed page instead of
  yt-dlp (which has no Spotify extractor). Track, album and playlist URLs work.
- YouTube playlist URLs download every entry instead of returning one
  non-existent path.
- URL mode no longer flips the saved `keep` setting permanently.
- Ctrl+D / EOF while entering URLs now aborts cleanly instead of looping.
- Same-format conversions are skipped by default; `--force` re-encodes.

### Added

- Persistent UI language; `[L]` changes it from the menu.
- `--force` for deliberate same-format re-encodes.
- CI on Windows and Linux (Python 3.11 and 3.13), Ruff, Dependabot.
- LICENSE file and accurate README.
- `test_v22.py` regression tests for the fixes above.
