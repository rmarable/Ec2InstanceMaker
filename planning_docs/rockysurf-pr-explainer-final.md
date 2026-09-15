# RockySurf hardening proposal — summary

## What is Ec2InstanceMaker?

[Ec2InstanceMaker](https://github.com/rmarable/Ec2InstanceMaker) is a command-line tool that creates and tears down AWS
EC2 instances using Terraform to assign IAM permissions, set up networking,
and providing the operator SSM-based access with no open inbound
ports. `--enable_rockysurf` installs RockySurf on a Linux instance it
builds as a systemd service bound to localhost, reachable only through a
tunnel the operator opens.

## What is the core issue?

An instance IAM role carries a defined set of permissions that any process
running on that instance, RockySurf included, gets to use with no extra step.
This inheritance is normal AWS behavior.

RockySurf already applies IMDSv2 hardening (a setting that stops a
process from silently pulling that instance's credentials via an
unauthenticated request) to servers *it provisions for a user*.  However, RockySurf
doesn't apply or document the same reasoning for the host running its
own control-plane process (which is our use case). Ec2InstanceMaker handles
this with an IAM permissions boundary plus IMDSv2 enforcement. PR-1 asks RockySurf
to document the same principle for its own process.

We are submitting three standalone PRs and one issue.

**PR-1: Documentation.**  In addition to the ambient-credential point raised above,
an automated-setup path (admin password + config via environment variable and file
without a browser wizard, i.e. “headless bootstrap”) already works but is not documented
anywhere. We feel this is a low risk change as no code modifications are required.

**PR-2: DNS-rebinding fix.** RockySurf's own code comments already diagnose a timing gap
in its outbound-fetch guard: it checks the safety of the destination IP and then connects,
but a DNS server can change what that hostname resolves to in-between. The suggested fix is
to pin the checked IP at connect time. Since the endpoint is admin-only and requires an
active session, this is a lower priority. Implemented and validated: their full existing
test suite passes unmodified, plus 6 new tests we added.

**PR-3: Optional TLS.** RockySurf has no built-in TLS; the current guidance is to run
RockySurf as a service behind a reverse proxy.  We propose an opt-in self-signed-certificate
mode to protect less experienced operators who are unaware of this potential risk.  We recoginze this could be considered a product change as RockySurf may not want to own this certificate lifecycle.  We're sending it as a PR anyway, on the assumption it's easier to react to a
concrete proposal than an abstract question — happy to close it if "bring your own proxy" is the answer.
Implemented and validated against their full test suite plus 13 new tests we added — 1812/1812
passing, including a real TLS handshake against the generated certificate.

**Issue-1: MCP token scope.** Programs that connect to RockySurf over MCP get a
credential valid for one year, with one permission-scope setting applied to the whole install.
RockySurf's own security notes name per-connection scope limits as the intended next step. We
discovered scope is only checked inside the `rockysurf mcp` client process, but the core's own
HTTP layer does not appear to be evauated. The project explicitly comments that the client-side
filter is "not a sandbox," and anything with shell access can call core's API directly with the
same token and skip it entirely. One fix we have explorted would be adding scope enforcement to
the core's own routes.

## How we tested these hypotheses

We read the source directly; each submission cites specific files and line numbers. For each PR,
we offer potential implementations that passed RockySurf's own test suite against
the changes (zero regressions).  Separately, we built and tore down real AWS instances running
RockySurf built with Ec2InstanceMaker to confirm everything works as expected.
