"""Portable document checks must not hide ordinary missing or untracked links."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('document_checker', ROOT / 'tools/check_docs.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PortableDocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'guide.md').write_text('# Current\n', encoding='utf-8')

    def check(self, target, tracked=(), local=(), fragment=''):
        return checker.check_link(self.root / target, fragment, root=self.root,
                                  tracked=set(tracked), local_references=set(local))

    def test_missing_private_material_is_reported_separately(self):
        self.assertEqual(self.check('private-run', local=['private-run']), (None, True))

    def test_unlisted_missing_target_is_error(self):
        self.assertIsNotNone(self.check('typo.md')[0])

    def test_existing_untracked_file_cannot_mask_clean_clone_failure(self):
        self.assertIsNotNone(self.check('guide.md')[0])

    def test_tracked_missing_file_cannot_be_exempted_by_local_declaration(self):
        error, excluded = self.check('deleted.md', tracked=['deleted.md'], local=['deleted.md'])
        self.assertEqual(error, 'missing target')
        self.assertFalse(excluded)

    def test_anchor_is_validated_in_tracked_document(self):
        self.assertEqual(self.check('guide.md', tracked=['guide.md'], fragment='current'), (None, False))
        self.assertEqual(self.check('guide.md', tracked=['guide.md'], fragment='old')[0], 'missing anchor')

    def test_tracked_directory_and_outside_repository(self):
        self.assertEqual(self.check('.', tracked=['guide.md']), (None, False))
        error, excluded = checker.check_link(self.root.parent / 'outside', '', root=self.root, tracked=set())
        self.assertEqual(error, 'target outside repository')
        self.assertFalse(excluded)


if __name__ == '__main__':
    unittest.main()
