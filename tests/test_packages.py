"""Unit tests for chemcompute/packages.py

Tests comprehensive security controls, malicious ZIP attacks, CRC verification,
manifest handling, safe extraction rollback, and packing of calculation packages.
"""

from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import pytest

from chemcompute.packages import (
    MAX_EXPANDED_BYTES,
    MAX_FILE_COUNT,
    MAX_MANIFEST_BYTES,
    PackageError,
    extract_package,
    inspect_package,
    pack_results,
    sha256_file,
)


def _create_zip(
    zip_path: Path,
    entries: dict[str | zipfile.ZipInfo, bytes | str],
    compression: int = zipfile.ZIP_STORED,
) -> Path:
    """Helper to create test zip archives."""
    with zipfile.ZipFile(zip_path, "w", compression=compression) as zf:
        for name_or_info, data in entries.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            if isinstance(name_or_info, zipfile.ZipInfo):
                zf.writestr(name_or_info, data)
            else:
                zf.writestr(name_or_info, data)
    return zip_path


class TestLegitimatePackages:
    """Tests for valid, benign calculation packages."""

    def test_inspect_and_extract_legitimate_package(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "valid_pkg.zip"
        dest_dir = tmp_path / "dest"

        manifest_data = {
            "calculation_id": "calc-001",
            "gromacs_version": "2024.1",
            "box_vectors": [5.0, 5.0, 5.0],
            "ignore_command": "rm -rf /",  # Should be treated as plain text, no code execution
        }

        _create_zip(
            zip_path,
            {
                "chemcompute.json": json.dumps(manifest_data),
                "topol.top": "[ molecules ]\nSol 100\n",
                "inputs/run.mdp": "integrator = md\nnsteps = 500\n",
            },
        )

        # 1. Inspect package
        info = inspect_package(zip_path)
        assert sorted(info["files"]) == ["chemcompute.json", "inputs/run.mdp", "topol.top"]
        assert info["size_bytes"] > 0
        assert info["manifest"] == manifest_data

        # 2. Extract package
        extract_info = extract_package(zip_path, dest_dir)
        assert extract_info == info

        # Verify extracted content
        assert (dest_dir / "chemcompute.json").is_file()
        assert (dest_dir / "topol.top").read_text(encoding="utf-8") == "[ molecules ]\nSol 100\n"
        assert (dest_dir / "inputs" / "run.mdp").read_text(encoding="utf-8") == "integrator = md\nnsteps = 500\n"

    def test_package_without_manifest(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "no_manifest.zip"
        _create_zip(zip_path, {"data.txt": "sample data"})

        info = inspect_package(zip_path)
        assert info["files"] == ["data.txt"]
        assert info["manifest"] is None


class TestManifestSecurity:
    """Tests for manifest parsing, size limits, and security."""

    def test_manifest_exceeding_64kb(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "oversized_manifest.zip"
        large_manifest = {"key": "x" * (MAX_MANIFEST_BYTES + 10)}
        _create_zip(zip_path, {"chemcompute.json": json.dumps(large_manifest)})

        with pytest.raises(PackageError, match="exceeds"):
            inspect_package(zip_path)

    def test_manifest_invalid_json(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "invalid_json_manifest.zip"
        _create_zip(zip_path, {"chemcompute.json": "{bad json content: "})

        with pytest.raises(PackageError, match="not valid JSON"):
            inspect_package(zip_path)

    def test_manifest_not_a_dict(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "list_manifest.zip"
        _create_zip(zip_path, {"chemcompute.json": json.dumps(["not", "a", "dict"])})

        with pytest.raises(PackageError, match="must be a JSON dictionary"):
            inspect_package(zip_path)


class TestMaliciousZipAttacks:
    """Tests for path traversal, drive prefix, UNC, symlinks, ADS, and Windows names."""

    def test_reject_zip_traversal_dot_dot(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "traversal.zip"
        _create_zip(zip_path, {"../escaped.txt": "evil"})

        with pytest.raises(PackageError, match="Path traversal"):
            inspect_package(zip_path)

    def test_reject_zip_traversal_nested(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "nested_traversal.zip"
        _create_zip(zip_path, {"sub/../../escaped.txt": "evil"})

        with pytest.raises(PackageError, match="Path traversal"):
            inspect_package(zip_path)

    def test_reject_absolute_path(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "abs_path.zip"
        _create_zip(zip_path, {"/etc/passwd": "evil"})

        with pytest.raises(PackageError, match="Absolute or UNC path"):
            inspect_package(zip_path)

    def test_reject_unc_path(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "unc_path.zip"
        _create_zip(zip_path, {"//server/share/evil.txt": "evil"})

        with pytest.raises(PackageError, match="Absolute or UNC path"):
            inspect_package(zip_path)

    def test_reject_windows_drive(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "drive.zip"
        _create_zip(zip_path, {"C:system32/evil.dll": "evil"})

        with pytest.raises(PackageError, match="Colons|Windows drive"):
            inspect_package(zip_path)

    def test_reject_backslashes(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "backslash.zip"
        _create_zip(zip_path, {"sub/file.txt": "evil"})
        zip_path.write_bytes(zip_path.read_bytes().replace(b"sub/file.txt", b"sub\\file.txt"))

        with pytest.raises(PackageError, match="Backslashes are not permitted"):
            inspect_package(zip_path)

    def test_reject_ads_colon(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "ads.zip"
        _create_zip(zip_path, {"test.txt:stream": "evil"})

        with pytest.raises(PackageError, match="Colons"):
            inspect_package(zip_path)

    def test_reject_symlink_unix(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "symlink_unix.zip"
        info = zipfile.ZipInfo("symlink_entry")
        info.create_system = 3  # Unix
        info.external_attr = 0o120777 << 16  # S_IFLNK
        _create_zip(zip_path, {info: "target_path"})

        with pytest.raises(PackageError, match="Symlinks are not permitted"):
            inspect_package(zip_path)

    @pytest.mark.parametrize(
        "reserved_name",
        ["CON", "con.txt", "prn.dat", "aux", "NUL.json", "com1.bin", "lpt2.txt", "sub/COM3"],
    )
    def test_reject_windows_reserved_device_names(self, tmp_path: Path, reserved_name: str) -> None:
        zip_path = tmp_path / f"reserved_{reserved_name.replace('/', '_')}.zip"
        _create_zip(zip_path, {reserved_name: "device data"})

        with pytest.raises(PackageError, match="reserved Windows device name"):
            inspect_package(zip_path)

    @pytest.mark.parametrize("bad_name", ["file.txt.", "file.txt ", " file.txt", "sub./test.txt", "sub /test.txt"])
    def test_reject_trailing_dot_or_space(self, tmp_path: Path, bad_name: str) -> None:
        zip_path = tmp_path / "bad_name.zip"
        _create_zip(zip_path, {bad_name: "test"})

        with pytest.raises(PackageError, match="trailing dot or leading/trailing whitespace"):
            inspect_package(zip_path)

    def test_reject_case_insensitive_duplicate(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "case_duplicate.zip"
        info1 = zipfile.ZipInfo("file.txt")
        info2 = zipfile.ZipInfo("FILE.TXT")
        _create_zip(zip_path, {info1: "one", info2: "two"})

        with pytest.raises(PackageError, match="Case-insensitive duplicate"):
            inspect_package(zip_path)

    def test_reject_encrypted_archive(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "encrypted.zip"
        info = zipfile.ZipInfo("secret.txt")
        info.flag_bits |= 0x1  # Encrypted flag
        _create_zip(zip_path, {info: "classified"})
        raw = bytearray(zip_path.read_bytes())
        struct.pack_into("<H", raw, 6, 1)
        struct.pack_into("<H", raw, raw.index(b"PK\x01\x02") + 8, 1)
        zip_path.write_bytes(raw)

        with pytest.raises(PackageError, match="Encrypted entries are not permitted"):
            inspect_package(zip_path)


class TestLimitsAndBombs:
    """Tests for file count, expansion limit, and zip bombs."""

    def test_reject_compression_bomb_ratio(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "bomb_ratio.zip"
        # 50KB of null bytes compressed with deflate compresses to ~50 bytes (ratio ~1000 > 200)
        data = b"\x00" * 50000
        _create_zip(zip_path, {"bomb.bin": data}, compression=zipfile.ZIP_DEFLATED)

        with pytest.raises(PackageError, match="[Cc]ompression ratio too high"):
            inspect_package(zip_path)

    def test_reject_too_many_files(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "too_many_files.zip"
        entries = {f"f{i}.txt": b"" for i in range(MAX_FILE_COUNT + 1)}
        _create_zip(zip_path, entries)

        with pytest.raises(PackageError, match="exceeding maximum limit"):
            inspect_package(zip_path)

    def test_reject_oversized_declared_expansion(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "oversized_declared.zip"
        info = zipfile.ZipInfo("huge.bin")
        info.file_size = MAX_EXPANDED_BYTES + 1024
        info.compress_size = 100
        _create_zip(zip_path, {info: b"small payload"})
        raw = bytearray(zip_path.read_bytes())
        struct.pack_into("<I", raw, raw.index(b"PK\x01\x02") + 24, MAX_EXPANDED_BYTES + 1024)
        zip_path.write_bytes(raw)

        with pytest.raises(PackageError, match="exceeds"):
            inspect_package(zip_path)


class TestCrcAndRollback:
    """Tests for CRC corruption detection and clean rollback without partial extraction."""

    def test_crc_mismatch_detection_and_no_partial_files(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "corrupt_crc.zip"
        dest_dir = tmp_path / "extract_dest"

        # Create valid zip first
        _create_zip(
            zip_path,
            {
                "good.txt": "good content",
                "corrupt.txt": "original content that will be tampered",
            },
            compression=zipfile.ZIP_STORED,
        )

        # Tamper with compressed data of 'corrupt.txt' without updating CRC in header
        raw = zip_path.read_bytes()
        tampered = raw.replace(b"original content that will be tampered", b"Xriginal content that will be tampered")
        assert tampered != raw
        zip_path.write_bytes(tampered)

        # inspect_package must detect CRC error
        with pytest.raises(PackageError, match="CRC check or format error"):
            inspect_package(zip_path)

        # extract_package must fail and leave no partial files in destination
        with pytest.raises(PackageError):
            extract_package(zip_path, dest_dir)

        if dest_dir.exists():
            assert not list(dest_dir.iterdir()), "Partial extracted files were left in destination"


class TestExtractionProtection:
    """Tests for destination symlink and existing file protections."""

    def test_reject_extraction_if_target_file_already_exists(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "pkg.zip"
        _create_zip(zip_path, {"file.txt": "content"})

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        (dest_dir / "file.txt").write_text("pre-existing", encoding="utf-8")

        with pytest.raises(PackageError, match="Destination directory already contains existing files"):
            extract_package(zip_path, dest_dir)

    def test_reject_extraction_to_symlink_destination(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        symlink_dest = tmp_path / "symlink_dir"

        try:
            symlink_dest.symlink_to(real_dir, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation requires elevated privileges on Windows")

        zip_path = tmp_path / "pkg.zip"
        _create_zip(zip_path, {"file.txt": "content"})

        with pytest.raises(PackageError, match="Destination is a symlink"):
            extract_package(zip_path, symlink_dest)


class TestPackResultsAndSha256:
    """Tests for pack_results and sha256_file functions."""

    def test_pack_results_excludes_output_and_enforces_safety(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "calc_output"
        src_dir.mkdir()

        (src_dir / "summary.json").write_text('{"status": "completed"}', encoding="utf-8")
        sub = src_dir / "trajectories"
        sub.mkdir()
        (sub / "traj.xtc").write_text("trajectory binary mock", encoding="utf-8")

        # Output archive placed directly inside the directory to verify exclusion
        archive_file = src_dir / "results.zip"

        pack_results(src_dir, archive_file)

        assert archive_file.is_file()

        # Inspect the packed results
        info = inspect_package(archive_file)
        assert "results.zip" not in info["files"]
        assert sorted(info["files"]) == ["summary.json", "trajectories/traj.xtc"]

    def test_sha256_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "data.bin"
        test_file.write_bytes(b"chemcompute-sha256-test")

        # echo -n "chemcompute-sha256-test" | sha256sum
        digest = sha256_file(test_file)
        assert len(digest) == 64
        assert digest == "a934c4012e87680aa93bd2ecfd7eb24f41ed8ac81d0419e7454738c476a4bd10"

        with pytest.raises(PackageError, match="File not found"):
            sha256_file(tmp_path / "non_existent_file.bin")
