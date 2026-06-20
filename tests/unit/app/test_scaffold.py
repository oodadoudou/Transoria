from pathlib import Path
from unittest import TestCase


class ScaffoldTests(TestCase):
    def test_backend_package_imports(self) -> None:
        import transoria

        self.assertTrue(transoria.__doc__)

    def test_sample_fixtures_are_available(self) -> None:
        fixture_dir = Path("tests/fixtures/public/translation_confidence")

        self.assertTrue(fixture_dir.is_dir())
        self.assertTrue((fixture_dir / "model_anomalies.json").is_file())
