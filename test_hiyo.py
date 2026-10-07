"""Tests for the pure helpers. No ffmpeg, no network, no subprocess.

    python -m unittest test_hiyo -v
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "hiyo_convert", Path(__file__).with_name("hiyo-convert.py")
)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


class TestQualityParams(unittest.TestCase):
    def test_mp3_quality_is_inverted(self):
        """MP3 -q:a is 0=best 9=worst, so level 10 must map to 0."""
        best, _ = hc.quality_params(hc.CODECS[".mp3"], 10)
        worst, _ = hc.quality_params(hc.CODECS[".mp3"], 1)
        self.assertEqual(best, ["-q:a", "0"])
        self.assertEqual(worst, ["-q:a", "9"])

    def test_vorbis_is_not_inverted(self):
        best, _ = hc.quality_params(hc.CODECS[".ogg"], 10)
        self.assertEqual(best, ["-q:a", "10"])

    def test_cbr_stays_in_range(self):
        for level in range(1, 11):
            args, label = hc.quality_params(hc.CODECS[".m4a"], level)
            kbps = int(label.replace("kbps", ""))
            self.assertGreaterEqual(kbps, 96)
            self.assertLessEqual(kbps, 320)
            self.assertEqual(args[0], "-b:a")

    def test_lossless_has_no_quality_args(self):
        for ext in (".wav", ".alac"):
            args, label = hc.quality_params(hc.CODECS[ext], 10)
            self.assertEqual(args, [])
            self.assertTrue(label)

    def test_monotonic_bitrate(self):
        rates = [int(hc.quality_params(hc.CODECS[".opus"], lv)[1].replace("kbps", ""))
                 for lv in range(1, 11)]
        self.assertEqual(rates, sorted(rates))

    def test_every_codec_maps_every_level(self):
        for ext in hc.CODECS:
            for level in range(1, 11):
                args, label = hc.quality_params(hc.CODECS[ext], level)
                self.assertIsInstance(args, list, ext)
                self.assertTrue(label, f"{ext} level {level} has no label")

    def test_mp2_snaps_to_allowed_bitrates(self):
        """Regression: level 5 mapped to 206k and ffmpeg rejected it with
        'bitrate 206 is not allowed in mp2'. Every level must land on the
        encoder's discrete table."""
        allowed = hc.CODECS[".mp2"]["allowed"]
        for level in range(1, 11):
            args, label = hc.quality_params(hc.CODECS[".mp2"], level)
            kbps = int(label.replace("kbps", ""))
            self.assertIn(kbps, allowed, f"level {level} -> {kbps}k")
            self.assertEqual(args, ["-b:a", f"{kbps}k"])


class TestSimplifyName(unittest.TestCase):
    def test_removes_official_video_tag(self):
        self.assertEqual(hc.simplify_name("Song Name (Official Video)"), "Song Name")

    def test_removes_youtube_id(self):
        self.assertEqual(hc.simplify_name("Song [dQw4w9WgXcQ]"), "Song")

    def test_drops_artist_prefix(self):
        self.assertEqual(hc.simplify_name("Artist - Song Title"), "Artist")

    def test_removes_feat_artist(self):
        out = hc.simplify_name("My Song (feat. Someone Else)")
        self.assertNotIn("Someone", out)
        self.assertIn("My Song", out)

    def test_never_returns_empty(self):
        self.assertTrue(hc.simplify_name("..."))
        self.assertTrue(hc.simplify_name("---"))
        self.assertTrue(hc.simplify_name("【】"))

    def test_strips_reserved_filename_chars(self):
        out = hc.simplify_name('a<b>c:d"e/f\\g|h?i*j')
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, out)

    def test_never_returns_a_reserved_windows_name(self):
        for raw in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
            self.assertNotEqual(hc.safe_stem(hc.simplify_name(raw)).lower(), raw.lower())

    def test_safe_stem_escapes_all_reserved(self):
        for raw in ("CON", "prn", "AUX", "nul", "COM1", "com9", "LPT1", "lpt9"):
            self.assertFalse(hc.safe_stem(raw).lower() in hc._RESERVED, raw)

    def test_safe_stem_leaves_normal_names(self):
        for raw in ("Song", "track 01", "Bài Hát", "a.b.c"):
            self.assertEqual(hc.safe_stem(raw), raw)

    def test_safe_stem_fixes_empty(self):
        self.assertTrue(hc.safe_stem("   "))
        self.assertTrue(hc.safe_stem("..."))

    def test_truncates_long_names(self):
        self.assertLessEqual(len(hc.simplify_name("word " * 60)), 60)

    def test_idempotent(self):
        once = hc.simplify_name("Artist - Song (Official Music Video) [dQw4w9WgXcQ]")
        self.assertEqual(hc.simplify_name(once), once)

    def test_strip_diacritics(self):
        self.assertEqual(hc.strip_diacritics("Bài Hát"), "Bai Hat")
        self.assertEqual(hc.strip_diacritics("Đặng Thùy Trâm"), "Dang Thuy Tram")


class TestGetOutputPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "Album" / "Disc1").mkdir(parents=True)
        self.src = self.root / "Album" / "Disc1" / "track.flac"
        self.src.write_bytes(b"x" * 10)
        self.s = hc.Settings()

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_dir_by_default(self):
        self.assertEqual(hc.get_output_path(self.src, ".mp3", self.s),
                         self.src.with_name("track.mp3"))

    def test_creates_out_dir(self):
        out = self.root / "out"
        dst = hc.get_output_path(self.src, ".mp3", self.s, root=self.root)
        self.assertEqual(dst.parent, self.src.parent)

    def test_preserves_subfolder_when_out_dir_set(self):
        self.s.output_dir = self.root / "out"
        dst = hc.get_output_path(self.src, ".mp3", self.s, root=self.root)
        self.assertEqual(dst, self.root / "out" / "Album" / "Disc1" / "track.mp3")
        self.assertTrue(dst.parent.is_dir())

    def test_collision_keeps_subfolder(self):
        """Regression: the counter branch used to flatten to the output root."""
        self.s.output_dir = self.root / "out"
        first = hc.get_output_path(self.src, ".mp3", self.s, root=self.root)
        first.write_bytes(b"y" * 10)
        second = hc.get_output_path(self.src, ".mp3", self.s, root=self.root)
        self.assertEqual(second.name, "track_1.mp3")
        self.assertEqual(second.parent, first.parent)

    def test_collision_counter_increments(self):
        self.s.output_dir = self.root / "out"
        seen = set()
        for _ in range(3):
            p = hc.get_output_path(self.src, ".mp3", self.s, root=self.root)
            self.assertNotIn(p, seen)
            seen.add(p)
            p.write_bytes(b"z")

    def test_simplify_applied(self):
        self.s.simplify = True
        dst = hc.get_output_path(self.src, ".mp3", self.s)
        self.assertEqual(dst.stem, "track")

    def test_strip_diacritics_applied(self):
        vi = self.root / "Bài Hát.mp3"
        vi.write_bytes(b"x")
        self.s.strip_diacritics = True
        self.assertEqual(hc.get_output_path(vi, ".mp3", self.s).stem, "Bai Hat")

    def test_outside_root_falls_back_to_out_dir(self):
        """A file not under root must not crash relative_to()."""
        self.s.output_dir = self.root / "out"
        outside = Path(tempfile.gettempdir()) / "hc_outside_probe.mp3"
        outside.write_bytes(b"x")
        try:
            dst = hc.get_output_path(outside, ".flac", self.s, root=self.root)
            self.assertEqual(dst.parent, self.root / "out")
        finally:
            outside.unlink(missing_ok=True)


class TestScanDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "Empty").mkdir()
        (self.root / "Music" / "CD1").mkdir(parents=True)
        (self.root / "Music" / "CD2").mkdir(parents=True)
        (self.root / "top.mp3").write_bytes(b"x")
        (self.root / "Music" / "CD1" / "a.flac").write_bytes(b"x")
        (self.root / "Music" / "CD2" / "b.flac").write_bytes(b"x")
        (self.root / "Music" / "cover.jpg").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_skips_empty_dirs(self):
        names = [d.name for d in hc.scan_dirs(self.root, None)]
        self.assertNotIn("Empty", names)

    def test_finds_nested_audio(self):
        names = [d.name for d in hc.scan_dirs(self.root, ".flac")]
        self.assertIn("Music", names)

    def test_root_included_when_it_has_audio(self):
        self.assertIn(self.root, hc.scan_dirs(self.root, None))

    def test_root_excluded_when_filter_excludes_it(self):
        self.assertNotIn(self.root, hc.scan_dirs(self.root, ".flac"))

    def test_dirs_are_audio(self):
        for d in hc.scan_dirs(self.root, None):
            self.assertTrue(d.is_dir(), d)


class TestCollectFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "Album" / "CD1").mkdir(parents=True)
        (self.root / "Album" / "CD2").mkdir(parents=True)
        (self.root / "Album" / "CD1" / "a.flac").write_bytes(b"x")
        (self.root / "Album" / "CD2" / "b.flac").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_nested_selection_does_not_duplicate(self):
        """Regression: selecting Album and Album/CD1 converted each file twice,
        racing two ffmpeg processes onto one destination."""
        dirs = [self.root / "Album", self.root / "Album" / "CD1",
                self.root / "Album" / "CD2"]
        files = hc.collect_files(dirs, None)
        self.assertEqual(len(files), 2)
        self.assertEqual(len({str(f) for f in files}), 2)

    def test_scan_then_collect_is_unique(self):
        dirs = hc.scan_dirs(self.root, None)
        files = hc.collect_files(dirs, None)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(len(files), 2)

    def test_respects_extension_filter(self):
        dirs = hc.scan_dirs(self.root, ".flac")
        self.assertEqual(len(hc.collect_files(dirs, ".flac")), 2)

    def test_filter_excludes_other_types(self):
        (self.root / "Album" / "cover.jpg").write_bytes(b"x")
        files = hc.collect_files([self.root / "Album"], None)
        self.assertTrue(all(f.suffix.lower() in hc.ALL_AUDIO_EXTS for f in files))

    def test_result_is_sorted(self):
        files = hc.collect_files([self.root / "Album"], None)
        self.assertEqual(files, sorted(files))

    def test_directories_not_included(self):
        files = hc.collect_files([self.root], None)
        self.assertTrue(all(f.is_file() for f in files))

    def test_empty_input(self):
        self.assertEqual(hc.collect_files([], None), [])

    def test_excludes_output_dir_inside_source_tree(self):
        """An --out dir inside the source tree must not be re-scanned, or
        each run would convert its own output again."""
        out = self.root / "Album" / "out"
        out.mkdir(parents=True)
        (out / "done.flac").write_bytes(b"x")
        files = hc.collect_files([self.root / "Album"], None, exclude=out)
        self.assertEqual(len(files), 2)
        self.assertTrue(all(out not in f.parents for f in files))

    def test_exclude_none_behaves_as_before(self):
        self.assertEqual(len(hc.collect_files([self.root / "Album"], None, exclude=None)), 2)


