# RockySurf PR: Fix DNS-rebinding gap in the fetch guard

**Status:** Draft. Not sent, filed, or submitted.

**Type:** Bug fix, narrow scope, one file (plus its test file and one new
direct dependency).

**Patch:** `rockysurf-pr3-dns-rebinding.patch` in this folder — a real
`git diff` against a fresh clone of `amroja-biz/rockysurf`, verified to
`git apply --check` cleanly. Touches `packages/core/src/packs/
safe-fetch.ts`, `safe-fetch.test.ts`, `packages/core/package.json`
(adds `undici` as a direct dependency — it was already present
transitively via the lockfile, but importing an undeclared package is
fragile under pnpm's strict resolution), and `pnpm-lock.yaml` (the
matching lockfile entry for that dependency — `pnpm install
--frozen-lockfile` fails without it).

## What is Ec2InstanceMaker?

[Ec2InstanceMaker](https://github.com/rmarable/Ec2InstanceMaker) is a
Python/Terraform CLI that provisions EC2 instances and can install
RockySurf on them.

## What did we find?

`packages/core/src/packs/safe-fetch.ts:11-16` (near-verbatim in
`SECURITY.md:822-825`) already diagnoses a timing gap: the vetted DNS
lookup happens before the socket connects, so a DNS server that rebinds
between the two lookups can redirect the connection to an internal
address anyway. The fix, and the rationale for deferring it ("does not
currently justify [the added complexity]"), are both already written
down. We're proposing an implementation of the fix you'd already
designed.

## What are we proposing?

A connect-time lookup override: pin the vetted, already-resolved IP at
connect time via a custom undici `Agent`/`lookup`, so the address the
socket connects to matches the one that was checked.

Cite: `packages/core/src/packs/safe-fetch.ts:11-16`,
`SECURITY.md:822-825`.

## How did we validate this?

- `tsc` compiles clean under your exact `strict`/`verbatimModuleSyntax`/
  `NodeNext` settings. We hit a real cross-package type conflict
  (`undici`'s own `Dispatcher`/`Agent` types vs. the copy bundled inside
  `@types/node`'s `undici-types` — structurally identical, not nominally
  the same type) and resolved it with a single documented cast at the
  one point they hand off to each other, not scattered `any`s.
- Your full existing test suite (59 tests) passes unmodified against the
  patched file.
- We added 6 new tests: the dispatcher is attached and closed when a
  host is DNS-resolved, absent for a literal-IP URL and for an
  `allowHosts`-exempt name (neither one was ever resolved in the first
  place), re-pinned independently on each redirect hop, and never
  constructed when the address is refused before any fetch happens.
- We also ran a standalone real-socket smoke test, outside the test
  suite and not part of the patch, to prove the underlying mechanism
  directly: a `fetch()` call for a real-looking hostname, pinned via the
  dispatcher, provably lands on the pinned address instead of performing
  fresh DNS resolution.

## How urgent is this?

Low priority. It's an accepted residual risk on an admin-only,
session-gated endpoint, not an open hole.

## Does this break anything?

No. Internal fetch-guard implementation only.
