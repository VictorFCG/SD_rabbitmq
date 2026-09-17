import base64
import json
from pathlib import Path

from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15


def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def generate_keypair():
    key = RSA.generate(2048)
    return key.export_key(), key.publickey().export_key()


def load_private(path):
    return RSA.import_key(Path(path).read_bytes())


def load_public(path):
    return RSA.import_key(Path(path).read_bytes())


def _sign(private_key, message):
    return base64.b64encode(pkcs1_15.new(private_key).sign(SHA256.new(message))).decode("ascii")


def _verify(public_key, message, signature_b64):
    try:
        pkcs1_15.new(public_key).verify(SHA256.new(message), base64.b64decode(signature_b64))
        return True
    except (ValueError, TypeError, KeyError):
        return False


def build_envelope(service, routing_key, data, private_key):
    content = {"service": service, "routing_key": routing_key, "data": data}
    content["Signature"] = _sign(private_key, canonical_bytes(content))
    return content


def verify_envelope(envelope, key_dir):
    signature = envelope.get("Signature")
    if not signature:
        return False
    content = {k: v for k, v in envelope.items() if k != "Signature"}
    producer = content.get("service")
    if not producer:
        return False
    try:
        public_key = load_public(Path(key_dir) / f"{producer}_public.pem")
    except (OSError, ValueError):
        return False
    return _verify(public_key, canonical_bytes(content), signature)
