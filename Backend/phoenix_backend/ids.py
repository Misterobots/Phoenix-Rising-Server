"""Identifier formats the client uses.

- game account / character / item instance ids: 26 characters of RFC 4648 base32 (A-Z, 2-7), e.g. U2BLWVTDHZGWLBKKLY2QG6UX4I
- Epic account id and product user id (PUID): 32 lowercase hex characters
"""
import base64
import secrets


def base32_id():
    return base64.b32encode(secrets.token_bytes(20)).decode()[:26]


def hex32():
    return secrets.token_hex(16)
