import hashlib
import importlib.util
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = REPO_ROOT / "scripts/build_pinned_runtime.py"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_pinned_runtime", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load pinned runtime builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()


class PinnedRuntimeBuilderTests(unittest.TestCase):
    def _git(self, source: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(source), *args],
            text=True,
        ).strip()

    def _source_repo(self, root: Path) -> tuple[Path, str]:
        source = root / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        self._git(source, "config", "user.name", "Synthetic Test")
        self._git(source, "config", "user.email", "synthetic@example.invalid")

        package = source / "openhealth"
        package.mkdir()
        (package / "__init__.py").write_text('"""Synthetic package."""\n', encoding="utf-8")
        (package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

        for relative in BUILDER.REQUIRED_FILES:
            path = source / relative.as_posix()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".sh":
                content = "#!/usr/bin/env bash\nexit 0\n"
            elif path.suffix == ".plist":
                content = "<?xml version=\"1.0\"?><plist version=\"1.0\"><dict/></plist>\n"
            else:
                content = "#!/usr/bin/env python3\nVALUE = 1\n"
            path.write_text(content, encoding="utf-8")

        for path in source.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                path.chmod(0o755)

        self._git(source, "add", "openhealth", "scripts", "ui")
        self._git(source, "commit", "-q", "-m", "synthetic committed runtime")
        revision = self._git(source, "rev-parse", "HEAD")

        (source / ".env").write_text("PRIVATE_TOKEN=must-not-archive\n", encoding="utf-8")
        private_data = source / "data/index/private.json"
        private_data.parent.mkdir(parents=True)
        private_data.write_text('{"private":true}\n', encoding="utf-8")
        return source, revision

    def test_build_is_allowlisted_owner_only_manifested_and_repeat_verifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, revision = self._source_repo(root)
            releases_root = root / "runtime-releases"

            release, resolved, manifest = BUILDER.build_release(source, releases_root, revision)
            self.assertEqual(resolved, revision)
            self.assertEqual(release, releases_root / revision)
            self.assertEqual(stat.S_IMODE(releases_root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(release.stat().st_mode), 0o500)
            self.assertFalse((release / ".env").exists())
            self.assertFalse((release / "data").exists())

            actual_payload = {
                path.relative_to(release).as_posix()
                for path in release.rglob("*")
                if path.is_file() and path.name != "MANIFEST.json"
            }
            self.assertEqual(set(manifest["files"]), actual_payload)
            self.assertEqual(manifest["revision"], revision)
            self.assertEqual(manifest["schema_version"], BUILDER.SCHEMA_VERSION)

            for path in release.rglob("*"):
                relative = path.relative_to(release).as_posix()
                self.assertFalse(path.is_symlink(), relative)
                if path.is_dir():
                    expected_mode = 0o500
                elif relative in {item.as_posix() for item in BUILDER.EXECUTABLE_FILES}:
                    expected_mode = 0o500
                else:
                    expected_mode = 0o400
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected_mode, relative)

            for relative, metadata in manifest["files"].items():
                payload = release / relative
                self.assertEqual(metadata["size"], payload.stat().st_size)
                self.assertEqual(
                    metadata["sha256"],
                    hashlib.sha256(payload.read_bytes()).hexdigest(),
                )

            first_verification = BUILDER.verify_release(release, revision)
            second_verification = BUILDER.verify_release(release, revision)
            self.assertEqual(first_verification, second_verification)
            same_release, same_revision, same_manifest = BUILDER.build_release(
                source,
                releases_root,
                revision,
            )
            self.assertEqual((same_release, same_revision), (release, revision))
            self.assertEqual(same_manifest, manifest)

    def test_existing_release_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, revision = self._source_repo(root)
            releases_root = root / "runtime-releases"
            release, _, _ = BUILDER.build_release(source, releases_root, revision)
            target = release / "openhealth/module.py"
            target.chmod(0o600)
            target.write_text("VALUE = 999\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "manifest does not match"):
                BUILDER.verify_release(release, revision)
            with self.assertRaisesRegex(RuntimeError, "manifest does not match"):
                BUILDER.build_release(source, releases_root, revision)

    def test_committed_symlink_in_allowlisted_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _ = self._source_repo(root)
            (source / "openhealth/unsafe-link").symlink_to("/tmp/outside-runtime")
            self._git(source, "add", "openhealth/unsafe-link")
            self._git(source, "commit", "-q", "-m", "synthetic unsafe symlink")
            revision = self._git(source, "rev-parse", "HEAD")

            with self.assertRaisesRegex(RuntimeError, "unsafe runtime archive member"):
                BUILDER.build_release(source, root / "runtime-releases", revision)

    def test_manifest_json_has_no_untracked_or_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, revision = self._source_repo(root)
            release, _, _ = BUILDER.build_release(source, root / "runtime-releases", revision)
            manifest = json.loads((release / "MANIFEST.json").read_text(encoding="utf-8"))
            for relative in manifest["files"]:
                self.assertFalse(Path(relative).is_absolute())
                self.assertNotIn("..", Path(relative).parts)
                self.assertNotIn("private", relative)

    def test_relative_releases_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, revision = self._source_repo(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "must be an absolute path"):
                BUILDER.build_release(source, Path("runtime-releases"), revision)


if __name__ == "__main__":
    unittest.main()
