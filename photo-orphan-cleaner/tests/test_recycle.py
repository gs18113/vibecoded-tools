import os
import sys
import tempfile
import unittest

from photo_orphan_cleaner.recycle import RecycleError, send_to_recycle_bin, unsupported_volumes


@unittest.skipUnless(sys.platform == "win32", "Windows Recycle Bin only")
class WindowsRecycleBinTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _make(self, name):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(b"test")
        return path

    def test_temp_dir_volume_has_recycle_bin(self):
        self.assertEqual(unsupported_volumes([self._make("a.jpg")]), [])

    def test_file_is_moved_and_neighbours_untouched(self):
        victim = self._make("DSC0001 사진.ARW")
        keeper = self._make("DSC0002.ARW")
        send_to_recycle_bin(victim)
        self.assertFalse(os.path.exists(victim))
        self.assertTrue(os.path.exists(keeper))

    def test_missing_file_raises(self):
        with self.assertRaises(RecycleError) as ctx:
            send_to_recycle_bin(os.path.join(self.dir, "nope.jpg"))
        self.assertFalse(ctx.exception.cancelled)


@unittest.skipIf(sys.platform == "win32", "non-Windows behaviour")
class NonWindowsTest(unittest.TestCase):
    def test_refuses_to_delete(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            path = fh.name
        try:
            with self.assertRaises(RecycleError):
                send_to_recycle_bin(path)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(len(unsupported_volumes([path])), 1)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
