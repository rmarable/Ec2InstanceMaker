# RockySurf PR: Optional built-in TLS

**Status:** Draft. Not sent, filed, or submitted.

**Type:** New config option, opt-in, plus one new dependency.

**Patch:** `rockysurf-pr4-tls.patch` in this folder — a real `git diff`
against a fresh clone of `amroja-biz/rockysurf`, verified to `git apply
--check` cleanly. Touches `packages/core/src/config/schema.ts` (new
`server.tls.selfSigned` field), `packages/core/src/server.ts` (wiring),
a new `packages/core/src/tls/self-signed.ts` module plus its test file,
a test addition in `config.test.ts` and `server.test.ts`, and
`packages/core/package.json`/`pnpm-lock.yaml` for the new dependency.

## What is Ec2InstanceMaker?

[Ec2InstanceMaker](https://github.com/rmarable/Ec2InstanceMaker) is a
Python/Terraform CLI that provisions EC2 instances and can install
RockySurf on them.

## What did we find?

No TLS/cert code exists in `packages/core/src/server.ts` or the config
schema. `SECURITY.md:829-832` states the current position clearly: no
TLS of its own, terminate at a reverse proxy or firewall. That's a
reasonable default for an experienced operator, but a real gap for
anyone who exposes RockySurf beyond loopback without doing that.

Node has no built-in way to generate a self-signed certificate —
`crypto.X509Certificate` only *parses* one. Closing this gap means a new
dependency, not just new code. `@hono/node-server`'s `serve()` (already
in use in `server.ts`) already accepts `createServer`/`serverOptions`
for HTTPS, so the only real gap is certificate generation itself.

## What are we proposing?

An opt-in `server.tls.selfSigned: true` config flag. When set, a
self-signed certificate is generated once — under `server.dataDir`, not
a temp directory, so it survives a restart and doesn't re-invalidate a
browser's "I trust this" exception every boot — and `server.ts` starts
an HTTPS listener instead of plain HTTP. The certificate's name(s)
follow `server.publicUrl` when configured, falling back to `server.host`
itself (unless it's `0.0.0.0`/`::`, which nobody's browser ever
navigates to), always including `localhost`/`127.0.0.1`/`::1`. This
isn't a substitute for a real certificate — a self-signed one still
shows a browser warning. But it protects operators who bind beyond
loopback without already knowing to put a reverse proxy in front, which
plaintext HTTP does not.

The private key is written with the same file mode a secret key already
gets elsewhere in this codebase (`chmod 600`, matching
`secrets/master-key.ts`).

**The new dependency:** `selfsigned@5.5.0`. It pulls in `@peculiar/x509`
and `pkijs` (pure JS, WebCrypto-based, no native bindings). That's a
small, real addition, not the zero-transitive-deps claim an earlier pass
at this made. We confirmed via `check-npx-closure.mjs` (their own
dependency hygiene script) that none of this touches the vendor-SDK
closure it guards.

Cite: `SECURITY.md:829-832`.

## How did we validate this?

- `tsc --noEmit` on the whole `@rockysurf/core` package compiles clean.
- The full existing test suite — 1799 tests across 90 files — passes
  unmodified against the patched package.
- We added 13 new tests: schema defaults and explicit-`true` parsing
  (`config.test.ts`); certificate-name selection logic, real certificate
  generation, file permissions, and cache reuse across restarts and
  across a changed bind host (`tls/self-signed.test.ts`); and — the one
  that actually proves the wiring, not just the pieces — a real end-to-end
  boot with `server.tls.selfSigned: true`, a real ephemeral-port listener,
  and a genuine TLS handshake over `https.get()` against the generated
  certificate, confirmed against a sibling test that the default (TLS
  off) still serves plain HTTP (`server.test.ts`).
- Full suite after the additions: 1812/1812 passing.
- Ran `check-npx-closure.mjs` directly (not just via its own test, which
  hit vitest's default timeout under full-suite load — confirmed
  separately as a timing artifact, not a real failure): `"ok": true,
  "violations": []`.

## Does this break anything?

No. Opt-in, default off, no change to existing deployments.

## What are we asking for?

This is as much a product decision as a code change — whether RockySurf
wants to own self-signed-cert lifecycle (generation, renewal, warning
UX) at all, or whether "bring your own proxy" is the intended permanent
position. We're sending it as a PR anyway, on the assumption it's easier
to react to a concrete, working proposal than an abstract question —
happy to close it if "bring your own proxy" is the answer.
