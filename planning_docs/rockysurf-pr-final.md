# RockySurf hardening proposal — internal index (DRAFT)

Internal tracking only — not itself submitted anywhere. Each item below
is sent as an independent PR or issue, not a bundled series; their own
docs don't reference each other.

**Status:** Draft. Not sent, filed, or submitted. Three standalone PRs
plus one issue, each in this folder:

1. `rockysurf-pr1-docs.md` — document ambient-host credential inheritance
   + the existing headless bootstrap path. Docs only. Real patch:
   `rockysurf-pr1-docs.patch`.
2. `rockysurf-pr3-dns-rebinding.md` — connect-time IP pinning fix for the
   fetch-guard DNS-rebinding gap. Real patch, fully validated:
   `rockysurf-pr3-dns-rebinding.patch`.
3. `rockysurf-pr4-tls.md` — opt-in self-signed TLS. Real patch, fully
   validated: `rockysurf-pr4-tls.patch`.
4. `rockysurf-issue-mcp-scopes.md` — **an issue, not a PR.** Per-token MCP
   scope enforcement. No patch — see below for why.

Plain-language summary: `rockysurf-pr-explainer-final.md`.

## Why three PRs and one issue

The first three are docs, a bug fix using a mechanism RockySurf's own
comments already diagnose, or an opt-in feature that never touches
existing authorization. Bounded, low-ambiguity, shipped as real
validated patches.

The fourth started as a proposed API until tracing the actual
enforcement path showed scope is checked only inside the `rockysurf mcp`
client process, never in core's own authorization layer — a real fix
means changing how core authorizes every mutating route, which needs
their input on approach before any code exists. Full trace in
`rockysurf-issue-mcp-scopes.md`.

## Shared context

[Ec2InstanceMaker](https://github.com/rmarable/Ec2InstanceMaker) is a
Python/Terraform CLI that provisions EC2 instances.
`--enable_rockysurf` installs RockySurf via systemd on Linux instances it
builds, bound to `127.0.0.1`, reachable via SSH/SSM port-forward. That
surfaced a case RockySurf's threat model doesn't cover: its control-plane
process running on compute with an ambient IAM identity it didn't
provision.