class TestBuildFfmpegCmd(unittest.TestCase):
    def setUp(self):
        self.s = hc.Settings()
        self.codec = hc.CODECS[".mp3"]

    def test_always_maps_audio_only(self):
        """Regression: an m4a with cover art failed with 'Could not find tag
        for codec mp3' because the picture stream got mapped."""
        cmd = hc.build_ffmpeg_cmd(Path("a.m4a"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertIn("0:a:0", cmd)
        self.assertIn("-vn", cmd)

    def test_stdin_closed(self):
        """Without -nostdin ffmpeg can swallow the menu's keystrokes."""
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertIn("-nostdin", cmd)

    def test_sample_rate_clamped_to_codec_limit(self):
        self.s.sample_rate = 192000
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "48000")

    def test_sample_rate_under_limit_untouched(self):
        self.s.sample_rate = 44100
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "44100")

    def test_no_sample_rate_flag_when_zero(self):
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertNotIn("-ar", cmd)

    def test_strip_metadata(self):
        self.s.strip_meta = True
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "-1")

    def test_keep_metadata(self):
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "0")

    def test_normalize_adds_filter(self):
        self.s.normalize = True
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path("a.mp3"),
                                  self.codec["codec"], self.codec, self.s)
        self.assertIn("loudnorm", cmd[cmd.index("-af") + 1])

    def test_destination_is_last_arg(self):
        dst = Path("out") / "a.mp3"
        cmd = hc.build_ffmpeg_cmd(Path("a.flac"), dst,
                                  self.codec["codec"], self.codec, self.s)
        self.assertEqual(cmd[-1], str(dst))

    def test_every_target_codec_builds(self):
        s = hc.Settings(quality=7, sample_rate=48000, channels=2, normalize=True)
        for ext, info in hc.CODECS.items():
            cmd = hc.build_ffmpeg_cmd(Path("a.flac"), Path(f"out{ext}"), info["codec"], info, s)
            self.assertIn(info["codec"], cmd)
            self.assertEqual(cmd[cmd.index("-ar") + 1],
                             str(min(48000, info["max_sr"])))


class TestSrcFormats(unittest.TestCase):
    def test_every_source_ext_is_scannable(self):
        """The [F] menu filter and --src must only offer extensions that
        scan_dirs/collect_files actually match."""
        for label, ext in hc.SRC_FORMATS:
            if ext is None:
                continue
            self.assertIn(ext, hc.ALL_AUDIO_EXTS, label)

    def test_every_target_ext_has_a_codec(self):
        for name, ext, _, codec in hc.TGT_FORMATS:
            self.assertIn(ext, hc.CODECS, name)
            self.assertEqual(hc.CODECS[ext]["codec"], codec)

    def test_codecs_is_a_dict(self):
        self.assertIsInstance(hc.CODECS, dict)
        self.assertEqual(len(hc.CODECS), 10)


class TestDrawMenu(unittest.TestCase):
    def test_renders_every_combination_without_crashing(self):
        """Menu smoke test: both languages x source types x filters x targets."""
        import io as _io
        real = hc.sys.stdout
        try:
            for lang in ("en", "vi"):
                hc.L = hc.LANG[lang]
                for src_type in ("local", "url"):
                    for src_ext in (None, ".flac"):
                        for tgt, info in hc.CODECS.items():
                            hc.sys.stdout = _io.StringIO()
                            hc.draw_menu(hc.Settings(), Path("."), src_type,
                                         src_ext, tgt, info)
        finally:
            hc.sys.stdout = real
            hc.L = hc.LANG["en"]


class TestFormatSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(hc.format_size(500), "500B")
        self.assertEqual(hc.format_size(2048), "2.0KB")
        self.assertEqual(hc.format_size(5 * 1024 ** 2), "5.0MB")
        self.assertEqual(hc.format_size(3 * 1024 ** 3), "3.00GB")


class TestSettings(unittest.TestCase):
    def test_roundtrip_preserves_fields(self):
        s = hc.Settings(quality=9, normalize=True, sample_rate=48000, channels=1)
        again = hc.Settings.from_dict(s.to_dict())
        for f in hc.Settings.FIELDS:
            self.assertEqual(getattr(s, f), getattr(again, f), f)

    def test_unknown_keys_ignored(self):
        s = hc.Settings.from_dict({"quality": 3, "nonsense": 1})
        self.assertEqual(s.quality, 3)

    def test_output_dir_none_roundtrip(self):
        self.assertIsNone(hc.Settings.from_dict({"output_dir": None}).output_dir)

    def test_unknown_format_size_label(self):
        self.assertIsInstance(hc.quality_params(hc.CODECS[".flac"], 5)[1], str)


class TestGetShortName(unittest.TestCase):
    def test_splits_on_dash(self):
        self.assertEqual(hc.get_short_name("Artist - Song"), "Artist")

    def test_leaves_plain_name(self):
        self.assertEqual(hc.get_short_name("Song"), "Song")

    def test_leading_dash_kept(self):
        self.assertEqual(hc.get_short_name("- Song"), "- Song")


if __name__ == "__main__":
    unittest.main(verbosity=2)