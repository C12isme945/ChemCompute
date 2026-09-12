"""Export a single-use invitation beside a verified public installer."""
from __future__ import annotations

import configparser
import io
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from chemcompute.common.security import secure_path
from chemcompute.online_update import check_updates, download_update, verified_file


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('邀请部署地址必须为不含凭据或查询参数的 HTTPS 地址。')
    if '\n' in url or '\r' in url:
        raise ValueError('地址格式无效。')
    return url.rstrip('/')


def write_bundle(destination, installer, release, url, invite, media=None, licensed=False):
    url = validate_url(url)
    if not re.fullmatch(r'cc-inv-[A-Za-z0-9_-]{32}', invite['invite_code']):
        raise ValueError('一次性邀请码格式无效。')
    if not verified_file(installer, release):
        raise ValueError('安装包校验失败。')
    media = media or {}
    if media and not licensed:
        raise ValueError('请确认安装介质可用于受邀电脑。')
    for name, source in media.items():
        if name not in {'Gaussian-Setup.exe', 'GaussView-Setup.exe'} or not Path(source).is_file() or Path(source).suffix.lower() != '.exe':
            raise ValueError('请选择有效的 Gaussian / GaussView EXE 安装程序。')
    config = configparser.ConfigParser(interpolation=None)
    config['join'] = {'url': url, 'invite': invite['invite_code'], 'expires': invite['expires_at']}
    text = io.StringIO()
    config.write(text)
    destination = Path(destination)
    temporary = destination.with_suffix('.partial')
    if temporary.exists():
        raise ValueError('导出临时文件已存在，请选择另一文件名。')
    try:
        temporary.touch(exist_ok=False)
        if not secure_path(temporary):
            raise RuntimeError('无法保护含邀请码的部署包。')
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(installer, 'ChemCompute-Setup.exe')
            archive.writestr('chemcompute-join.ini', text.getvalue().encode('utf-16'))
            for name, source in media.items():
                archive.write(source, name)
            archive.writestr('请先阅读.txt', f'请完整解压 ZIP 后运行 ChemCompute-Setup.exe。\n确认加入服务器：{url}\n安装器已预填一次性邀请码，默认登录时启动，安装结束后自动连接。\n有效期至 {invite["expires_at"]}，仅供一台电脑使用，请勿公开上传。\n关闭操控台不会停止后台。需要退出协作时点击停止后台。\nWSL/驱动可能需要管理员确认和重启。Gaussian 使用本机已有授权安装或在依赖安装页选择正式安装介质。\n此包不包含 Gaussian、许可证或管理员密钥。\n'.encode('utf-8-sig'))
        temporary.replace(destination)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_bundle(destination, url, hours=24, note='', media=None, licensed=False):
    from chemcompute.desktop_client import call
    validate_url(url)
    # Include the current version; normal updater selection excludes it.
    release = check_updates(current_version='0.0.0')
    if not release or tuple(int(n) for n in release['version'].split('.')[:2]) < (0, 6):
        raise RuntimeError('专属部署包需要已发布的 0.6 或更高版本安装器。')
    installer = Path('updates') / f'ChemCompute-{release["version"]}-Setup.exe'
    if not verified_file(installer, release):
        download_update(release, installer)
    invite = call('POST', '/api/v1/invites', json={'expires_in_hours': hours, 'note': note})
    return write_bundle(destination, installer, release, url, invite, media, licensed)
