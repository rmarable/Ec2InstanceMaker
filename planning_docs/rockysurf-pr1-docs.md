# RockySurf PR: Document ambient-host credential inheritance and the existing headless bootstrap path

**Status:** Draft. Not sent, filed, or submitted. Grounded against the
real cloned source — citations are file:line.

**Type:** Documentation only. No code changes.

**Patch:** `rockysurf-pr1-docs.patch` in this folder — a real `git diff`
against a fresh clone of `amroja-biz/rockysurf`, verified to `git apply
--check` cleanly. Covers `README.md` and `SECURITY.md`. Their docs
consistently write "Rocky Surf" (two words); the patch matches that,
even though this repo's own docs/code correctly say "RockySurf" (the
npm package/repo name).

## What is Ec2InstanceMaker?

[Ec2InstanceMaker](https://github.com/rmarable/Ec2InstanceMaker) is a
Python/Terraform CLI that provisions EC2 instances. `--enable_rockysurf`
installs RockySurf via systemd on Linux instances it builds.

## What did we find?

Building that feature surfaced a gap in RockySurf's threat model: its
control-plane process runs on compute with an ambient IAM identity it
didn't provision itself.

`SECURITY.md:183` discloses the standard AWS SDK credential chain
(`AWS_PROFILE`, environment, instance role). `provider-aws` already
enforces IMDSv2 + `HttpPutResponseHopLimit: 1` on instances *it
provisions for a user* (`SECURITY.md:463-464`), with the stated reason:
"These boxes run agent-authored code: IMDSv1 lets anything that can forge
a GET read instance metadata, and a hop limit above 1 lets a container on
the box reach it." RockySurf doesn't apply or document that same
reasoning for the host running its own process.

Separately, we found that RockySurf already supports a full headless
setup path that isn't documented anywhere:

- `ROCKYSURF_ADMIN_PASSWORD` (`ADMIN_PASSWORD_ENV`) is read fresh on
  every server start (`packages/core/src/server.ts:205`) and passed into
  `ensureLocalAdmin()` (`packages/core/src/auth/admin.ts:49-70`). An
  explicit value always overwrites whatever is stored, so it doubles as
  rotation, not just first-boot setup. Reading the real implementation
  (not just its doc comment) also showed it's passed straight to
  `scrypt` with no strength check of its own — the generated-password
  path is where the randomness guarantee actually lives, which is worth
  a line in `SECURITY.md`, not just the README.
- Config pre-seeding works via `--config <path>` (`packages/rockysurf/
  src/cli.ts`) or `~/.rockysurf/config.yaml` (`packages/core/src/config/
  load.ts:15-31`).

Neither mechanism appears in `README.md` or `SECURITY.md` today.

## What are we proposing?

Two documentation additions, no code:

1. A new `SECURITY.md` section, sibling to the existing IMDSv2 rationale,
   titled "Running RockySurf's control plane on a host with an inherited
   cloud identity." It states that RockySurf does no scoping of its own
   process's identity, and that an embedder is responsible for a
   permissions boundary or scoped service account at the host layer.
2. Documenting `ROCKYSURF_ADMIN_PASSWORD` and `--config`/
   `~/.rockysurf/config.yaml` as the supported automated-deployment
   path, in both `README.md` and `SECURITY.md`.

We think this is a low-risk change since it requires no code
modifications.

Cite: `SECURITY.md:183`, `SECURITY.md:463-464`,
`packages/provider-aws/src/config.ts:47-48`,
`packages/provider-aws/src/provider.ts:167-169`,
`packages/core/src/auth/admin.ts:20-23,44`,
`packages/core/src/config/load.ts:15-31`.

A possible fast-follow, not in scope here: log the effective caller
identity once on startup (`sts:GetCallerIdentity` or the provider
equivalent), so an operator can see what RockySurf is actually running
as.

## How did we validate this?

We built and tore down a real EC2 instance running RockySurf. That
confirmed RockySurf's process inherits the instance's IAM role via the
standard SDK chain with zero extra steps.

## Does this break anything?

No. Documentation only.
