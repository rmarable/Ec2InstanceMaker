# RockySurf issue: MCP scope enforcement lives entirely client-side today, not in core

**Status:** Draft. Not sent, filed, or submitted. Issue, not a PR — see
below.

## What is Ec2InstanceMaker?

[Ec2InstanceMaker](https://github.com/rmarable/Ec2InstanceMaker) is a
Python/Terraform CLI that provisions EC2 instances, and drives
RockySurf-adjacent builds through its own MCP server.

## What did we find?

`SECURITY.md:806-808` names per-token MCP scopes as the project's own
"obvious next step." `runTokenCommand`'s doc comment
(`packages/rockysurf/src/cli.ts`) says the same about the command that
mints tokens: *"the cost is stated in the output: revoking it means
signing out everywhere, which is the honest trade at v0.1 and the thing
to improve when per-token scopes arrive."*

We started implementing that, then traced where `mcp.scopes` is actually
enforced. It isn't enforced anywhere per-token scoping would require.

`issueSession()` (`packages/core/src/auth/sessions.ts:46`) already takes
a `ttlMs` parameter — that half is done. Scope is the problem:

- No session carries a scope. The `sessions` table has no such column.
- `rockysurf mcp` reads `config.mcp.scopes` once, from its own local
  config file, at process startup (`packages/rockysurf/src/cli.ts:216`),
  and uses it to filter which tools it lists and dispatches
  (`visibleTools`/`runTool`, `packages/rockysurf/src/mcp/tools.ts`).
- Their own doc comment on that filter (`packages/rockysurf/src/mcp/
  server.ts:14-19`) says what it is: *"WHERE THE REAL ENFORCEMENT IS,
  and it is not here... It is NOT a sandbox: an agent with shell access
  on this machine can read the same token out of the environment and
  call the HTTP API directly."*
- Core's HTTP layer (`packages/core/src/app.ts`) has no scope concept at
  all. `requireAuth` checks a session is valid and not expired, nothing
  more. Every mutating route a valid session can reach, it can reach —
  `mcp.scopes` or not.

So a `scopes` column wired only into `rockysurf token` and the
`rockysurf mcp` bridge wouldn't close the gap SECURITY.md names. It
would narrow the bridge's own tool list for one token while core keeps
honoring that token for everything else. Bypass the bridge, which their
own comment says is already trivial, and the scope never applied at all.

## Proposed solution

Enforcement moves into core:

1. `sessions` gains a `scopes` column, nullable. `null` means
   unrestricted, so every existing session keeps today's behavior.
2. `issueSession()` accepts an optional scope list alongside `ttlMs`;
   `rockysurf token` gains `--scopes`/`--ttl-days` flags, validated as a
   subset of the install's configured `mcp.scopes` — never a superset.
3. Core's route layer (`requireAuth` or a check on top of it) maps each
   mutating route to the `McpScope` it needs and checks the session's
   own stored scope, falling back to `mcp.scopes` when it's `null`. This
   is the actual enforcement point; everything else is bookkeeping.
4. `rockysurf mcp` asks core for its token's real effective scope
   instead of trusting its own local config, which today can name a
   completely different scope than the token is actually good for.

## Why this is an issue, not a PR

Step 3 changes the authorization boundary of a live application we
don't maintain. Every mutating route needs a correct scope mapping, and
getting one wrong — too loose is a real hole, too tight breaks a
legitimate call — is a different risk than a docs change, a bug fix
using a mechanism already diagnosed in-repo, or an opt-in feature that
never touches existing auth. We don't know every route `requireAuth`
gates, or what direction the maintainers already have in mind for their
own "obvious next step." A PR that guesses wrong here is worse than no
PR. It also needs a mergeable diff to exist at all, and we're not
manufacturing one just to have something to submit.

## What we're asking

Does the design above match what you had in mind? Specifically:

- Is `requireAuth`-layer enforcement the right place, or is there an
  existing per-route authorization pattern we should use instead?
- Should a `null` scope really fall back to `mcp.scopes` in full, or
  should a scope-aware token always be explicit?
- Should the `rockysurf mcp` bridge ask core for its own effective
  scope as part of this, or is that a separate concern?

Happy to implement once there's a direction.
