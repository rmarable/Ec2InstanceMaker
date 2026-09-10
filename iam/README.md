# iam/

**Nothing here is applied, and nothing requires it.** These are policy
documents you would create yourself, in an account you control, if you
decide you want them. The toolkit works exactly as documented without
them.

See README.md's "Optional: running the MCP server under a scoped IAM role"
for whether that decision is worth making. Short version: probably not for
a personal account you are the only operator of; worth it if the MCP
server points at anything you would mind losing.

## What these are

Note the naming, because it is close enough to be a trap:

* `templates/*Ec2InstancePolicy.json` -- what a **built instance** gets.
  Those are live; the build stages and applies one on every run.
* `iam/*.json` -- what the **operator or MCP server** gets. Different
  principal, different blast radius, and inert until you apply them.

| File | Purpose |
|---|---|
| `McpServerTrustPolicy.json` | Who may assume the server's role. Requires MFA. |
| `McpServerPolicy.json` | What that role may do. |
| `CreatedRoleBoundary.json` | A permissions boundary attached to every instance role the toolkit creates. |

## Setup

Substitute the placeholders (`<AWS_ACCOUNT_ID>`, `<OPERATOR_IAM_USER>`,
`<ALLOWED_REGIONS>`, `<ALLOWED_INSTANCE_TYPES>`, `<EC2_IAM_PREFIX>`), then:

```
$ aws iam create-policy --policy-name Ec2InstanceMakerCreatedRoleBoundary \
    --policy-document file://iam/CreatedRoleBoundary.json
$ aws iam create-policy --policy-name Ec2InstanceMakerMcpServerPolicy \
    --policy-document file://iam/McpServerPolicy.json
$ aws iam create-role --role-name Ec2InstanceMakerMcpServer \
    --assume-role-policy-document file://iam/McpServerTrustPolicy.json
$ aws iam attach-role-policy --role-name Ec2InstanceMakerMcpServer \
    --policy-arn arn:aws:iam::<AWS_ACCOUNT_ID>:policy/Ec2InstanceMakerMcpServerPolicy
```

Point the server at it with a profile that assumes the role:

```json
{
  "mcpServers": {
    "ec2instancemaker": {
      "command": ".venv/bin/python3",
      "args": ["mcp_server.py", "--allow-mutating"],
      "env": { "AWS_PROFILE": "ec2instancemaker-mcp" }
    }
  }
}
```

Then check it rather than assuming it:

```
$ AWS_PROFILE=ec2instancemaker-mcp ./verify_mcp_credentials.py --region us-east-1
```

`verify_mcp_credentials.py` asks IAM itself, via
`iam:SimulatePrincipalPolicy`, whether the dangerous things are denied and
the things a build needs are still allowed. Every check is a simulation --
nothing is created, modified or deleted -- and it exits non-zero if any
check fails, so it works as a pre-flight.

Running it against an ordinary admin identity will report that almost
everything is allowed. That is not a bug: it is measuring whatever
identity you actually have, and an unscoped one is unscoped.

## What the policy enforces

* An explicit `Deny` on start/stop/reboot/terminate for any instance not
  tagged `ManagedBy=Ec2InstanceMaker`. This is the single most valuable
  statement in the file -- an explicit Deny holds even if some future
  `Allow` gets broader.
* An explicit `Deny` outside your chosen regions and instance types.
* IAM writes scoped to `<EC2_IAM_PREFIX>-*`, `iam:PassRole` restricted to
  `ec2.amazonaws.com`, and role creation refused unless the permissions
  boundary is attached -- so a role the toolkit creates cannot exceed the
  boundary even under `ExtendedEc2InstancePolicy.json`, whose IAM grants
  are otherwise enough to reach account administrator.
* A `Deny` on rewriting the boundary or the server's own role. A boundary
  the caller can edit is not a boundary.
* A `Deny` on creating users, access keys or login profiles at all.

## What it does not enforce

* **It cannot bound how many instances a build launches.** IAM has no
  condition key for `RunInstances` count. An EC2 vCPU service quota is the
  only real cap, and it is worth setting independently of any of this.
* **The instance-type `Deny` covers on-demand cleanly and Spot only
  partially.** Spot instances are launched by the Spot service in response
  to `ec2:RequestSpotInstances`, so tag-on-create conditions do not apply
  the way they do to `ec2:RunInstances` -- the same reason
  `DEFAULT_EC2_TEMPLATE.j2` needs a `create-tags` `local-exec` for spot at
  all. The `ManagedBy` Deny still protects them once tagged.
* **These documents have never been applied against a live account.**
  Everything else in this toolkit's security posture was verified by
  execution; this was reasoned about. Treat a failed
  `verify_mcp_credentials.py` check as the policy being wrong rather than
  the check being wrong.
