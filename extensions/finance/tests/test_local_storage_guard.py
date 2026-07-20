from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


FINANCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FINANCE_ROOT))

from src.local_storage_guard import StoragePolicyViolation, validate_runtime_path


class LocalStorageGuardTests(unittest.TestCase):
    def test_accepts_local_path_outside_configured_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            external_root = Path(temporary_directory) / "local-finance-data"
            repository_root = Path(temporary_directory) / "source-repository"
            repository_root.mkdir()

            actual = validate_runtime_path(external_root, repository_roots=[repository_root])

            self.assertEqual(actual, external_root.resolve())

    def test_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(StoragePolicyViolation, "FINANCE_STORAGE_PATH_NOT_ABSOLUTE"):
            validate_runtime_path("finance-data")

    def test_rejects_configured_repository_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory) / "repository"
            runtime_path = repository_root / "runtime-data"
            repository_root.mkdir()

            with self.assertRaisesRegex(StoragePolicyViolation, "FINANCE_STORAGE_INSIDE_GIT_REPOSITORY"):
                validate_runtime_path(runtime_path, repository_roots=[repository_root])

    def test_rejects_discovered_git_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory) / "repository"
            (repository_root / ".git").mkdir(parents=True)

            with self.assertRaisesRegex(StoragePolicyViolation, "FINANCE_STORAGE_INSIDE_GIT_REPOSITORY"):
                validate_runtime_path(repository_root / "finance")

    def test_rejects_cloud_sync_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cloud_path = Path(temporary_directory) / "Dropbox" / "finance"

            with self.assertRaisesRegex(StoragePolicyViolation, "FINANCE_CLOUD_SYNC_STORAGE_BLOCKED"):
                validate_runtime_path(cloud_path)

    def test_rejects_known_network_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            network_root = Path(temporary_directory) / "mounted-share"

            with self.assertRaisesRegex(StoragePolicyViolation, "FINANCE_NETWORK_STORAGE_BLOCKED"):
                validate_runtime_path(network_root / "finance", known_network_roots=[network_root])


if __name__ == "__main__":
    unittest.main()

