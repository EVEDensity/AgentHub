"""Real filesystem snapshot/patch boundaries for trusted-local experiments."""
from __future__ import annotations

import os
import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.services.trusted_tests import snapshot as snapshot_module
from app.services.trusted_tests.snapshot import (
    SnapshotError, SnapshotLimits, capture_candidate, freeze_snapshot,
    materialize_candidate, reconstruct_candidate, tree_digest, verify_snapshot,
)


class TrustedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo, self.storage = self.root / 'repo', self.root / 'storage'
        self.repo.mkdir(); self.storage.mkdir()
        (self.repo / 'code.py').write_text('answer = 1\n')

    def test_snapshot_contains_dirty_and_untracked_bytes_and_omits_known_secret_runtime_names(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        subprocess.run(['git', 'add', 'code.py'], cwd=self.repo, check=True)
        (self.repo / 'code.py').write_text('answer = 2\n')
        (self.repo / 'new.txt').write_text('untracked')
        (self.repo / '.ENV').write_text('sensitive')
        (self.repo / 'node_modules').mkdir()
        (self.repo / 'node_modules' / 'cache').write_text('runtime')
        with freeze_snapshot(self.repo, self.storage) as snapshot:
            self.assertEqual({file.path for file in snapshot.files}, {'code.py', 'new.txt'})
            self.assertEqual((snapshot.root / 'code.py').read_text(), 'answer = 2\n')
            captured = snapshot.root
        self.assertFalse(captured.exists())
        self.assertEqual((self.repo / '.ENV').read_text(), 'sensitive')

    def test_patch_reconstructs_text_binary_additions_and_deletions_without_touching_source(self):
        (self.repo / 'old.txt').write_text('delete me')
        with freeze_snapshot(self.repo, self.storage) as snapshot:
            candidate = materialize_candidate(snapshot, self.root / 'candidate')
            (candidate / 'code.py').write_text('answer = 3\n')
            (candidate / 'old.txt').unlink()
            (candidate / 'new.bin').write_bytes(b'\x00\xffbinary')
            patch = capture_candidate(snapshot, candidate)
            reconstructed = reconstruct_candidate(snapshot, patch, self.root / 'reconstructed')
            self.assertIn(b'diff --git', patch.patch_bytes)
            self.assertEqual(tree_digest(reconstructed), patch.candidate_snapshot_digest)
            self.assertEqual((reconstructed / 'new.bin').read_bytes(), b'\x00\xffbinary')
            self.assertFalse((reconstructed / 'old.txt').exists())
            self.assertEqual((self.repo / 'code.py').read_text(), 'answer = 1\n')

    def test_patch_or_frozen_snapshot_tampering_is_refused(self):
        with freeze_snapshot(self.repo, self.storage) as snapshot:
            candidate = materialize_candidate(snapshot, self.root / 'candidate')
            (candidate / 'code.py').write_text('changed')
            patch = capture_candidate(snapshot, candidate)
            with self.assertRaises(SnapshotError):
                reconstruct_candidate(snapshot, replace(patch, candidate_snapshot_digest='sha256:'+'0'*64), self.root / 'bad')
            (snapshot.root / 'code.py').write_text('tampered')
            with self.assertRaises(SnapshotError):
                verify_snapshot(snapshot)

    def test_links_special_files_and_overlap_are_refused(self):
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.repo, self.repo)
        os.link(self.repo / 'code.py', self.repo / 'hardlink.py')
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.repo, self.storage)

    def test_symlink_cannot_escape_snapshot(self):
        try:
            (self.repo / 'escape').symlink_to(self.storage, target_is_directory=True)
        except OSError:
            self.skipTest('host cannot create symlinks')
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.repo, self.storage)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO requires POSIX')
    def test_special_file_is_rejected_before_open(self):
        os.mkfifo(self.repo / 'pipe')
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.repo, self.storage)

    def test_file_count_and_byte_limits_are_enforced_and_cleanup_occurs(self):
        for limits in (SnapshotLimits(max_file_bytes=1), SnapshotLimits(max_files=0), SnapshotLimits(max_total_bytes=1)):
            with self.assertRaises(SnapshotError):
                freeze_snapshot(self.repo, self.storage, limits)
        self.assertEqual(list(self.storage.iterdir()), [])


