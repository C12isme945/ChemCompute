"""Current-user Windows DPAPI encrypted credentials, outside installed binaries."""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from chemcompute.common.security import ensure_secure_directory, secure_path


def _crypt(raw: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Encrypted credential persistence requires Windows; use environment variables elsewhere')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def read_credentials() -> dict:
    path = Path('config/desktop-credentials.dpapi')
    if not path.exists():
        return {}
    return json.loads(_crypt(path.read_bytes(), decrypt=True))


def save_credentials(values: dict) -> None:
    path = Path('config/desktop-credentials.dpapi')
    ensure_secure_directory(path.parent)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(_crypt(json.dumps(values).encode()))
    secure_path(temporary)
    temporary.replace(path)


def deepseek_key() -> str:
    value = os.environ.get('DEEPSEEK_API_KEY') or read_credentials().get('deepseek_key', '')
    if not value:
        raise RuntimeError('请在设置中保存 DeepSeek API 密钥。')
    return value
