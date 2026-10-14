import os
import tempfile
import unicodedata
import unittest

from photo_orphan_cleaner.core import FORMATS, find_orphans, name_key


def touch(root, *relpaths):
    for rel in relpaths:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x")


class FindOrphansTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ref = os.path.join(self._tmp.name, "ref")
        self.tgt = os.path.join(self._tmp.name, "tgt")
        os.makedirs(self.ref)
        os.makedirs(self.tgt)

    def tearDown(self):
        self._tmp.cleanup()

    def orphan_names(self, ref_fmt="JPG", tgt_fmts=tuple(FORMATS), ref_rec=False, tgt_rec=False,
                     ref=None, tgt=None):
        result = find_orphans(ref or self.ref, ref_fmt, ref_rec, tgt or self.tgt, tgt_fmts, tgt_rec)
        return sorted(os.path.relpath(f.path, tgt or self.tgt) for f in result.orphans), result

    def test_basic_matching_by_stem(self):
        touch(self.ref, "A.jpg", "B.jpg")
        touch(self.tgt, "A.arw", "B.tif", "C.arw", "C.heif", "D.jpg")
        names, result = self.orphan_names()
        self.assertEqual(names, ["C.arw", "C.heif", "D.jpg"])
        self.assertEqual(result.reference_count, 2)
        self.assertEqual(result.target_count, 5)
        self.assertEqual(result.matched_count, 2)

    def test_case_insensitive_names_and_extensions(self):
        touch(self.ref, "dsc0001.JPG")
        touch(self.tgt, "DSC0001.ARW", "DSC0002.ArW")
        names, _ = self.orphan_names()
        self.assertEqual(names, ["DSC0002.ArW"])

    def test_extension_aliases(self):
        touch(self.ref, "a.jpeg", "b.tiff")
        touch(self.tgt, "a.arw", "b.arw", "c.arw")
        self.assertEqual(self.orphan_names("JPG")[0], ["b.arw", "c.arw"])
        self.assertEqual(self.orphan_names("TIFF")[0], ["a.arw", "c.arw"])

    def test_heif_includes_heic_and_hif(self):
        touch(self.ref, "a.jpg")
        touch(self.tgt, "x.heif", "y.heic", "z.HIF", "a.hif")
        names, _ = self.orphan_names(tgt_fmts=["HEIF"])
        self.assertEqual(names, ["x.heif", "y.heic", "z.HIF"])

    def test_only_selected_target_formats_and_other_files_ignored(self):
        touch(self.ref, "a.jpg")
        touch(self.tgt, "b.arw", "b.jpg", "b.png", "b.ARW.xmp", "notes.txt")
        names, result = self.orphan_names(tgt_fmts=["ARW"])
        self.assertEqual(names, ["b.arw"])
        self.assertEqual(result.target_count, 1)

    def test_reference_format_is_exclusive(self):
        # An ARW in the reference folder must not count when JPG is chosen.
        touch(self.ref, "a.jpg", "b.arw")
        touch(self.tgt, "a.arw", "b.arw")
        self.assertEqual(self.orphan_names("JPG")[0], ["b.arw"])

    def test_target_recursion(self):
        touch(self.ref, "a.jpg")
        touch(self.tgt, "a.arw", "b.arw", os.path.join("sub", "c.arw"), os.path.join("sub", "deep", "a.arw"))
        self.assertEqual(self.orphan_names()[0], ["b.arw"])
        self.assertEqual(
            self.orphan_names(tgt_rec=True)[0], ["b.arw", os.path.join("sub", "c.arw")]
        )

    def test_reference_recursion(self):
        touch(self.ref, "a.jpg", os.path.join("2024", "b.jpg"))
        touch(self.tgt, "a.arw", "b.arw")
        self.assertEqual(self.orphan_names()[0], ["b.arw"])
        self.assertEqual(self.orphan_names(ref_rec=True)[0], [])

    def test_same_folder_for_reference_and_target(self):
        touch(self.ref, "a.jpg", "a.arw", "b.arw")
        names, _ = self.orphan_names(ref=self.ref, tgt=self.ref)
        self.assertEqual(names, ["b.arw"])  # reference files themselves always match

    def test_unicode_normalization(self):
        nfc = unicodedata.normalize("NFC", "사진")
        nfd = unicodedata.normalize("NFD", "사진")
        self.assertNotEqual(nfc, nfd)
        self.assertEqual(name_key(nfc + ".jpg"), name_key(nfd + ".ARW"))

    def test_multiple_dots_in_name(self):
        touch(self.ref, "trip.day1.jpg")
        touch(self.tgt, "trip.day1.arw", "trip.arw")
        self.assertEqual(self.orphan_names()[0], ["trip.arw"])

    def test_directory_named_like_photo_is_ignored(self):
        touch(self.ref, "a.jpg")
        os.makedirs(os.path.join(self.tgt, "folder.arw"))
        self.assertEqual(self.orphan_names()[0], [])

    def test_empty_reference_is_refused(self):
        touch(self.ref, "a.arw")  # wrong format only
        touch(self.tgt, "a.arw")
        with self.assertRaises(ValueError):
            self.orphan_names("JPG")

    def test_invalid_input(self):
        touch(self.ref, "a.jpg")
        with self.assertRaises(ValueError):
            find_orphans("", "JPG", False, self.tgt, ["ARW"], False)
        with self.assertRaises(ValueError):
            find_orphans(self.ref, "JPG", False, os.path.join(self.tgt, "missing"), ["ARW"], False)
        with self.assertRaises(ValueError):
            find_orphans(self.ref, "JPG", False, self.tgt, [], False)
        with self.assertRaises(ValueError):
            find_orphans(self.ref, "PNG", False, self.tgt, ["ARW"], False)

    def test_quoted_path_is_accepted(self):
        # Windows "Copy as path" wraps the path in double quotes.
        touch(self.ref, "a.jpg")
        touch(self.tgt, "b.arw")
        result = find_orphans(f'"{self.ref}"', "JPG", False, f' "{self.tgt}" ', ["ARW"], False)
        self.assertEqual([f.name for f in result.orphans], ["b.arw"])

    def test_orphan_metadata(self):
        touch(self.ref, "a.jpg")
        touch(self.tgt, "b.arw")
        _, result = self.orphan_names()
        (f,) = result.orphans
        self.assertEqual(f.size, 1)
        self.assertEqual(f.extension, ".arw")
        self.assertTrue(os.path.isabs(f.path))

    @unittest.skipIf(os.name == "nt", "directory permissions differ on Windows")
    def test_unreadable_subfolder_is_reported_not_fatal(self):
        touch(self.ref, "a.jpg")
        touch(self.tgt, "b.arw", os.path.join("locked", "c.arw"))
        locked = os.path.join(self.tgt, "locked")
        os.chmod(locked, 0)
        try:
            if os.access(locked, os.R_OK):
                self.skipTest("running with privileges that ignore permissions")
            names, result = self.orphan_names(tgt_rec=True)
        finally:
            os.chmod(locked, 0o755)
        self.assertEqual(names, ["b.arw"])
        self.assertEqual(len(result.errors), 1)

    def test_progress_callback(self):
        touch(self.ref, *[f"{i}.jpg" for i in range(300)])
        touch(self.tgt, "x.arw")
        calls = []
        find_orphans(self.ref, "JPG", False, self.tgt, ["ARW"], False, progress=calls.append)
        self.assertTrue(calls)
        self.assertEqual(calls[-1], 301)


if __name__ == "__main__":
    unittest.main()
