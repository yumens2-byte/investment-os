"""mediastore(AI Drive 미디어 보관) 회귀 테스트 (v2.9.0 — 2026-10-15 설계 C-2).

실제 gsk 호출은 하지 않는다(네트워크·자격증명 비의존). 순수 로직만 검증한다.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from .. import mediastore
except ImportError:                                    # 단독 실행 대비
    from gen.hero_shorts import mediastore


class ArchiveNameTest(unittest.TestCase):
    def test_no_duplicate_prefix(self):
        self.assertEqual(
            mediastore.archive_name(88, "/tmp/ep88_final_voiced.mp4"),
            "ep88_final_voiced.mp4",
        )

    def test_prefix_added_when_missing(self):
        self.assertEqual(
            mediastore.archive_name(88, "/tmp/final_voiced.mp4"),
            "ep88_final_voiced.mp4",
        )


class ArchiveDisabledTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("HERO_MEDIA_ARCHIVE")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HERO_MEDIA_ARCHIVE", None)
        else:
            os.environ["HERO_MEDIA_ARCHIVE"] = self._old

    def test_disabled_returns_skipped_without_gsk(self):
        os.environ["HERO_MEDIA_ARCHIVE"] = "0"
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "ep88_final_voiced.mp4"
            f.write_bytes(b"not-a-real-video")
            out = mediastore.archive_media(f, 88)
        self.assertTrue(out["skipped"])
        self.assertIsNone(out["aidrive_path"])
        self.assertEqual(out["bytes"], len(b"not-a-real-video"))

    def test_missing_local_file_raises(self):
        with TemporaryDirectory() as tmp, self.assertRaises(RuntimeError):
            mediastore.archive_media(Path(tmp) / "absent.mp4", 88)


class MediaDirConstantTest(unittest.TestCase):
    def test_media_dir_under_drive_dir(self):
        self.assertEqual(mediastore.MEDIA_DIR, "/hero-shorts/media")


if __name__ == "__main__":
    unittest.main()
