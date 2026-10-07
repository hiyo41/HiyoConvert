"""End-to-end check against a real ffmpeg. Skips if ffmpeg is absent.

    python test_e2e.py

Creates synthetic audio, converts it, and asserts on the results: originals
deleted only on success, dedup, subfolder preservation, cover-art handling,
and the batch surviving a failing file.
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "hiyo_convert", Path(__file__).with_name("hiyo-convert.py")
)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def make_audio(path, seconds=1, freq=440):
    """Synthesize a sine tone. The codec follows the extension: ffmpeg refuses
    to put pcm_s16le in a .flac container, so encode per extension."""
    codec = {".flac": "flac", ".ogg": "libvorbis", ".opus": "libopus",
             ".mp3": "libmp3lame", ".m4a": "aac"}.get(path.suffix.lower(), "pcm_s16le")
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:a", codec, str(path)],
        check=True, capture_output=True,
    )
    return path


def make_m4a_with_cover(path):
    """An m4a carrying cover art. ffmpeg fails to mux this to mp3 unless the
    audio stream is mapped explicitly."""
    cover = path.with_suffix(".png")
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:s=32x32:d=1", "-frames:v", "1", str(cover)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-i", str(cover), "-map", "0:a", "-map", "1:v",
         "-c:a", "aac", "-c:v", "png", "-disposition:v:0", "attached_pic",
         "-movflags", "+faststart", str(path)],
        check=True, capture_output=True,
    )
    return path


def write_garbage(path):
    path.write_bytes(b"\x00" * 2048)
    return path


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class TestConvertFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        hc.L = hc.LANG["en"]

    def tearDown(self):
        self.tmp.cleanup()

    def _info(self, path):
        if not FFPROBE:
            return {}
        r = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return __import__("json").loads(r.stdout or "{}")

    def test_converts_and_deletes_original(self):
        src = make_audio(self.root / "a.wav", freq=440)
        dst = self.root / "a.mp3"
        s = hc.Settings(quality=8)
        status, _, orig, new, _ = hc.convert_file(src, dst, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(status, "ok")
        self.assertTrue(dst.exists())
        self.assertFalse(src.exists(), "original must be deleted on success")
        self.assertGreater(orig, 0, "original size must be measured before deletion")
        self.assertGreater(new, 0)
        info = self._info(dst)
        if info:
            self.assertEqual(info["format"]["format_name"], "mp3")

    def test_keep_preserves_original(self):
        src = make_audio(self.root / "b.wav")
        s = hc.Settings(quality=8, keep=True)
        status, _, _, _, _ = hc.convert_file(src, self.root / "b.mp3",
                                             "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(status, "ok")
        self.assertTrue(src.exists())

    def test_size_comparison_is_not_zero(self):
        """Regression: orig_size was read after unlink, so it was always 0 and
        the before/after size report never rendered."""
        src = make_audio(self.root / "c.wav")
        s = hc.Settings(quality=8)
        _, _, orig, new, _ = hc.convert_file(src, self.root / "c.mp3",
                                             "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertGreater(orig, 0)
        self.assertGreater(new, 0)

    def test_failed_convert_keeps_original(self):
        bad = write_garbage(self.root / "bad.mp3")
        dst = self.root / "bad.ogg"
        s = hc.Settings(quality=5)
        status, _, _, _, detail = hc.convert_file(bad, dst, "libvorbis", hc.CODECS[".ogg"], s)
        self.assertEqual(status, "fail")
        self.assertTrue(bad.exists(), "original must survive a failed convert")
        self.assertTrue(detail, "failure must carry a reason for the log")

    def test_cover_art_m4a_converts(self):
        """Regression: cover art mapped as video and the mp3 muxer rejected it."""
        src = make_m4a_with_cover(self.root / "art.m4a")
        dst = self.root / "art.mp3"
        s = hc.Settings(quality=8)
        status, _, _, _, detail = hc.convert_file(src, dst, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(status, "ok", detail)

    def test_lossless_targets(self):
        for ext in (".flac", ".wav", ".alac"):
            with self.subTest(ext=ext):
                src = make_audio(self.root / f"l{ext}.wav")
                info = hc.CODECS[ext]
                s = hc.Settings(quality=10)
                status, _, _, _, detail = hc.convert_file(src, self.root / f"out{ext}",
                                                         info["codec"], info, s)
                self.assertEqual(status, "ok", detail)

    def test_alac_uses_a_valid_container(self):
        """Regression: .alac is not an mp4 family extension, so ffmpeg refused
        it with 'Unable to choose an output format'. ALAC ships in .m4a."""
        src = make_audio(self.root / "alac_src.wav")
        info = hc.CODECS[".alac"]
        dst = self.root / "alac_out.alac"
        status, _, _, _, detail = hc.convert_file(src, dst, info["codec"], info,
                                                  hc.Settings(quality=10))
        self.assertEqual(status, "ok", detail)
        self.assertTrue(dst.exists())
        info_json = self._info(dst)
        if info_json:
            self.assertEqual(info_json["format"]["format_name"], "mov,mp4,m4a,3gp,3g2,mj2")

    def test_sample_rate_clamp_produces_valid_file(self):
        src = make_audio(self.root / "sr.wav")
        s = hc.Settings(quality=8, sample_rate=192000)
        dst = self.root / "sr.mp3"
        status, _, _, _, detail = hc.convert_file(src, dst, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(status, "ok", detail)
        info = self._info(dst)
        if info and info.get("streams"):
            self.assertLessEqual(int(info["streams"][0]["sample_rate"]), 48000)

    def test_mono_downmix(self):
        src = make_audio(self.root / "mono.wav")
        s = hc.Settings(quality=8, channels=1)
        dst = self.root / "mono.mp3"
        status, _, _, _, detail = hc.convert_file(src, dst, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(status, "ok", detail)
        info = self._info(dst)
        if info and info.get("streams"):
            self.assertEqual(info["streams"][0]["channels"], 1)

    def test_normalize_runs(self):
        src = make_audio(self.root / "n.wav")
        s = hc.Settings(quality=8, normalize=True)
        status, _, _, _, detail = hc.convert_file(src, self.root / "n.mp3",
                                                  "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(status, "ok", detail)

    def test_same_path_is_skipped(self):
        src = make_audio(self.root / "same.wav")
        s = hc.Settings(quality=8)
        status, _, _, _, _ = hc.convert_file(src, src, "pcm_s16le", hc.CODECS[".wav"], s)
        self.assertEqual(status, "skip")

    def test_existing_destination_is_skipped(self):
        src = make_audio(self.root / "e.wav")
        dst = self.root / "e.mp3"
        dst.write_bytes(b"already here")
        s = hc.Settings(quality=8)
        status, _, _, _, _ = hc.convert_file(src, dst, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(status, "skip")
        self.assertEqual(dst.read_bytes(), b"already here")


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class TestRunBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        hc.L = hc.LANG["en"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_batch_converts_all(self):
        for i in range(4):
            make_audio(self.root / f"t{i}.wav", freq=300 + i * 50)
        files = hc.collect_files([self.root], None)
        s = hc.Settings(quality=7)
        jobs = [(f, hc.get_output_path(f, ".mp3", s, self.root), f.stem) for f in files]
        summary = hc.run_batch(jobs, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(summary["ok"], 4)
        self.assertEqual(summary["fail"], 0)
        self.assertFalse(summary["interrupted"])

    def test_bad_file_does_not_kill_batch(self):
        """Regression: f.result() re-raised, so one failure aborted every
        remaining file in the batch."""
        for i in range(3):
            make_audio(self.root / f"g{i}.wav")
        write_garbage(self.root / "broken.mp3")
        files = hc.collect_files([self.root], None)
        s = hc.Settings(quality=7)
        jobs = [(f, hc.get_output_path(f, ".ogg", s, self.root), f.stem) for f in files]
        summary = hc.run_batch(jobs, "libvorbis", hc.CODECS[".ogg"], s)
        self.assertEqual(summary["ok"], 3)
        self.assertEqual(summary["fail"], 1)
        self.assertTrue(len(summary["errors"]) == 1)
        self.assertTrue(summary["errors"][0][1], "error detail must be recorded")

    def test_nested_dirs_convert_each_file_once(self):
        """Regression: Album and Album/Disc1 both selected converted every
        track twice, racing two ffmpeg processes onto one destination."""
        album = self.root / "Album"
        (album / "Disc1").mkdir(parents=True)
        (album / "Disc2").mkdir(parents=True)
        make_audio(album / "Disc1" / "a.wav")
        make_audio(album / "Disc2" / "b.wav")
        dirs = [album, album / "Disc1", album / "Disc2"]
        files = hc.collect_files(dirs, None)
        self.assertEqual(len(files), 2)
        s = hc.Settings(quality=7)
        jobs = [(f, hc.get_output_path(f, ".mp3", s, self.root), f.stem) for f in files]
        summary = hc.run_batch(jobs, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(summary["ok"], 2)
        # No _1 suffixes means no second, racing conversion happened.
        produced = sorted(p.name for p in album.rglob("*.mp3"))
        self.assertEqual(produced, ["a.mp3", "b.mp3"])

    def test_output_subfolders_preserved(self):
        album = self.root / "Album" / "Disc1"
        album.mkdir(parents=True)
        make_audio(album / "deep.wav")
        out = self.root / "out"
        s = hc.Settings(quality=7, output_dir=out)
        files = hc.collect_files([self.root], None)
        jobs = [(f, hc.get_output_path(f, ".mp3", s, self.root), f.stem) for f in files]
        hc.run_batch(jobs, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertTrue((out / "Album" / "Disc1" / "deep.mp3").exists())

    def test_size_totals_reported(self):
        for i in range(2):
            make_audio(self.root / f"s{i}.wav")
        files = hc.collect_files([self.root], None)
        s = hc.Settings(quality=7)
        jobs = [(f, hc.get_output_path(f, ".mp3", s, self.root), f.stem) for f in files]
        summary = hc.run_batch(jobs, "libmp3lame", hc.CODECS[".mp3"], s)
        self.assertEqual(summary["ok"], 2)
        self.assertGreater(summary["elapsed"], 0)


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "in"
        self.src.mkdir()
        hc.L = hc.LANG["en"]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *argv):
        # The CLI emits UTF-8 box drawing; this host's default codec is cp1252.
        return subprocess.run(
            [sys.executable, str(Path(__file__).with_name("hiyo-convert.py")), *argv],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=self.tmp.name, stdin=subprocess.DEVNULL,
        )

    def test_dry_run_changes_nothing(self):
        make_audio(self.src / "d.wav")
        r = self._run(str(self.src), "--to", "mp3", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.src / "d.wav").exists())
        self.assertFalse((self.src / "d.mp3").exists())
        self.assertIn("d.wav", r.stdout)

    def test_real_run(self):
        make_audio(self.src / "r.wav")
        r = self._run(str(self.src), "--to", "mp3", "-q", "8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.src / "r.mp3").exists())
        self.assertFalse((self.src / "r.wav").exists())

    def test_keep_flag(self):
        make_audio(self.src / "k.wav")
        r = self._run(str(self.src), "--to", "mp3", "--keep")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.src / "k.wav").exists())

    def test_src_filter(self):
        make_audio(self.src / "only.wav")
        make_audio(self.src / "skipme.flac")
        r = self._run(str(self.src), "--to", "mp3", "--src", ".wav")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.src / "only.mp3").exists())
        self.assertTrue((self.src / "skipme.flac").exists())

    def test_out_dir(self):
        make_audio(self.src / "o.wav")
        out = self.root / "out"
        r = self._run(str(self.src), "--to", "ogg", "-o", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((out / "o.ogg").exists())

    def test_unknown_target_exits_2(self):
        r = self._run(str(self.src), "--to", "wobble")
        self.assertEqual(r.returncode, 2)

    def test_missing_path_exits_2(self):
        r = self._run(str(self.root / "nope"), "--to", "mp3")
        self.assertEqual(r.returncode, 2)

    def test_failure_exits_1(self):
        write_garbage(self.src / "bad.mp3")
        r = self._run(str(self.src), "--to", "ogg")
        self.assertEqual(r.returncode, 1)
        self.assertTrue((self.src / "bad.mp3").exists())

    def test_config_prints_json(self):
        r = self._run("--config")
        self.assertEqual(r.returncode, 0, r.stderr)
        __import__("json").loads(r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)