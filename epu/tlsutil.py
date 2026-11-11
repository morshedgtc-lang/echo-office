"""A local certificate, so the owner's laptop browser allows the camera and mic.

Why: a browser only opens the microphone/camera on a *secure context* — https, or
localhost. The office is served over plain http on the home network, so the laptop's
Chrome would refuse to record. https with a self-signed certificate turns that origin
secure; the browser shows one "not private" warning the owner accepts once, and then
the camera and the microphone work exactly like on the PC.

The certificate is made by Windows itself (New-SelfSignedCertificate), kept in
secure/ (owner-only, gitignored), and covers this PC's LAN IP, localhost and 127.0.0.1.
It is not a secret: a self-signed certificate protects the browser rules, not a
private key exchange. Nothing leaves this PC either way.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import config
from .config import ROOT

CERT_DIR = ROOT / "secure"
PFX = CERT_DIR / "office.pfx"
PFX_PASSWORD = "epu-local-office"          # the certificate is not a secret value
CER = CERT_DIR / "office.cer"


def _powershell(script: str) -> tuple[int, str]:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=120)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def ensure_certificate(extra_names: list[str] | None = None) -> Path | None:
    """A usable .pfx for this PC, made on first use. None when it cannot be made."""
    if PFX.exists():
        return PFX
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    names = {"localhost", "127.0.0.1"}
    for ip in _lan_ips():
        names.add(ip)
    for name in (extra_names or []):
        names.add(name)
    dns = ",".join(f'"{n}"' for n in sorted(names))
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$cert = New-SelfSignedCertificate -DnsName {dns} -CertStoreLocation "
        "cert:\\CurrentUser\\My -KeyExportPolicy Exportable -NotAfter "
        "(Get-Date).AddYears(5) -FriendlyName 'EPU local office'; "
        f"Export-Certificate -Cert $cert -FilePath '{CER.as_posix()}' -Force; "
        f"Export-PfxCertificate -Cert $cert -FilePath '{PFX.as_posix()}' "
        f"-Password (ConvertTo-SecureString -String '{PFX_PASSWORD}' -Force -AsPlainText)"
    )
    code, output = _powershell(script)
    if not PFX.exists():
        config.secure_write("tls-error.log", output)
        return None
    # Trust it on this PC, so the owner's own browser does not warn every time.
    _powershell(f"Import-Certificate -FilePath '{CER.as_posix()}' -CertStoreLocation "
                "cert:\\CurrentUser\\Root")
    return PFX


def _lan_ips() -> list[str]:
    try:
        import socket

        host = socket.gethostname()
        ips = {info[4][0] for info in socket.getaddrinfo(host, None)
               if info[0] == socket.AF_INET and not info[4][0].startswith("127.")}
        return sorted(ips)
    except OSError:
        return []


def _pem(extra_names: list[str] | None = None) -> tuple[Path, Path] | None:
    """The certificate as PEM files, because Python's ssl reads PEM (not .pfx)."""
    if not ensure_certificate(extra_names):
        return None
    import ssl
    from cryptography.hazmat.primitives.serialization import (Encoding, NoEncryption,
                                                              PrivateFormat, pkcs12)

    try:
        with open(PFX, "rb") as handle:
            loaded = pkcs12.load_key_and_certificates(
                handle.read(), PFX_PASSWORD.encode("utf-8"))
    except Exception as exc:
        raise OSError(f"pfx could not be read: {exc}") from exc
    key, cert = loaded[0], loaded[1]
    if key is None or cert is None:
        raise OSError("the certificate has no key: re-run the office installer")
    cert_path = CERT_DIR / "office-cert.pem"
    key_path = CERT_DIR / "office-key.pem"
    cert_path.write_bytes(cert.public_bytes(Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ))
    return cert_path, key_path


def wrap(server, extra_names: list[str] | None = None):
    """Wrap a listening HTTPServer in TLS. None when no certificate could be made."""
    import ssl

    pem = _pem(extra_names)
    if pem is None:
        return None
    cert_path, key_path = pem
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    except ssl.SSLError:
        return None
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def status() -> dict:
    """What the office can say about its certificate, honestly."""
    return {"https": PFX.exists(), "cert": str(PFX) if PFX.exists() else None,
            "names": (["localhost", "127.0.0.1"] + _lan_ips()),
            "trusted": _trusted()}


def _trusted() -> bool:
    code, output = _powershell(
        "if (Get-ChildItem cert:\\CurrentUser\\Root | Where-Object "
        "{$_.FriendlyName -eq 'EPU local office'}) { 'yes' } else { 'no' }")
    return "yes" in output
