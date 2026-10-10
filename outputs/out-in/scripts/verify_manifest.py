"""Verify an Out/In DEMO artifact bundle; never a legal signature verifier.

Usage:
    python scripts/verify_manifest.py JOB/artifacts
    python scripts/verify_manifest.py JOB/artifacts/verification_manifest.json --source-dir JOB
    python scripts/verify_manifest.py JOB/artifacts --trusted-key-dir TRUSTED_PUBLIC_KEYS

The optional trusted directory must contain demo_public_key.pem,
demo_borrower_public_key.pem and demo_lender_public_key.pem, obtained separately.
Without external key pinning, verification proves self-contained byte integrity,
not authenticity against replacement of an entire bundle and its demo keys.
Only cryptography is required at verification time. Import has no side effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

LEGAL_STATUS = 'demo_attestation_not_legal_countersignature'
MANIFEST_NAME = 'verification_manifest.json'
MANIFEST_SIG = 'verification_manifest.sig'
PDF_NAME = 'Out_In_TNC_Agreement.pdf'
PUBLIC_KEYS = {'manifest': 'demo_public_key.pem',
               'borrower': 'demo_borrower_public_key.pem',
               'lender': 'demo_lender_public_key.pem'}
ROLE_SIGNATURES = {'borrower': 'borrower_attestation.sig', 'lender': 'lender_attestation.sig'}
EXPECTED_OUTPUTS = {'MasterLedger.xlsx', PDF_NAME, *PUBLIC_KEYS.values(), *ROLE_SIGNATURES.values()}
ATTESTATION_DOMAIN = b'OUT_IN_DEMO_PDF_ATTESTATION_V1\x00'


class VerificationError(ValueError):
    """Malformed, altered, or unverifiable demo artifact evidence."""


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _reject_number(value):
    raise VerificationError(f'Noncanonical numeric evidence: {value}; decimal quantities must be strings')


def _load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object,
                           parse_float=_reject_number, parse_constant=_reject_number)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f'Cannot read manifest: {exc}') from exc
    if not isinstance(value, dict):
        raise VerificationError('Manifest must be a JSON object')
    return value


def _safe_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise VerificationError('Artifact/source names must be relative POSIX paths')
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')):
        raise VerificationError(f'Unsafe evidence path: {name}')
    path = root.joinpath(*relative.parts)
    resolved = path.resolve()
    if root not in resolved.parents:
        raise VerificationError(f'Evidence path escapes directory: {name}')
    # Reject symlinks even if currently resolving inside the root: published
    # artifacts must be standalone regular files, not mutable external links.
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise VerificationError(f'Symlink evidence is forbidden: {name}')
    if not path.is_file():
        raise VerificationError(f'Missing evidence file: {name}')
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _entries(entries, label: str) -> dict[str, str]:
    if not isinstance(entries, list):
        raise VerificationError(f'{label} must be a list')
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'name', 'sha256'}:
            raise VerificationError(f'{label} entry must contain only name and sha256')
        name, digest = entry['name'], entry['sha256']
        if not isinstance(name, str) or not name or '\\' in name or ':' in name:
            raise VerificationError(f'Unsafe {label} name')
        if PurePosixPath(name).is_absolute() or any(part in ('', '.', '..') for part in name.split('/')):
            raise VerificationError(f'Unsafe {label} path: {name}')
        if name in result:
            raise VerificationError(f'Duplicate {label} entry: {name}')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise VerificationError(f'Invalid canonical SHA256 for {name}')
        result[name] = digest
    return result


def _crypto_dependencies():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise VerificationError('Verification requires installed cryptography; no packages are installed automatically') from exc
    return InvalidSignature, serialization, Ed25519PublicKey


def verify_manifest(manifest_path: str | Path, source_dir: str | Path | None = None,
                    trusted_key_dir: str | Path | None = None) -> dict:
    """Verify every payload hash, canonical manifest signature and PDF roles.

    Raise VerificationError on any failure. source_dir, when provided, must
    contain every declared source at its exact manifest-relative path.
    """
    manifest_path = Path(manifest_path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / MANIFEST_NAME
    if manifest_path.name != MANIFEST_NAME or manifest_path.is_symlink():
        raise VerificationError(f'Expected standalone {MANIFEST_NAME}')
    root = manifest_path.parent.resolve()
    manifest_path = _safe_path(root, MANIFEST_NAME)
    manifest = _load_manifest(manifest_path)
    if type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise VerificationError('Unsupported manifest schema version')
    if (manifest.get('legal_status') != LEGAL_STATUS or manifest.get('is_demo') is not True
            or manifest.get('real_funds') is not False):
        raise VerificationError('Manifest must explicitly be DEMO, not legally countersigned, and carry no real funds')
    if not isinstance(manifest.get('mode'), str) or not manifest['mode'].strip():
        raise VerificationError('Missing workflow mode')
    if set(re.split(r'[^a-z]+', manifest['mode'].lower())) & {'real', 'live', 'production'}:
        raise VerificationError('Real/live/production modes are forbidden')
    exclusions = manifest.get('hash_exclusions')
    if exclusions != [MANIFEST_NAME, MANIFEST_SIG]:
        raise VerificationError('Manifest and its detached signature must be explicitly excluded from self-hashing')
    outputs = _entries(manifest.get('outputs'), 'outputs')
    sources = _entries(manifest.get('sources'), 'sources')
    if set(outputs) != EXPECTED_OUTPUTS:
        raise VerificationError('Output inventory must include ledger, PDF, all separate public keys and both PDF signatures')
    actual_names = {path.name for path in root.iterdir()}
    expected_names = EXPECTED_OUTPUTS | {MANIFEST_NAME, MANIFEST_SIG}
    if actual_names != expected_names:
        raise VerificationError(f'Unexpected/missing bundle files: {sorted(actual_names ^ expected_names)}')
    signatures = manifest.get('signatures')
    if not isinstance(signatures, dict) or signatures.get('algorithm') != 'Ed25519':
        raise VerificationError('Detached Ed25519 signature metadata is required')
    manifest_metadata = signatures.get('manifest')
    if not isinstance(manifest_metadata, dict) or (
            manifest_metadata.get('signature') != MANIFEST_SIG
            or manifest_metadata.get('public_key') != PUBLIC_KEYS['manifest']):
        raise VerificationError('Manifest signature/key metadata is invalid')
    pdf_metadata = signatures.get('pdf')
    if not isinstance(pdf_metadata, dict) or set(pdf_metadata) != set(ROLE_SIGNATURES):
        raise VerificationError('Both borrower and lender demo attestations are required')
    for role in ROLE_SIGNATURES:
        metadata = pdf_metadata[role]
        if not isinstance(metadata, dict) or (
                metadata.get('signature') != ROLE_SIGNATURES[role]
                or metadata.get('public_key') != PUBLIC_KEYS[role]
                or metadata.get('artifact') != PDF_NAME):
            raise VerificationError(f'Invalid {role} PDF signature/key metadata')

    InvalidSignature, serialization, Ed25519PublicKey = _crypto_dependencies()
    keys = {}
    for role, name in PUBLIC_KEYS.items():
        try:
            key = serialization.load_pem_public_key(_safe_path(root, name).read_bytes())
        except (ValueError, TypeError) as exc:
            raise VerificationError(f'Invalid {role} public key') from exc
        if not isinstance(key, Ed25519PublicKey):
            raise VerificationError(f'{role} public key must be Ed25519')
        keys[role] = key
    raw_keys = {role: key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                for role, key in keys.items()}
    if len(set(raw_keys.values())) != len(PUBLIC_KEYS):
        raise VerificationError('Manifest and both PDF roles must use separate keys')
    if trusted_key_dir is not None:
        trusted_root = Path(trusted_key_dir).resolve()
        for role, name in PUBLIC_KEYS.items():
            try:
                trusted = serialization.load_pem_public_key(_safe_path(trusted_root, name).read_bytes())
            except (ValueError, TypeError) as exc:
                raise VerificationError(f'Invalid pinned {role} key') from exc
            if not isinstance(trusted, Ed25519PublicKey) or trusted.public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw) != raw_keys[role]:
                raise VerificationError(f'{role} public key does not match separately trusted key')

    signature = _safe_path(root, MANIFEST_SIG).read_bytes()
    if len(signature) != 64:
        raise VerificationError('Manifest Ed25519 signature must contain 64 raw bytes')
    try:
        keys['manifest'].verify(signature, canonical_json(manifest))
    except InvalidSignature as exc:
        raise VerificationError('Canonical manifest signature verification failed') from exc
    # Only after authenticating the canonical manifest do we use its digests.
    for name, digest in outputs.items():
        if _sha256(_safe_path(root, name)) != digest:
            raise VerificationError(f'Output SHA256 mismatch: {name}')
    pdf_bytes = _safe_path(root, PDF_NAME).read_bytes()
    for role, name in ROLE_SIGNATURES.items():
        signature = _safe_path(root, name).read_bytes()
        if len(signature) != 64:
            raise VerificationError(f'{role} Ed25519 signature must contain 64 raw bytes')
        message = ATTESTATION_DOMAIN + role.encode('ascii') + b'\x00' + pdf_bytes
        try:
            keys[role].verify(signature, message)
        except InvalidSignature as exc:
            raise VerificationError(f'{role} demo PDF attestation verification failed') from exc
    evidence_input = manifest.get('evidence_hash_input')
    if not isinstance(evidence_input, dict) or evidence_input.get('sources') != manifest['sources']:
        raise VerificationError('Upstream evidence hash input must include the declared sources')
    if hashlib.sha256(canonical_json(evidence_input)).hexdigest() != manifest.get('evidence_hash'):
        raise VerificationError('Upstream source/terms evidence SHA256 mismatch')
    checked_sources = 0
    if source_dir is not None:
        source_root = Path(source_dir).resolve()
        if not source_root.is_dir():
            raise VerificationError('Source directory does not exist')
        for name, digest in sources.items():
            if _sha256(_safe_path(source_root, name)) != digest:
                raise VerificationError(f'Source SHA256 mismatch: {name}')
            checked_sources += 1
    return {'verified': True, 'legal_status': LEGAL_STATUS, 'real_funds': False,
            'mode': manifest['mode'], 'outputs_checked': len(outputs),
            'manifest_signature': 'verified', 'borrower_attestation': 'verified',
            'lender_attestation': 'verified', 'sources_checked': checked_sources,
            'source_verification': 'checked' if source_dir is not None else 'not_requested',
            'key_trust': 'externally_pinned_demo_keys' if trusted_key_dir is not None else 'self_contained_demo_keys_only',
            'notice': 'DEMO integrity only; NOT legal countersignatures, real funds, or identity verification'}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Verify DEMO artifacts, hashes and attestations; NOT legal countersignatures.')
    parser.add_argument('manifest', help='Artifact directory or verification_manifest.json')
    parser.add_argument('--source-dir', help='Optional directory containing every declared input source')
    parser.add_argument('--trusted-key-dir', help='Optional separately trusted demo public-key directory')
    args = parser.parse_args(argv)
    try:
        result = verify_manifest(args.manifest, args.source_dir, args.trusted_key_dir)
    except (VerificationError, OSError, TypeError, ValueError) as exc:
        print(f'DEMO VERIFICATION FAILED: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
