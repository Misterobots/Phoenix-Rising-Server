"""The client's HTTPS / WSS entry point: TLS on 127.0.0.1:443 for the service hostnames the hosts file points at
this machine, served by the same app as the plain ports (no separate proxy).

Certificates live in <data>/tls: ca.pem (the local CA the player trusts once), leaf.pem + leaf-key.pem (one leaf
for every hostname below). Missing files are generated; an existing CA is kept so a trusted CA stays trusted."""
import datetime
import logging
import os
import ssl

log = logging.getLogger('phoenix.tls')

HOSTNAMES = ('api.epicgames.dev', 'connect.epicgames.dev', 'account-public-service-prod03.ol.epicgames.com',
             'egp-idsoc-proxy-prod.ol.epicgames.com', 'xmpp-service-prod.ol.epicgames.com',
             'tracking-prod.steelyard.ca', 'telemetry-ingest-prod.steelyard.ca')
CA_NAME = 'Phoenix Rising Local CA'


def _write(path, data):
    with open(path, 'wb') as f:
        f.write(data)


def ensure_certificates(cert_dir, hostnames=HOSTNAMES):
    """-> (leaf.pem, leaf-key.pem) paths, generating the CA and/or leaf when missing or not covering `hostnames`."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    os.makedirs(cert_dir, exist_ok=True)
    ca_pem, ca_key = os.path.join(cert_dir, 'ca.pem'), os.path.join(cert_dir, 'ca-key.pem')
    leaf_pem, leaf_key = os.path.join(cert_dir, 'leaf.pem'), os.path.join(cert_dir, 'leaf-key.pem')
    now = datetime.datetime.now(datetime.timezone.utc)

    if os.path.exists(leaf_pem) and os.path.exists(leaf_key):
        cert = x509.load_pem_x509_certificate(open(leaf_pem, 'rb').read())
        try:
            sans = set(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
        except x509.ExtensionNotFound:
            sans = set()
        if set(hostnames) <= sans and cert.not_valid_after_utc > now + datetime.timedelta(days=7):
            return leaf_pem, leaf_key
        if not os.path.exists(ca_key):
            raise RuntimeError('%s does not cover %s and there is no ca-key.pem to reissue it' % (leaf_pem, sorted(set(hostnames) - sans)))

    pem = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption())
    if os.path.exists(ca_pem) and os.path.exists(ca_key):
        ca = x509.load_pem_x509_certificate(open(ca_pem, 'rb').read())
        ca_private = serialization.load_pem_private_key(open(ca_key, 'rb').read(), None)
    else:
        ca_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_private.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
              .not_valid_after(now + datetime.timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
              .sign(ca_private, hashes.SHA256()))
        _write(ca_key, pem(ca_private))
        _write(ca_pem, ca.public_bytes(serialization.Encoding.PEM))
        log.warning('generated a new local CA %s: trust it once (Play.bat or tools/setup-local.ps1 does this)', ca_pem)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hostnames]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_private, hashes.SHA256()))
    _write(leaf_key, pem(key))
    _write(leaf_pem, leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    log.info('issued leaf certificate for %d hostnames', len(hostnames))
    return leaf_pem, leaf_key


def server_context(cert_dir):
    leaf_pem, leaf_key = ensure_certificates(cert_dir)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(leaf_pem, leaf_key)
    return ctx
