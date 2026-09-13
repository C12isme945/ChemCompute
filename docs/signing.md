# 1.0 正式版签名与信任限制

本次发布使用维护者本机生成的 **RSA 3072 / SHA-256 自签名代码签名证书**。它用于核对签名者和文件完整性，**不是 CA 认证的发行者身份**。新电脑仍可能显示未知发行者、证书不受信任或 SmartScreen 提示。

- 证书主题：`CN=ChemCompute Community Release`
- SHA-1 证书指纹（标识证书，不是安装包散列）：`1EFD02C1EC474A5BEC00FAA3C4EF64887AAF391B`
- 有效期至 2028-09-13；未使用可信时间戳，证书过期后验证状态可能变化。
- 签名对象：`ChemCompute.exe`、`ChemCompute-Setup.exe`。第三方运行库及 Inno 生成的卸载器不另行使用此证书签名。
- Release 附带 `ChemCompute-SelfSigned.cer` 公钥证书和 `SHA256SUMS.txt`。不分发私钥，程序不修改 Windows 根证书信任库。
- GitHub Actions 的构建产物未签名，用于可复现流程和测试。正式 Release 使用维护者本机签名后的安装包，CI 不以未签名文件替换它。

## 下载核对

仅从 [官方仓库 Releases](https://github.com/C12isme945/ChemCompute/releases) 获取文件。对照同一 Release 的 SHA-256，再查看签名：

```powershell
Get-FileHash .\ChemCompute-Setup.exe -Algorithm SHA256
Get-AuthenticodeSignature .\ChemCompute-Setup.exe | Format-List Status,StatusMessage,SignerCertificate
```

未信任自签名证书的机器可能返回 `UnknownError` 并说明证书链终止于不受信任的根，或返回 `NotTrusted`。这与 `HashMismatch`（内容变化）不同。**不要为了消除警告关闭安全软件或批量导入根证书**；组织需要默认受信任发行者时，应改用 CA/企业签名服务。

## 维护者构建

```powershell
.\scripts\build.ps1 -Python .\.venv\Scripts\python.exe -ISCC 'C:\path\ISCC.exe' -CertificateThumbprint '<本机代码签名证书指纹>'
```

构建脚本先签主程序，再编译和签安装包，最后生成校验值。`sign-file.ps1` 验证签名证书、CMS 密码学签名和 Windows 验证状态。私钥保留在当前用户证书存储；不写入仓库或 Actions。

自签名信任边界参考 [Microsoft 签名说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_signing)。