class GitSnapshotProvenanceTests(unittest.TestCase):
    setUp = TrustedSnapshotTests.setUp
    """Use actual Git refs; source file identity and repository ancestry differ."""
    def git(self, *arguments):
        command = ['git', '-c', 'user.name=AgentHub Tests', '-c', 'user.email=tests@example.invalid',
                   '-c', 'commit.gpgsign=false', '-c', f'core.hooksPath={os.devnull}', *arguments]
        return subprocess.run(command, cwd=self.repo, check=True, capture_output=True).stdout.decode().strip()

    def commit_source(self):
        self.git('init', '-q')
        self.git('add', 'code.py')
        self.git('commit', '-qm', 'baseline')
        return self.git('rev-parse', 'HEAD')

    def test_non_git_and_unborn_are_distinct_explicit_null_baselines(self):
        with freeze_snapshot(self.repo, self.storage) as plain:
            self.assertEqual((plain.source_type, plain.baseline_commit), ('non-git', None))
            self.git('init', '-q')
            with freeze_snapshot(self.repo, self.storage) as unborn:
                self.assertEqual((unborn.source_type, unborn.baseline_commit), ('git-unborn', None))
                self.assertNotEqual(plain.digest, unborn.digest)
                self.assertEqual(plain.tree_digest, unborn.tree_digest)
                self.assertIsNone(json.loads(unborn.manifest_bytes)['baselineCommit'])

    def test_head_commit_and_dirty_untracked_bytes_share_one_snapshot_identity(self):
        baseline = self.commit_source()
        (self.repo / 'code.py').write_text('answer = 17\n')
        (self.repo / 'new.txt').write_text('untracked evidence')
        with freeze_snapshot(self.repo, self.storage) as snapshot:
            metadata = json.loads(snapshot.manifest_bytes)
            self.assertEqual((snapshot.source_type, snapshot.baseline_commit), ('git-commit', baseline))
            self.assertEqual(metadata['baselineCommit'], baseline)
            self.assertEqual(metadata['sourceType'], 'git-commit')
            self.assertEqual(metadata['format'], 'agenthub.source-snapshot.v2')
            self.assertEqual((snapshot.root / 'code.py').read_text(), 'answer = 17\n')
            self.assertEqual((snapshot.root / 'new.txt').read_text(), 'untracked evidence')
            self.assertEqual(tree_digest(snapshot.root), snapshot.tree_digest)

    def test_same_bytes_at_different_head_commits_have_distinct_provenance(self):
        first_commit = self.commit_source()
        with freeze_snapshot(self.repo, self.storage) as first:
            self.git('commit', '--allow-empty', '-qm', 'another baseline')
            with freeze_snapshot(self.repo, self.storage) as second:
                self.assertNotEqual(first.baseline_commit, second.baseline_commit)
                self.assertEqual(first.baseline_commit, first_commit)
                self.assertEqual(first.tree_digest, second.tree_digest)
                self.assertNotEqual(first.digest, second.digest)
                # Moving the live source after capture does not rewrite history.
                verify_snapshot(first)

    def test_head_change_during_capture_is_refused_and_owned_storage_is_cleaned(self):
        self.commit_source()
        inventory = snapshot_module._inventory
        def changed_head(*arguments, **keywords):
            result = inventory(*arguments, **keywords)
            if len(arguments) >= 3 and arguments[2] is not None:
                self.git('commit', '--allow-empty', '-qm', 'racing baseline')
            return result
        with patch.object(snapshot_module, '_inventory', side_effect=changed_head):
            with self.assertRaisesRegex(SnapshotError, 'Git baseline changed'):
                freeze_snapshot(self.repo, self.storage)
        self.assertEqual(list(self.storage.iterdir()), [])

    def test_subdirectory_uses_its_actual_parent_repository_head(self):
        baseline = self.commit_source()
        package = self.repo / 'package'
        package.mkdir()
        (package / 'module.py').write_text('value = 3')
        with freeze_snapshot(package, self.storage) as snapshot:
            self.assertEqual(snapshot.baseline_commit, baseline)
            self.assertEqual([file.path for file in snapshot.files], ['module.py'])

    def test_corrupt_git_metadata_is_not_labeled_non_git(self):
        (self.repo / '.git').mkdir()
        with self.assertRaises(SnapshotError):
            freeze_snapshot(self.repo, self.storage)
        self.assertEqual(list(self.storage.iterdir()), [])

    def test_bare_repository_is_refused_instead_of_labeled_non_git(self):
        self.git('init', '--bare', '-q')
        with self.assertRaisesRegex(SnapshotError, 'not a working tree'):
            freeze_snapshot(self.repo, self.storage)
