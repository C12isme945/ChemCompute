r"""ChemCompute 安全计算包处理模块 (Safe ZIP Archive Management)

核心安全与处理规范:
1. 严格限制包体积与展开体积: MAX_ARCHIVE_BYTES=100MB, MAX_EXPANDED_BYTES=512MB
2. 防路径穿越: 严格拒绝 '..', 绝对路径, Windows 驱动器盘符 (C:), UNC 路径 (//)
3. 防路径注入: 拒绝反斜杠 (\), 冒号 (ADS 数据流), Windows 保留设备名称 (CON, PRN, AUX, NUL, COM*, LPT*)
4. 防规范化缺陷: 拒绝路径段尾随点号或前后空格, 严格校验大小写不敏感重名
5. 防 Zip Bomb: 压缩比上限 200, 最大文件数 2000, 流式内存受限校验
6. 防提权与符号链接: 拒绝归档内符号链接, 拒绝目标目录符号链接及 pre-existing 文件
7. CRC 与完整性: 边流式校验边提取, 异常时立即终止并原子回滚, 不留外部/残余文件
8. 清单安全: chemcompute.json 仅支持纯 JSON 对象且限 64KB, 不执行任何代码
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path

# 接口常量规范
MAX_ARCHIVE_BYTES: int = 100 * 1024 * 1024  # 100 MB
MAX_EXPANDED_BYTES: int = 512 * 1024 * 1024  # 512 MB
MAX_FILE_COUNT: int = 2000  # 最大允许文件数量
MAX_FILES: int = MAX_FILE_COUNT  # 常量别名
MAX_COMPRESSION_RATIO: float = 200.0  # 压缩比阈值
MAX_MANIFEST_BYTES: int = 64 * 1024  # 64 KB
MANIFEST_FILENAME: str = "chemcompute.json"
CHUNK_SIZE: int = 64 * 1024  # 64 KB 流式读取块大小

# Windows 保留设备名 (全大写匹配)
RESERVED_NAMES: frozenset[str] = frozenset({
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM0",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT0",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
})

# 非法文件名字符集合
INVALID_NAME_CHARS: frozenset[str] = frozenset({"<", ">", ":", '"', "|", "?", "*", "\\"})


class PackageError(ValueError):
    """计算包处理错误 (校验失败、安全违规、格式异常或损坏)"""


def sha256_file(path: Path | str) -> str:
    """以流式受限内存计算文件的 SHA-256 十六进制散列值"""
    p = Path(path)
    if not p.is_file():
        raise PackageError(f"File not found or not a regular file: {p}")
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def _validate_entry_name(name: str) -> None:
    """严格校验归档内部路径名称的安全合规性"""
    if not name:
        raise PackageError("Archive entry filename cannot be empty")

    # 1. 拒绝反斜杠 (ZIP 标准规范要求正斜杠 /)
    if "\\" in name:
        raise PackageError(f"Backslashes are not permitted in archive paths: '{name}'")

    # 2. 拒绝冒号 (NTFS ADS 数据流及 Windows 驱动器盘符注入)
    if ":" in name:
        raise PackageError(f"Colons (ADS / drive prefix) are not permitted in archive paths: '{name}'")

    # 3. 拒绝以正斜杠开头的绝对路径或 UNC 路径
    if name.startswith("/") or name.startswith("//"):
        raise PackageError(f"Absolute or UNC path not permitted: '{name}'")

    # 4. 拒绝 Windows 盘符形式 (e.g. C:...)
    if re.match(r"^[a-zA-Z]:", name):
        raise PackageError(f"Windows drive prefix not permitted: '{name}'")

    # 5. 路径段拆解校验
    parts = name.split("/")
    if name.endswith("/"):
        parts = parts[:-1]

    for part in parts:
        if not part or part == ".":
            raise PackageError(f"Invalid empty or dot path segment in '{name}'")
        if part == "..":
            raise PackageError(f"Path traversal segment '..' not permitted: '{name}'")
        if part.endswith(".") or part.endswith(" ") or part.startswith(" "):
            raise PackageError(
                f"Path segment '{part}' has trailing dot or leading/trailing whitespace in '{name}'"
            )
        if any(c in INVALID_NAME_CHARS or ord(c) < 32 for c in part):
            raise PackageError(f"Path segment '{part}' contains invalid characters in '{name}'")

        stem = part.split(".")[0].upper()
        if stem in RESERVED_NAMES:
            raise PackageError(
                f"Path segment '{part}' uses reserved Windows device name '{stem}' in '{name}'"
            )


def _validate_entry_info(entry: zipfile.ZipInfo) -> None:
    """校验单条 ZipInfo 元数据"""
    _validate_entry_name(entry.orig_filename)
    _validate_entry_name(entry.filename)

    # 检查符号链接属性
    mode = entry.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        raise PackageError(f"Symlinks are not permitted in package: '{entry.filename}'")
    if (entry.external_attr >> 28) == 0xA:
        raise PackageError(f"Symlink attribute detected in package: '{entry.filename}'")

    # 检查加密
    if (entry.flag_bits & 0x1) != 0:
        raise PackageError(f"Encrypted entries are not permitted: '{entry.filename}'")

    # 检查单文件申报体积
    if entry.file_size > MAX_EXPANDED_BYTES:
        raise PackageError(
            f"Entry '{entry.filename}' declared uncompressed size {entry.file_size} exceeds {MAX_EXPANDED_BYTES}"
        )
    if entry.compress_size > MAX_ARCHIVE_BYTES:
        raise PackageError(
            f"Entry '{entry.filename}' declared compressed size {entry.compress_size} exceeds {MAX_ARCHIVE_BYTES}"
        )

    # 检查单文件压缩比 (避免单体 zip bomb)
    if entry.file_size > 1024:
        compressed = max(entry.compress_size, 1)
        ratio = entry.file_size / compressed
        if ratio > MAX_COMPRESSION_RATIO:
            raise PackageError(
                f"Compression ratio too high ({ratio:.1f} > {MAX_COMPRESSION_RATIO}) for '{entry.filename}'"
            )


def inspect_package(path: Path | str) -> dict:
    """校验并检查计算包合法性，返回包内文件列表、展开总字节数及元数据清单。

    Returns:
        {'files': [relative names], 'size_bytes': expanded, 'manifest': dict | None}
    """
    p = Path(path)
    if not p.is_file():
        raise PackageError(f"Package file not found or is not a regular file: {p}")

    archive_size = p.stat().st_size
    if archive_size > MAX_ARCHIVE_BYTES:
        raise PackageError(
            f"Archive file size {archive_size} exceeds maximum {MAX_ARCHIVE_BYTES} bytes"
        )

    try:
        zf = zipfile.ZipFile(p, "r")
    except zipfile.BadZipFile as e:
        raise PackageError(f"Invalid or corrupted ZIP archive: {e}") from e

    with zf:
        infolist = zf.infolist()
        if len(infolist) > MAX_FILE_COUNT:
            raise PackageError(
                f"Archive contains {len(infolist)} entries, exceeding maximum limit {MAX_FILE_COUNT}"
            )

        total_expanded = sum(e.file_size for e in infolist)
        if total_expanded > MAX_EXPANDED_BYTES:
            raise PackageError(
                f"Archive total uncompressed size {total_expanded} exceeds maximum limit {MAX_EXPANDED_BYTES}"
            )

        total_compressed = sum(e.compress_size for e in infolist)
        if total_expanded > 1024 and (total_expanded / max(total_compressed, 1)) > MAX_COMPRESSION_RATIO:
            raise PackageError(
                f"Overall compression ratio too high: {total_expanded / max(total_compressed, 1):.1f} > {MAX_COMPRESSION_RATIO}"
            )

        # 校验条目格式及大小写不敏感重名
        seen_names: set[str] = set()
        for entry in infolist:
            _validate_entry_info(entry)
            norm = entry.filename.rstrip("/").lower()
            if norm in seen_names:
                raise PackageError(
                    f"Case-insensitive duplicate entry found: '{entry.filename}'"
                )
            seen_names.add(norm)

        # 流式校验 CRC 及实际解压大小 (受限内存 CHUNK_SIZE)
        total_streamed = 0
        for entry in infolist:
            if entry.is_dir() or entry.filename.endswith("/"):
                continue
            entry_streamed = 0
            try:
                with zf.open(entry, "r") as f:
                    while chunk := f.read(CHUNK_SIZE):
                        chunk_len = len(chunk)
                        entry_streamed += chunk_len
                        total_streamed += chunk_len
                        if entry_streamed > entry.file_size:
                            raise PackageError(
                                f"Decompressed size of '{entry.filename}' exceeded declared size ({entry_streamed} > {entry.file_size})"
                            )
                        if total_streamed > MAX_EXPANDED_BYTES:
                            raise PackageError(
                                f"Total decompressed size exceeded maximum limit {MAX_EXPANDED_BYTES} bytes"
                            )
                        if (
                            entry_streamed > 1024
                            and (entry_streamed / max(entry.compress_size, 1)) > MAX_COMPRESSION_RATIO
                        ):
                            raise PackageError(
                                f"Decompression ratio for '{entry.filename}' exceeded {MAX_COMPRESSION_RATIO}"
                            )
            except zipfile.BadZipFile as e:
                raise PackageError(f"CRC check or format error in '{entry.filename}': {e}") from e
            except Exception as e:
                if isinstance(e, PackageError):
                    raise
                raise PackageError(f"Failed reading entry '{entry.filename}': {e}") from e

            if entry_streamed != entry.file_size:
                raise PackageError(
                    f"Decompressed size of '{entry.filename}' ({entry_streamed}) does not match declared size ({entry.file_size})"
                )

        # 读取并解析根清单 chemcompute.json
        manifest = None
        manifest_entry = None
        for entry in infolist:
            if entry.filename.lower() == MANIFEST_FILENAME.lower():
                if entry.filename != MANIFEST_FILENAME:
                    raise PackageError(
                        f"Manifest filename must be exact '{MANIFEST_FILENAME}', found '{entry.filename}'"
                    )
                manifest_entry = entry
                break

        if manifest_entry is not None:
            if manifest_entry.file_size > MAX_MANIFEST_BYTES:
                raise PackageError(
                    f"Manifest '{MANIFEST_FILENAME}' declared size {manifest_entry.file_size} exceeds {MAX_MANIFEST_BYTES} bytes"
                )
            try:
                raw_manifest = zf.read(manifest_entry)
            except Exception as e:
                raise PackageError(f"Failed to read manifest: {e}") from e

            if len(raw_manifest) > MAX_MANIFEST_BYTES:
                raise PackageError(
                    f"Manifest '{MANIFEST_FILENAME}' size {len(raw_manifest)} exceeds {MAX_MANIFEST_BYTES} bytes"
                )
            try:
                data = json.loads(raw_manifest.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise PackageError(f"Manifest '{MANIFEST_FILENAME}' is not valid JSON: {e}") from e
            if not isinstance(data, dict):
                raise PackageError(
                    f"Manifest '{MANIFEST_FILENAME}' must be a JSON dictionary, got {type(data).__name__}"
                )
            manifest = data

        files = [
            entry.filename
            for entry in infolist
            if not entry.is_dir() and not entry.filename.endswith("/")
        ]
        size_bytes = sum(
            entry.file_size
            for entry in infolist
            if not entry.is_dir() and not entry.filename.endswith("/")
        )

        return {
            "files": files,
            "size_bytes": size_bytes,
            "manifest": manifest,
        }


def extract_package(path: Path | str, destination: Path | str) -> dict:
    """安全提取计算包到指定目录，拒绝覆盖现有文件与穿越符号链接，异常时回滚。

    Returns:
        与 inspect_package 相同的 dict: {'files': [...], 'size_bytes': ..., 'manifest': ...}
    """
    # 1. 严格检查并验证计算包 (CRC、体积限制、路径安全与清单)
    info = inspect_package(path)

    dest = Path(destination)

    # 2. 检查 destination 及其祖先路径是否包含符号链接
    if dest.is_symlink():
        raise PackageError(f"Destination is a symlink: {dest}")

    curr_ancestor = dest
    while True:
        if curr_ancestor.is_symlink():
            raise PackageError(f"Destination or ancestor is a symlink: {curr_ancestor}")
        if curr_ancestor.parent == curr_ancestor:
            break
        curr_ancestor = curr_ancestor.parent

    # 3. 如果目标目录已存在，检查是否已有已有文件 (防利用已有符号链接穿越)
    if dest.exists():
        if not dest.is_dir():
            raise PackageError(f"Destination exists and is not a directory: {dest}")
        if any(dest.iterdir()):
            raise PackageError(f"Destination directory already contains existing files: {dest}")
    else:
        dest.mkdir(parents=True, exist_ok=True)

    dest_resolved = dest.resolve()

    # 4. 执行解压，严格防范越界与写入冲突，失败时完整清理回滚
    created_files: list[Path] = []
    created_dirs: list[Path] = []

    try:
        with zipfile.ZipFile(path, "r") as zf:
            for entry in zf.infolist():
                rel_parts = entry.filename.split("/")

                if entry.is_dir() or entry.filename.endswith("/"):
                    cur_dir = dest
                    for part in rel_parts:
                        if not part:
                            continue
                        cur_dir = cur_dir / part
                        if cur_dir.is_symlink():
                            raise PackageError(f"Directory segment is a symlink: {cur_dir}")
                        if not cur_dir.exists():
                            cur_dir.mkdir(parents=False, exist_ok=False)
                            created_dirs.append(cur_dir)
                        elif not cur_dir.is_dir():
                            raise PackageError(
                                f"Directory conflict with non-directory: {cur_dir}"
                            )
                    continue

                # 常规文件
                cur_dir = dest
                for part in rel_parts[:-1]:
                    cur_dir = cur_dir / part
                    if cur_dir.is_symlink():
                        raise PackageError(f"Directory segment is a symlink: {cur_dir}")
                    if not cur_dir.exists():
                        cur_dir.mkdir(parents=False, exist_ok=False)
                        created_dirs.append(cur_dir)
                    elif not cur_dir.is_dir():
                        raise PackageError(f"Directory conflict with non-directory: {cur_dir}")

                target_file = cur_dir / rel_parts[-1]
                if target_file.exists() or target_file.is_symlink():
                    raise PackageError(f"Target file already exists: {target_file}")

                target_resolved = target_file.parent.resolve() / target_file.name
                if (
                    not str(target_resolved).startswith(str(dest_resolved) + os.sep)
                    and target_resolved != dest_resolved
                ):
                    raise PackageError(f"Target path escapes destination: {entry.filename}")

                with zf.open(entry, "r") as src, open(target_file, "wb") as dst:
                    created_files.append(target_file)
                    while chunk := src.read(CHUNK_SIZE):
                        dst.write(chunk)

    except Exception:
        # 回滚清理已提取的文件与目录
        for f in reversed(created_files):
            try:
                if f.is_file() or f.is_symlink():
                    f.unlink(missing_ok=True)
            except OSError:
                pass
        for d in reversed(created_dirs):
            try:
                if d.is_dir() and not d.is_symlink():
                    d.rmdir()
            except OSError:
                pass
        raise

    return info


def pack_results(directory: Path | str, output: Path | str) -> None:
    """将指定目录下的所有常规文件压缩打包为安全的计算结果归档文件。

    自动排除输出文件本身，且执行完整的大小、数量与文件名安全检查。
    """
    if Path(directory).is_symlink():
        raise PackageError("Source directory is a symlink")
    dir_path = Path(directory).resolve()
    if not dir_path.is_dir():
        raise PackageError(f"Source directory does not exist or is not a directory: {directory}")
    if dir_path.is_symlink():
        raise PackageError(f"Source directory is a symlink: {directory}")

    out_path = Path(output).resolve()
    if out_path.is_dir():
        raise PackageError(f"Output path must be a file, not an existing directory: {output}")

    files_to_pack: list[tuple[Path, str]] = []
    total_expanded = 0
    seen_names: set[str] = set()

    for root, _dirs, files in os.walk(dir_path):
        if any((Path(root) / d).is_symlink() for d in _dirs):
            raise PackageError("Symlink directory found in results")
        root_path = Path(root)
        if root_path.is_symlink():
            raise PackageError(f"Symlink directory found in results: {root_path}")
        for file_name in files:
            file_path = root_path / file_name
            if file_path.is_symlink():
                raise PackageError(f"Symlink file found in results: {file_path}")
            if file_path.resolve() == out_path:
                continue

            if not file_path.is_file():
                continue

            rel_path = file_path.relative_to(dir_path)
            arcname = rel_path.as_posix()

            _validate_entry_name(arcname)
            lower_name = arcname.lower()
            if lower_name in seen_names:
                raise PackageError(
                    f"Duplicate filename (case-insensitive) in results: '{arcname}'"
                )
            seen_names.add(lower_name)

            file_size = file_path.stat().st_size
            total_expanded += file_size
            files_to_pack.append((file_path, arcname))

    if len(files_to_pack) > MAX_FILE_COUNT:
        raise PackageError(
            f"Too many files to pack: {len(files_to_pack)} exceeds limit {MAX_FILE_COUNT}"
        )
    if total_expanded > MAX_EXPANDED_BYTES:
        raise PackageError(
            f"Total uncompressed size {total_expanded} exceeds limit {MAX_EXPANDED_BYTES}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_out = out_path.with_name(f".tmp_{out_path.name}_{os.getpid()}")
    try:
        with zipfile.ZipFile(temp_out, "w", compression=zipfile.ZIP_STORED) as zf:
            for file_path, arcname in files_to_pack:
                zf.write(file_path, arcname=arcname)

        archive_size = temp_out.stat().st_size
        if archive_size > MAX_ARCHIVE_BYTES:
            raise PackageError(
                f"Generated archive size {archive_size} exceeds maximum {MAX_ARCHIVE_BYTES} bytes"
            )

        temp_out.replace(out_path)
    except Exception:
        if temp_out.exists():
            try:
                temp_out.unlink(missing_ok=True)
            except OSError:
                pass
        raise
