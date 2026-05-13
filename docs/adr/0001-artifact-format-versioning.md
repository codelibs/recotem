# ADR 0001 — Artifact Format Versioning Strategy

**Status:** Accepted

**Date:** 2026-05-13

## Context

Recotem artifacts are self-contained signed binaries exchanged between `recotem train` and `recotem serve`. The binary layout must be versioned so that:

1. Readers can detect and refuse formats they do not understand.
2. The evolution path (adding fields, changing the HMAC scope) is predictable for operators planning upgrades.

## Binary layout (current: version 1)

```
Offset    Size  Field
------    ----  -----
0         8     Magic bytes: b"RECOTEM\0"
8         2     Format version (uint16 LE) — currently 1
10        2     Reserved (uint16 LE) — must be 0
12        1     Key-id length K (uint8; 1 <= K <= 32)
13        K     Key-id bytes (UTF-8)
13+K      32    HMAC-SHA256 digest
45+K      4     Header JSON length N (uint32 LE; N <= 65536)
49+K      N     Header JSON (UTF-8)
49+K+N    M     Native binary payload (irspack IDMappedRecommender)
```

**HMAC scope:** `kid_bytes || header_json_bytes || payload_bytes`. Tampering with the kid, header, or payload fails verification. HMAC is verified before a single payload byte is interpreted.

**Reserved bytes:** Two bytes at offset 10-11, currently always 0. Reserved for future flag bits (e.g. compression format, payload encoding). Readers reject any non-zero value so that a future writer cannot silently produce a file a current reader misinterprets.

## Decision

### Version bump policy

| Change | Version impact |
|--------|---------------|
| HMAC scope changes (different byte sequence signed) | MAJOR bump (new `FORMAT_VERSION`) |
| Required header JSON field added | MAJOR bump |
| Optional header JSON field added or renamed | Compatible — no bump required |
| Reserved bytes assigned a flag bit meaning | MAJOR bump (existing readers already reject non-zero reserved bytes) |
| FQCN allow-list extended or narrowed | Documented in CHANGELOG; no format bump; requires reader upgrade before writer for narrowing changes |
| Payload serialisation format changes | MAJOR bump |

### Reader/writer compatibility guarantee

Within a major format version (`FORMAT_VERSION=1`):

- A newer **writer** may add optional header JSON fields. Older readers silently ignore unknown fields via `dict.get()` with defaults.
- A newer **reader** must accept artifacts produced by any writer at the same `FORMAT_VERSION`.
- A **reader** encountering `FORMAT_VERSION > FORMAT_VERSION_THIS_BUILD` raises `ArtifactError` with message `"unsupported format version N; this build supports up to version M"`. Upgrade the reader or retrain.

### Zero-downtime upgrade path

When the format version bumps:

1. Upgrade `recotem serve` first. It continues serving old-format artifacts while new-format artifacts are produced.
2. Retrain all recipes (`recotem train`). New artifacts carry the new format version.
3. Once all recipes have been retrained and hot-swapped, old artifacts can be removed.

The `recotem inspect` command reads the format version from the fixed prefix before any deserialization and reports it safely on corrupt or future-version artifacts.

## Consequences

- Operators must plan retraining after a major format bump. The operations runbook documents this under [Upgrades](../operations.md#upgrades).
- The reserved bytes field gives 16 flag bits of runway before a reserved-byte bump would force a MAJOR version change, allowing lightweight feature flags to be added without a full version increment.
- Any change to the HMAC scope invalidates all existing artifacts — operators must complete a full retrain after such a change.

## References

- `src/recotem/artifact/format.py` — binary layout constants and `parse_header_from_bytes`
- `src/recotem/artifact/signing.py` — HMAC computation and verification
- [docs/operations.md — Environment variable reference](../operations.md#environment-variable-reference)
- [docs/security.md — Artifact payload and the FQCN allow-list](../security.md#artifact-payload-and-the-fqcn-allow-list)
