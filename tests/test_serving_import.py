"""Le serving garde son jeu de dependances, meme avec le lecteur Gold."""

import subprocess
import sys


def test_api_import_does_not_require_pipeline_dependencies():
    code = """
import sys
class NoPipeline:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'duckdb', 'pandera'}:
            raise ImportError('pipeline dependency unexpectedly imported: ' + fullname)
sys.meta_path.insert(0, NoPipeline())
import velov.api.main
"""
    result = subprocess.run(  # noqa: S603 -- interpreter et script de test locaux
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
