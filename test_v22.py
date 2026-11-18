"""Tests for the 2.2 hardening work. No ffmpeg, no network."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "hiyo_convert", Path(__file__).with_name("hiyo-convert.py")
)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)
hc.L = hc.LANG["en"]


def embed_html(entity):
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    payload = json.dumps(data)
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{payload}</script></html>"
    )


class TestSafeFilename(unittest.TestCase):
    def test_replaces_path_and_windows_chars(self):
        name = hc.safe_filename('a/b\\c:d*e?f"g<h>i|j')
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, name)
        self.assertEqual(Path(name).name, name)

    def test_traversal_cannot_escape(self):
        name = hc.safe_filename("../../etc/passwd")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertEqual(Path(name).name, name)

    def test_reserved_windows_names_are_escaped(self):
        for raw in ("CON", "prn", "AUX", "NUL", "COM1", "LPT9"):
            self.assertNotIn(hc.safe_filename(raw).lower(), hc._RESERVED)

    def test_empty_and_dots_fall_back(self):
        for raw in ("", "   ", "...", "\x00"):
            self.assertTrue(hc.safe_filename(raw))

    def test_truncates_long_titles(self):
        self.assertLessEqual(len(hc.safe_filename("x" * 500)), 120)

    def test_keeps_unicode_and_artist_dash(self):
        self.assertEqual(hc.safe_filename("Bài Hát - Ca Sĩ"), "Bài Hát - Ca Sĩ")

    def test_strips_trailing_dot_and_space(self):
        self.assertEqual(hc.safe_filename("Song. "), "Song")


class TestPlanJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "out"
        self.s = hc.Settings(output_dir=self.out, simplify=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _files(self):
        a = self.root / "Song (Official Video).flac"
        b = self.root / "Song [Official Video].flac"
        a.write_bytes(b"x")
        b.write_bytes(b"x")
        return [a, b]

    def test_reserves_colliding_names(self):
        jobs = hc.plan_jobs(self._files(), ".mp3", self.s, self.root)
        names = [dst.name for _, dst, _ in jobs]
        self.assertEqual(names, ["Song.mp3", "Song_1.mp3"])

    def test_reserves_existing_files(self):
        self.out.mkdir()
        (self.out / "Song.mp3").write_bytes(b"old")
        jobs = hc.plan_jobs(self._files(), ".mp3", self.s, self.root)
        self.assertEqual([dst.name for _, dst, _ in jobs], ["Song_1.mp3", "Song_2.mp3"])

    def test_dry_run_does_not_create_directories(self):
        self.s.dry_run = True
        jobs = hc.plan_jobs(self._files(), ".mp3", self.s, self.root)
        self.assertFalse(self.out.exists())
        self.assertTrue(all(dst.parent == self.out for _, dst, _ in jobs))

    def test_keeps_distinct_names_without_simplify(self):
        self.s.simplify = False
        jobs = hc.plan_jobs(self._files(), ".mp3", self.s, self.root)
        self.assertEqual([dst.name for _, dst, _ in jobs],
                         ["Song (Official Video).mp3", "Song [Official Video].mp3"])


class TestPlanDownloadJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def test_sanitizes_and_reserves_names(self):
        s = hc.Settings(output_dir=self.out)
        jobs = hc.plan_download_jobs(
            [(self.root / "a.webm", "Artist/Song"), (self.root / "b.webm", "Artist/Song")],
            ".mp3", s)
        self.assertEqual([dst.name for _, dst, _ in jobs],
                         ["Artist_Song.mp3", "Artist_Song_1.mp3"])
        self.assertTrue(self.out.is_dir())


class TestSameFormat(unittest.TestCase):
    def test_same_extension_is_skipped(self):
        s = hc.Settings()
        self.assertTrue(hc.same_format(Path("a.wav"), Path("b.wav"), s))

    def test_different_extension_is_not_skipped(self):
        s = hc.Settings()
        self.assertFalse(hc.same_format(Path("a.wav"), Path("b.mp3"), s))

    def test_real_processing_allows_same_extension(self):
        self.assertFalse(hc.same_format(Path("a.wav"), Path("b.wav"),
                                        hc.Settings(sample_rate=48000)))
        self.assertFalse(hc.same_format(Path("a.wav"), Path("b.wav"),
                                        hc.Settings(channels=1)))
        self.assertFalse(hc.same_format(Path("a.wav"), Path("b.wav"),
                                        hc.Settings(normalize=True)))

    def test_force_allows_same_extension(self):
        self.assertFalse(hc.same_format(Path("a.wav"), Path("b.wav"),
                                        hc.Settings(force=True)))


class TestClassifyUrl(unittest.TestCase):
    def test_youtube_variants(self):
        for url in (
            "https://www.youtube.com/watch?v=x",
            "https://youtu.be/x",
            "https://music.youtube.com/watch?v=x",
            "https://youtube.com/shorts/x",
        ):
            self.assertEqual(hc.classify_url(url), "youtube", url)

    def test_spotify_variants(self):
        for url in (
            "https://open.spotify.com/track/abc",
            "https://open.spotify.com/intl-de/track/abc",
            "https://open.spotify.com/playlist/abc",
            "https://spotify.link/abc",
        ):
            self.assertEqual(hc.classify_url(url), "spotify", url)

    def test_invalid(self):
        for url in ("https://example.com", "not a url", ""):
            self.assertIsNone(hc.classify_url(url), url)


class TestSpotifyParse(unittest.TestCase):
    def test_track(self):
        html = embed_html({
            "type": "track",
            "title": "Never Gonna Give You Up",
            "artists": [{"name": "Rick Astley"}],
        })
        self.assertEqual(hc.parse_spotify_embed(html),
                         [{"title": "Never Gonna Give You Up", "artist": "Rick Astley"}])

    def test_playlist_uses_thin_space_between_artists(self):
        html = embed_html({
            "type": "playlist",
            "trackList": [
                {"title": "One", "subtitle": "A,\u2009B"},
                {"title": "Two", "subtitle": "C"},
                {"title": "Three", "subtitle": "D\u2009E"},
            ],
        })
        self.assertEqual(hc.parse_spotify_embed(html), [
            {"title": "One", "artist": "A, B"},
            {"title": "Two", "artist": "C"},
            {"title": "Three", "artist": "D, E"},
        ])

    def test_missing_or_broken_data(self):
        self.assertEqual(hc.parse_spotify_embed("<html></html>"), [])
        self.assertEqual(hc.parse_spotify_embed(
            '<script id="__NEXT_DATA__" type="application/json">{oops</script>'), [])
        self.assertEqual(hc.parse_spotify_embed(
            '<script id="__NEXT_DATA__" type="application/json">{}</script>'), [])


class TestEntryFilepath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_requested_downloads_wins(self):
        path = self.root / "one.webm"
        path.write_bytes(b"x")
        info = {"requested_downloads": [{"filepath": str(path)}]}
        self.assertEqual(hc._entry_filepath(info, self.root), path)

    def test_id_and_ext_fallback(self):
        (self.root / "abc.webm").write_bytes(b"x")
        self.assertEqual(hc._entry_filepath({"id": "abc", "ext": "webm"}, self.root),
                         self.root / "abc.webm")

    def test_id_prefix_fallback(self):
        (self.root / "abcXYZ.webm").write_bytes(b"x")
        self.assertEqual(hc._entry_filepath({"id": "abc"}, self.root),
                         self.root / "abcXYZ.webm")

    def test_search_wrapper_unwraps_first_entry(self):
        (self.root / "vid.webm").write_bytes(b"x")
        info = {"entries": [{"id": "vid", "ext": "webm"}]}
        self.assertEqual(hc._entry_filepath(info, self.root), self.root / "vid.webm")

    def test_missing(self):
        self.assertIsNone(hc._entry_filepath(None, self.root))
        self.assertIsNone(hc._entry_filepath({"id": "nope"}, self.root))


class TestSettingsV22(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_force_is_runtime_only(self):
        s = hc.Settings(force=True)
        self.assertNotIn("force", s.to_dict())
        self.assertFalse(hc.Settings.from_dict(s.to_dict()).force)

    def test_language_roundtrip(self):
        cfg = self.dir / "config.json"
        with mock.patch.object(hc, "CONFIG_DIR", self.dir), \
                mock.patch.object(hc, "CONFIG_PATH", cfg):
            self.assertTrue(hc.Settings(quality=7).save(lang="vi"))
            self.assertEqual(hc.load_saved_lang(), "vi")
            self.assertFalse(cfg.with_name(cfg.name + ".tmp").exists())

    def test_corrupt_language_is_ignored(self):
        cfg = self.dir / "config.json"
        cfg.write_text("{oops", encoding="utf-8")
        with mock.patch.object(hc, "CONFIG_PATH", cfg):
            self.assertIsNone(hc.load_saved_lang())


class TestInputUrls(unittest.TestCase):
    def _run(self, answers):
        it = iter(answers)
        with mock.patch.object(hc, "ask", lambda *a, **k: next(it)), \
                mock.patch.object(hc.sys, "stdout", io.StringIO()):
            return hc.input_urls()

    def test_parses_youtube_and_spotify(self):
        urls = self._run(["https://youtu.be/x https://open.spotify.com/track/abc", ""])
        self.assertEqual(urls, [("youtube", "https://youtu.be/x"),
                                ("spotify", "https://open.spotify.com/track/abc")])

    def test_eof_aborts_instead_of_looping(self):
        with self.assertRaises(hc.Abort):
            self._run(["\x03"])

    def test_invalid_only_aborts(self):
        with self.assertRaises(hc.Abort):
            self._run(["https://example.com", ""])


if __name__ == "__main__":
    unittest.main(verbosity=2)
