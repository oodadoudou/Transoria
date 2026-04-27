from pathlib import Path
from unittest import TestCase


class ScaffoldTests(TestCase):
    def test_backend_package_imports(self) -> None:
        import transoria

        self.assertTrue(transoria.__doc__)

    def test_sample_fixtures_are_available(self) -> None:
        fixture_dir = Path("test/test-files")

        self.assertTrue(fixture_dir.is_dir())
        self.assertTrue(any(path.suffix.lower() == ".epub" for path in fixture_dir.iterdir()))
        self.assertTrue(any(path.suffix.lower() == ".txt" for path in fixture_dir.iterdir()))

