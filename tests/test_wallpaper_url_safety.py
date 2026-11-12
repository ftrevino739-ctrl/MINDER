from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import desktop


class WallpaperUrlSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name) / "wallpapers"
        self._cache_patch = patch.object(desktop, "_wallpaper_cache_dir", return_value=self.cache)
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_non_http_scheme_before_network_access(self):
        with patch("urllib.request.urlopen") as open_url:
            with self.assertRaisesRegex(ValueError, "Only HTTP and HTTPS"):
                desktop._download_wallpaper("file:///C:/private/photo.jpg")
        open_url.assert_not_called()

    def test_rejects_unsupported_extension(self):
        with patch("urllib.request.urlopen") as open_url:
            with self.assertRaisesRegex(ValueError, "supported image extension"):
                desktop._download_wallpaper("https://example.test/image.svg")
        open_url.assert_not_called()

    def test_download_uses_bounded_http_response_and_persistent_unique_file(self):
        class Response:
            headers = {"Content-Length": "4"}
            def __enter__(self):
                self.stream = io.BytesIO(b"test")
                return self
            def __exit__(self, *_):
                self.stream.close()
            def read(self, size):
                return self.stream.read(size)

        with patch("urllib.request.urlopen", return_value=Response()) as open_url:
            path = desktop._download_wallpaper("https://example.test/photo.jpg")

        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), b"test")
        self.assertEqual(path.parent, self.cache)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 20)

    def test_rejects_download_over_size_limit_and_cleans_partial_file(self):
        class Response:
            headers = {"Content-Length": str(desktop._MAX_WALLPAPER_DOWNLOAD_BYTES + 1)}
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None

        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ValueError, "20 MB limit"):
                desktop._download_wallpaper("https://example.test/large.jpg")
        self.assertEqual(list(self.cache.glob("*")), [])


if __name__ == "__main__":
    unittest.main()
