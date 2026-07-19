import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class ForgeStandaloneLoadingTests(unittest.TestCase):
    def test_helper_scripts_load_without_package_context(self):
        paths = [
            ROOT / "scripts" / "cache_db.py",
            ROOT / "scripts" / "natural_language.py",
            ROOT / "scripts" / "natural_prompt_schema.py",
        ]
        loader_script = """
import importlib.util
import os
import sys

for path in sys.argv[1:]:
    spec = importlib.util.spec_from_file_location(os.path.basename(path), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
"""
        result = subprocess.run(
            [sys.executable, "-c", loader_script, *map(str, paths)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
