"""An actual built/installed wheel must retain every reviewed skill resource."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReviewedSkillWheelTests(unittest.TestCase):
    def test_installed_wheel_reads_all_three_packages_outside_checkout(self):
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, wheels, installed = root / 'source', root / 'wheels', root / 'installed'
            source.mkdir()
            shutil.copytree(repository / 'app', source / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for name in ('pyproject.toml', 'README.md', 'LICENSE'):
                shutil.copy2(repository / name, source / name)
            self.run_command([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                              '--no-index', '--disable-pip-version-check', '--wheel-dir', str(wheels), str(source)], root)
            wheel, = wheels.glob('agenthub_cli-*.whl')
            self.run_command([sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index',
                              '--disable-pip-version-check', '--target', str(installed), str(wheel)], root)
            script = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import app.services.reviewed_skills as skills
assert Path(skills.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
catalog = skills.load_bundled_catalog()
names = []
for meta in catalog.list_metadata():
    name = meta.name
    assert catalog.load(name).body
    assert catalog.read_resource(name, 'references/output.md').content
    names.append(name)
print(json.dumps(sorted(names)))
'''
            result = self.run_command([sys.executable, '-I', '-c', script, str(installed)], root)
            self.assertEqual(json.loads(result.stdout), ['code-review', 'repository-fix', 'source-traceable-research'])

    def run_command(self, command, cwd):
        result = subprocess.run(command, cwd=cwd, text=True, encoding='utf-8', capture_output=True,
                                timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-6000:])
        return result
