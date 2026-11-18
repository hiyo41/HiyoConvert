# HiyoConvert 🎵

> Batch audio converter with multithreading — fast, smart, easy.
> Công cụ chuyển đổi audio hàng loạt với đa luồng — nhanh, thông minh, dễ dùng.

[![CI](https://github.com/hiyo412012/HiyoConvert/actions/workflows/ci.yml/badge.svg)](https://github.com/hiyo412012/HiyoConvert/actions/workflows/ci.yml)

One menu screen, one progress bar, a CLI for scripting, and 123 tests over the
logic that decides filenames, ffmpeg arguments, URL parsing and job planning.

---

## Quick Start / Bắt đầu nhanh

### Interactive / Tương tác

```
# Windows
setup.bat
convert.bat

# Linux / macOS
chmod +x setup.sh && ./setup.sh
./convert.sh
```

Then pick a source format, a target format, tweak settings, press Enter.
Everything is on one screen — no stepping through six menus to convert an
album.

### CLI / Dòng lệnh

```
python hiyo-convert.py "D:\Music" --to mp3 --quality 8
python hiyo-convert.py "D:\Music" --to opus --out "D:\Out" --keep
python hiyo-convert.py "D:\Music" --to flac --dry-run
python hiyo-convert.py "D:\Music" --to m4a --src .wav -j 8
python hiyo-convert.py --config          # show saved settings
python hiyo-convert.py --version
python hiyo-convert.py --help
```

| Flag | Meaning |
| --- | --- |
| `--to` | target format: `.mp3 .ogg .flac .wav .m4a .opus .wma .ac3 .alac .mp2` |
| `--src` | only convert this source extension, e.g. `--src .flac` |
| `-q` | quality 1-10, 10 = best |
| `-o` | output directory (default: alongside the original) |
| `--sample-rate` | 44100 / 48000 / 96000 / 192000, clamped per codec |
| `--channels` | 1 mono, 2 stereo |
| `--normalize` | EBU R128 loudnorm |
| `--strip-meta` | drop all metadata |
| `--simplify` | clean up filenames |
| `--strip-diacritics` | `Bài Hát` → `Bai Hat` |
| `--keep` | keep originals (default: delete after success) |
| `--force` | re-encode even when source and target extensions match |
| `--dry-run` | list what would happen, convert nothing, create nothing |
| `-j` | parallel ffmpeg processes (default: CPU count − 1) |
| `--lang` | `en` or `vi` for CLI output |

Exit code is 1 if any file failed, so it drops straight into CI.

Same-extension conversions are skipped by default (`mp3 → mp3` only loses
quality). Pass `--force` when you really want a re-encode.

---

## The Menu / Menu

```
  ╒ === HiyoConvert ===
  [S] Current source: Local files
  [T] Target:         MP3 (LAME)
  [F] Source filter:  All audio formats
  [D] Change source dir:  D:\Music
  [L] Language: English

  ╘ Settings
  [1] Quality              7/10 (VBR q=3)
  [2] Output dir           C:\Music\out
  [3] Sample rate          Original
  [4] Channels             Original
  [5] Normalize            NO
  [6] Strip metadata       NO
  [7] Simplify names       NO
  [8] Strip diacritics     NO
  [9] Keep originals       NO
  [0] Dry-run              NO
  ──────────────────────────────────────────────
  [Enter] START        [Q] Quit
```

Nothing is sequential. Change any row, press Enter. Before converting you get a
confirm screen that spells out the codec, the quality, and — in bold — whether
**originals will be deleted**.

Settings and language persist to `%APPDATA%\HiyoConvert\config.json`
(`~/.config/hiyoconvert` elsewhere). Configure once.

---

## Supported Codecs / Codec hỗ trợ

### Source (decode) / Nguồn

MP3, OGG, FLAC, WAV, AAC (M4A), Opus, WMA, AIFF, ALAC, AC3, DTS, AMR, AU,
MP2, WavPack, TTA, APE, Speex.

### Target (encode) / Đích

| Format | Codec | Quality 1-10 maps to | Max sample rate |
| --- | --- | --- | --- |
| MP3 | libmp3lame | VBR `-q:a` 9→0 | 48 kHz |
| OGG | libvorbis | VBR `-q:a` 0→10 | 96 kHz |
| FLAC | flac | compression_level 0→8 | 192 kHz |
| WAV | pcm_s16le | uncompressed | 192 kHz |
| AAC | aac | CBR 96→320 kbps | 96 kHz |
| Opus | libopus | CBR 64→320 kbps | 48 kHz |
| WMA | wmav2 | CBR 64→320 kbps | 48 kHz |
| AC3 | ac3 | CBR 64→640 kbps | 48 kHz |
| ALAC | alac | lossless | 96 kHz |
| MP2 | mp2 | CBR snapped to 32/48/56/64/80/96/112/128/160/192/224/256/320/384 kbps | 48 kHz |

Quality 1-10 is inverted automatically for MP3, whose `-q:a` scale runs
backwards. Asking for 192 kHz MP3 clamps to 48 kHz instead of failing.

---

## How it works / Cách hoạt động

```
User input                       Scanner            Converter
   │                                │                    │
   ├─ Language                      │                    │
   ├─ Source type ──────────────►   │                    │
   ├─ Target codec                  │                    │
   ├─ Settings row (any order)      │                    │
   ├─ [Enter] START ───────────►    │                    │
   │                                ▼                    ▼
   │                          rglob + dedupe      ffmpeg -threads 1 -i a.flac
   │                                │             ffmpeg -threads 1 -i b.flac
   │                                │             ffmpeg -threads 1 -i c.flac
   │                           ThreadPoolExecutor
   │                           progress bar + ETA
```

- **ffmpeg** is the backend. One process per file, `-threads 1` each, so
  `CPU − 1` processes actually use `CPU − 1` cores instead of oversubscribing.
- Files are **deduplicated** before conversion. Selecting both `Album` and
  `Album/Disc1` would otherwise convert each track twice.
- Output names are **reserved in memory** while jobs are planned, so
  `--simplify` cannot make two files race onto one destination.
- **Originals are deleted only after** ffmpeg exits 0 *and* the output exists
  with non-zero size. A failed or empty convert always keeps the source.
- ffmpeg's stderr is captured. Failures land in `hiyo-convert_errors.log` with
  the actual reason, not just the path.
- **Ctrl+C** stops after the file in flight finishes and prints the summary.
- `--dry-run` creates no directories and converts nothing.

### YouTube / Spotify

```
python hiyo-convert.py --to mp3
# then pick [S] YouTube / Spotify, paste URLs
```

- YouTube videos, Shorts and playlists are downloaded with `yt-dlp`; every
  playlist entry is converted.
- Spotify track, album and playlist links are resolved through Spotify's
  public embed page for title/artist metadata, then each track is searched on
  YouTube with `yt-dlp`. This is **not** Spotify's audio.
- Downloaded titles are sanitized before they become filenames, so characters
  like `/ : ? *` cannot break the output path.
- Files go to a temp dir that is removed afterwards.

---

## Project Structure / Cấu trúc

```
HiyoConvert/
├── hiyo-convert.py      # everything: menu, CLI, conversion core
├── test_hiyo.py         # unit tests for pure helpers
├── test_v22.py          # regression tests for 2.2 fixes
├── test_e2e.py          # real ffmpeg end-to-end tests
├── requirements.txt     # yt-dlp (only for URL mode)
├── requirements-dev.txt # ruff
├── pyproject.toml       # ruff config
├── .github/workflows/   # CI: Windows + Linux, Python 3.11/3.13
├── convert.bat/sh       # launchers
├── setup.bat/sh         # installers
├── CHANGELOG.md
└── README.md
```

One file on purpose. A single-file CLI tool that people drag onto a folder does
not need a package layout, and the menu and CLI share every function below the
UI layer.

---

## Tests / Kiểm thử

```
python -m unittest discover -v
```

123 tests. The pure ones need no ffmpeg and no network; the e2e ones skip
automatically when ffmpeg is absent:

- `quality_params` maps all 10 levels for all 10 codecs, monotonic, in range
- `simplify_name` strips tags, avoids reserved Windows names, stays idempotent
- `plan_jobs` reserves colliding output names and does not create dirs on dry-run
- `safe_filename` blocks path traversal and invalid Windows characters
- `get_output_path` preserves subfolders and collision suffixes
- `collect_files` dedupes nested directory selections
- `build_ffmpeg_cmd` always maps audio-only and clamps sample rate
- Spotify embed parsing handles tracks, albums and playlists
- `strip_diacritics` handles Đ/đ, which NFKD alone does not decompose

Development checks:

```
pip install -r requirements-dev.txt
ruff check .
python -m unittest discover -v
```

---

## Requirements / Yêu cầu

- **Python 3.11+** (required by current `yt-dlp`)
  - Windows: `winget install -e --id Python.Python.3.14`
  - Debian: `sudo apt install -y python3 ffmpeg`
  - macOS: `brew install python ffmpeg`
- **ffmpeg**
  - Windows: `winget install -e --id Gyan.FFmpeg`
  - Debian: `sudo apt install -y ffmpeg`
  - macOS: `brew install ffmpeg`
- **yt-dlp** — only for YouTube/Spotify URL mode:
  `pip install -r requirements.txt`

`setup.bat` / `setup.sh` handle all of it.

---

## License / Giấy phép

MIT — see [LICENSE](LICENSE). Free to use, modify, and share.
