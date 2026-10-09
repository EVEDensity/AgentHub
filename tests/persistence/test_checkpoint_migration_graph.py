from __future__ import annotations

import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


class CheckpointMigrationGraphTests(unittest.TestCase):
    def test_original_checkpoint_revision_and_new_resume_head_are_distinct(
        self,
    ) -> None:
        root = Path(__file__).parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        scripts = ScriptDirectory.from_config(config)

        self.assertEqual(scripts.get_heads(), ["b7e1f203c4d5"])
        original = scripts.get_revision("a6d0e1f2b3c4")
        resumed = scripts.get_revision("b7e1f203c4d5")
        self.assertEqual(original.down_revision, "f5c9d0e1a2b3")
        self.assertEqual(resumed.down_revision, original.revision)
        self.assertEqual(
            [
                revision.revision
                for revision in scripts.iterate_revisions("head", original.revision)
            ],
            [resumed.revision],
        )
