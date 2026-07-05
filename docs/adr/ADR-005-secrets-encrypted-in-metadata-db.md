# ADR-005: Credentials encrypted at rest in the metadata DB

**Status:** Accepted
**Date:** 2026-07
**Owners:** Platform
**Related:** [ADR-001](ADR-001-control-plane-data-plane-split.md)

## Status Quo

Provisioning a database produces a superuser credential, and the platform later
needs credentials to perform managed operations inside instances (create roles,
rotate passwords, run backups). Those secrets have to live somewhere. The lazy
options are to store them in plaintext in the metadata DB, or to hand them to the
user and keep no copy — the first is a breach waiting to happen, the second makes
automated management impossible.

## Goal

Store exactly the secret material the platform needs to operate, protected at
rest, with a rotation story, while never persisting a recoverable copy of secrets
the platform does not need to keep. Compromise of the metadata database alone
should not yield usable plaintext credentials.

## Non-Goal

This ADR does not stand up a full secrets-management platform (Vault/KMS) in the
MVP, and it does not solve secure *transport* of credentials to clients beyond the
one-time reveal. Hardware-backed keys and automatic KEK rotation are deferred to
the roadmap.

## Proposed Solution

We combine three techniques: **one-time reveal**, **encryption at rest**, and
**hashing for authenticators**.

**The superuser password is revealed once, then only ever stored encrypted.** The
create response returns the generated password exactly once; the platform keeps
an encrypted copy (needed for rotation and managed operations) but the plaintext
is never persisted or logged. A user who loses it rotates rather than recovers.

**Encryption uses authenticated symmetric crypto with a key held outside the
DB.** Credentials are sealed with Fernet (AES-128-CBC + HMAC) using a key
supplied via environment/secret file, not stored in the metadata DB. This means a
dump of the metadata DB *alone* is ciphertext; an attacker also needs the running
process's key material. The [Design Review §1.3](../design-review.md#security--isolation)
flags the weakness here — a single env-held key — and the accepted hardening path
is **envelope encryption**: a per-instance data key wrapped by a KEK, so KEK
rotation doesn't require re-encrypting every row, with the KEK eventually moving
to Vault Transit or a cloud KMS.

**API keys are hashed, not encrypted.** Principal authenticators are stored as
argon2/bcrypt hashes because the platform never needs to recover them — it only
needs to *verify* a presented key. This is a deliberately different treatment
from database credentials (which must be recoverable to be used) and closes the
"reversible secret" hole.

**Rotation is a first-class operation.** `POST /instances/{id}/credentials:rotate`
generates a new password, applies `ALTER ROLE ... PASSWORD` inside the instance,
re-encrypts the stored copy, bumps `rotated_at`, and writes an audit row. Old
credentials stop working immediately.

## Alternatives

### Plaintext credentials in the metadata DB

Trivial to implement. Rejected outright: a single DB compromise or careless
backup exposes every tenant's database. Non-negotiable to avoid.

### Never store any credential (fully stateless, user-held only)

Hand the password to the user and keep nothing. Rejected because the platform
must perform managed operations (rotation, role management, backups) that require
authenticating to the instance; with no stored credential those features are
impossible. Encryption-at-rest is the balance between usability and safety.

### Full Vault/KMS integration from day one

The correct long-term answer for key custody. Rejected for the MVP only on
grounds of setup weight and demo friction; the envelope-encryption design is
chosen specifically so this can be adopted later without a data migration.
