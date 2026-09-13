param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$CertificateThumbprint
)
$ErrorActionPreference = 'Stop'
if ($CertificateThumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'Invalid certificate thumbprint' }
$certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint"
if (-not $certificate.HasPrivateKey) { throw 'Signing certificate has no local private key' }
if ($certificate.NotAfter -le (Get-Date)) { throw 'Signing certificate has expired' }
$file = (Resolve-Path -LiteralPath $Path).Path
$result = Set-AuthenticodeSignature -LiteralPath $file -Certificate $certificate -HashAlgorithm SHA256
$verified = Get-AuthenticodeSignature -LiteralPath $file
if (-not $verified.SignerCertificate -or $verified.SignerCertificate.Thumbprint -ne $CertificateThumbprint) { throw 'Unexpected signer after signing' }
# A self-signed certificate deliberately remains outside trusted root stores.
if ($verified.Status -notin @('Valid', 'NotTrusted', 'UnknownError')) { throw "Signature failed: $($verified.Status): $($verified.StatusMessage)" }
# Independently verify the CMS signature. This does not grant OS publisher trust.
Add-Type -AssemblyName System.Security
$bytes = [IO.File]::ReadAllBytes($file)
$pe = [BitConverter]::ToInt32($bytes, 0x3c)
$optional = $pe + 24
$magic = [BitConverter]::ToUInt16($bytes, $optional)
$directory = if ($magic -eq 0x20b) { $optional + 112 } elseif ($magic -eq 0x10b) { $optional + 96 } else { throw 'Not a supported PE image' }
$offset = [BitConverter]::ToInt32($bytes, $directory + 32)
$length = [BitConverter]::ToInt32($bytes, $offset)
$cmsBytes = New-Object byte[] ($length - 8)
[Array]::Copy($bytes, $offset + 8, $cmsBytes, 0, $cmsBytes.Length)
$cms = New-Object System.Security.Cryptography.Pkcs.SignedCms
$cms.Decode($cmsBytes)
$cms.CheckSignature($true)
Write-Output "Signed: $([IO.Path]::GetFileName($file)); CMS signature verified; Windows trust: $($verified.Status)"
