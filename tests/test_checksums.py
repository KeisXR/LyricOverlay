import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "write_checksums.py"
spec = importlib.util.spec_from_file_location("checksums_under_test", MODULE_PATH)
checksums = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(checksums)


def test_generate_and_verify_are_deterministic(tmp_path):
    package = tmp_path / "package"
    (package / "sub").mkdir(parents=True)
    (package / "z.txt").write_text("z", encoding="utf-8")
    (package / "sub" / "a.txt").write_text("a", encoding="utf-8")

    manifest = checksums.generate(package)
    first = manifest.read_text(encoding="utf-8")
    checksums.generate(package)

    assert manifest.read_text(encoding="utf-8") == first
    assert first.splitlines()[0].endswith("  sub/a.txt")
    assert checksums.verify(package) == []


def test_verify_detects_modified_missing_and_unlisted_files(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    original = package / "original.txt"
    original.write_text("before", encoding="utf-8")
    checksums.generate(package)

    original.write_text("after", encoding="utf-8")
    (package / "extra.txt").write_text("extra", encoding="utf-8")

    failures = checksums.verify(package)

    assert "checksum mismatch: original.txt" in failures
    assert "unlisted file: extra.txt" in failures


def test_verify_rejects_unsafe_manifest_paths(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / checksums.CHECKSUM_FILE).write_text(
        f"{'0' * 64}  ../outside\n", encoding="utf-8"
    )

    assert checksums.verify(package) == ["unsafe path on line 1: ../outside"]
