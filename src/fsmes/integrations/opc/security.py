"""Client-side OPC UA security: certificates and the connection handshake.

The twin's own simulator accepts anonymous, unencrypted connections, which is
why the default configuration has none of this. Real servers do not. A stock
KEPServerEX offers exactly one endpoint — SignAndEncrypt / Basic256Sha256 /
UserName — so talking to anything real means presenting a certificate and a
named user.

Both the agent and labs/kepsim/verify_readback.py connect through here, so the
lab and the production path prove out the same handshake.

The first connection to a server that has never seen this certificate is
EXPECTED to fail with BadCertificateUntrusted / BadSecurityChecksFailed. Trust
it once on the server (in Kepware: OPC UA Configuration Manager -> Instance
Certificates), then reconnect. That one-time dance is the single most common
OPC UA failure in the field, so `explain_connection_error` spells it out
instead of leaving a bare status code.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

log = structlog.get_logger("opc.security")


def ensure_client_certificate(cert: Path, key: Path, *, application_uri: str, common_name: str) -> None:
    """Mint a self-signed client certificate if one is not already present."""
    cert, key = Path(cert), Path(key)
    if cert.exists() and key.exists():
        return

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    cert.parent.mkdir(parents=True, exist_ok=True)
    key.parent.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MES-TWIN"),
        ]
    )
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        # The URI entry is not decoration: a UA server rejects a certificate
        # whose SAN URI disagrees with the client's advertised application URI.
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(application_uri), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=True,
                data_encipherment=True,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    )
    signed = builder.sign(private_key, hashes.SHA256())
    cert.write_bytes(signed.public_bytes(serialization.Encoding.DER))
    key.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    log.info("minted client certificate", cert=str(cert), note="trust it once on the server, then reconnect")


async def apply_security(client, settings) -> None:
    """Configure a client from settings. A no-op when no security is configured,
    which keeps the anonymous local simulator path exactly as it was."""
    if settings.opc_user:
        client.set_user(settings.opc_user)
        client.set_password(settings.opc_password)
    if not settings.opc_security:
        return

    client.application_uri = settings.opc_application_uri
    policy, mode, *rest = [part.strip() for part in settings.opc_security.split(",")]
    cert = Path(rest[0]) if rest else settings.opc_cert_dir / "mes_twin_client.der"
    key = Path(rest[1]) if len(rest) > 1 else settings.opc_cert_dir / "mes_twin_client_key.pem"
    ensure_client_certificate(
        cert, key, application_uri=settings.opc_application_uri, common_name="MES-TWIN Agent"
    )
    await client.set_security_string(f"{policy},{mode},{cert},{key}")


def explain_connection_error(exc: Exception) -> str:
    """Turn a UA connection failure into the action that actually fixes it."""
    text = f"{type(exc).__name__}: {exc}"
    if "Certificate" in text or "SecurityChecks" in text:
        return (
            f"{text}\nThe server has not trusted this client certificate yet. This is the expected "
            f"first-run rejection — trust it once on the server (Kepware: OPC UA Configuration "
            f"Manager -> Instance Certificates), then reconnect."
        )
    if "Identity" in text or "UserAccessDenied" in text:
        return f"{text}\nThe certificate is accepted but the login was refused — check MES_OPC_USER / MES_OPC_PASSWORD."
    if "BadSecurityPolicyRejected" in text or "BadSecurityModeRejected" in text:
        return f"{text}\nThe server does not offer this policy/mode — check MES_OPC_SECURITY against the endpoint list."
    return text
