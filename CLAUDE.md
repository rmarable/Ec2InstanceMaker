# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Ec2InstanceMaker is a Python/Jinja2/Terraform command-line toolkit that
creates, accesses, and destroys AWS EC2 instances. It is not a library or
service — it's a set of scripts an operator runs directly from a shell, and
each run generates a per-instance Terraform/shell toolchain on disk under
`instance_data/`.

There is a `tests/` directory (pytest — see "Linting and CI" below) covering
`aux_data.py`'s pure logic, mocked-AWS behavior of its boto3-calling
functions, and behavioral assertions on rendered template output. It does
not cover `make-instance.py`'s own orchestration flow (untestable without a
much larger refactor — see CLAUDE-STATE.md) or a real end-to-end build.
"Testing" a behavioral change beyond what `tests/` covers means running the
relevant script against a real (or sandbox) AWS account and inspecting the
generated files and AWS side effects — see "Manually exercising the tool"
below.

## Environment setup

```bash
$ python3.12 -m venv .venv && source .venv/bin/activate
$ pip install -r requirements.txt
```

Also requires, on PATH: `terraform` (0.12.x — pinned/checked via
`TERRAFORM_VERSION` in `make-instance.py` and `linux-ec2-setup.sh`), `jq`,
and configured AWS credentials (`aws configure` or env vars).
`make-instance.py` and `template_engine.py` both resolve paths (e.g.
`templates/`) relative to the current working directory, so commands must be
run from the repo root.

## Manually exercising the tool

```bash
# Minimal instance build (needs a real AZ/VPC and AWS credentials):
./make-instance.py -A us-east-2a -N testinstance01 -O <owner> -E <owner_email>

# SSH/RDP access to a previously built instance or family:
./access_instance.py -N testinstance01

# Tear down everything tagged for an instance/family (auto-generated per run):
./kill-instance.<instance_name>.sh
```

Use `--debug_mode=true` to extend the CTRL-C abort window (30s vs 5s) while
inspecting generated templates before Terraform applies them. Full CLI
reference and worked examples live in `README.md` and
`EXAMPLE_USE_CASES.md` — check those before guessing at flag names, defaults,
or validation rules instead of re-deriving them from `make-instance.py`.

## Linting and CI

```bash
$ pip install -r requirements-dev.txt
$ pre-commit install          # one-time, wires the git commit hook
$ pre-commit run --all-files  # run everything on demand
```

`.pre-commit-config.yaml` is the single source of truth for what counts as
"clean" — the same config runs locally (on `git commit`) and in CI
(`.github/workflows/lint.yml`, via `pre-commit/action`). It covers:
- **`pytest`** (`tests/`, run via `python3 -m pytest`, or directly by hand
  during development) — unit tests for `aux_data.py`'s pure-logic functions
  (`base_os_instance_check`, `ebs_encryption_check`,
  `ec2_placement_group_check`, `modify_iam_policy_document`, etc., in
  `tests/test_aux_data_pure.py`), mocked-`boto3` tests for its AWS-calling
  functions (`get_instance_type_info`, `get_ami_info`, `check_custom_ami`,
  `ctrlC_Abort`, in `tests/test_aux_data_aws.py`), and behavioral assertions
  on rendered template output — e.g. "does `preserve_iam_role=true` actually
  produce a kill script with no `iam delete-role` call" — in
  `tests/test_template_behavior.py`, which `scripts/lint_templates.py`
  below never checks (it only verifies rendered output is syntactically
  valid, not which branch rendered or what it does). Run a single file with
  `python3 -m pytest tests/test_aux_data_pure.py -v`.
- `ruff` (lint) + `ruff format` (Black-equivalent) on the top-level `.py`
  files — config in `pyproject.toml`.
- `bandit` (Python security) at `-ll` (Medium/High only — see the comment in
  `.pre-commit-config.yaml` for why Low is excluded: this tool exists to
  shell out to terraform/aws/jq, so bare-name subprocess calls and `import
  subprocess` itself are inherent, not actionable).
- `shellcheck` on real, hand-written `.sh` files (currently just
  `linux-ec2-setup.sh`).
- `detect-secrets` (baseline in `.secrets.baseline` — false positives there
  are documented inline in this file's migration history; extend the
  baseline via `detect-secrets scan > .secrets.baseline`, don't hand-edit it).
- `pip-audit` against `requirements.txt`.
- standard hygiene (trailing whitespace, EOF newline, merge-conflict
  markers, JSON validity, broken/destroyed symlinks).
- **`scripts/lint_templates.py`** (local hook) — the important one for this
  repo: `templates/*.j2` aren't real `.py`/`.sh`/`.tf` files, so none of the
  above ever see them directly, and shellcheck would just choke on raw
  `{% %}`/`{{ }}` syntax. This script renders every template (via
  `template_engine.py`, the same code `make-instance.py` calls) with a
  set of synthetic contexts chosen to hit the major conditional branches
  (currently 14 — every `base_os` value covered at least once, plus
  ondemand/spot, single/family, EBS encryption, placement groups, a
  secondary EBS device volume, and a Graviton/arm64 instance_type), then
  lints the
  *rendered* output: shellcheck on `.sh`, `py_compile` + a narrow
  `ruff --select=F821,F822,F823,E9` pass on `.py` (not the full ruleset —
  generated code composes conditionally per Jinja branch, e.g.
  `access_instance.j2` only uses `csv` inside its `count > 1` block and
  `jq` inside its `'windows' in base_os` block, so a full unused-import
  pass would be permanently noisy), a `bandit -c pyproject.toml -ll` pass
  on `.py` (the pre-commit bandit hook only ever scans the 4 hand-written
  top-level `.py` files, never generated code -- this closes that blind
  spot, since `shell=True` + interpolated data is most likely to show up
  in what a template renders, not in `make-instance.py`/`aux_data.py`
  themselves), and `terraform init -backend=false` + `validate` on the
  `.tf` output.
  `instance_userdata.j2` is skipped for shell linting — it renders
  `#cloud-config` (cloud-init YAML), not bash, despite the `.sh` naming
  convention. When adding a new template or changing what variables it
  needs, add/update the corresponding key(s) in this script's `CONTEXTS`
  dict, or the render step will `KeyError`.
- `terraform fmt -check` also runs inside `scripts/lint_templates.py`, and is
  blocking: `DEFAULT_EC2_TEMPLATE.j2` and `provider_aws.j2` are hand-aligned
  to HCL2 canonical formatting (no legacy `"${...}"` wrapping around a whole
  attribute value, `=` columns aligned per contiguous attribute run) so every
  rendered `.tf` file is fmt-clean with no rewrite needed. If you touch either
  template, re-run `scripts/lint_templates.py` across all 14 `CONTEXTS`
  scenarios and keep it that way — don't reintroduce misalignment.

## Architecture

**`make-instance.py`** is the orchestrator: a linear, top-to-bottom
`__main__`-style script (no classes) that parses CLI args and then calls,
in sequence, the extracted functions in `instance_builder.py` (AZ/region
validation, EBS/spot-price validation, VPC/subnet/security-group
resolution, AMI/keypair/IAM setup, Terraform apply, vars-file writing,
etc. — see `instance_builder.py` below for the full list) threading their
results through local variables into the final `instance_parameters`
dict. The broad shape:
1. Parses CLI flags (argparse) and validates them (AZ, base_os/instance
   type compatibility, EBS size/type, etc.) using helpers from
   `aux_data.py`.
2. Uses boto3 directly (no wrapper classes) to look up AMIs, VPC/subnet info,
   create/modify security groups, IAM roles/policies/instance profiles, and
   an SNS topic for build/teardown notifications.
3. Writes a per-instance `vars_files/<instance_name>.yml` — a human-readable
   audit record of the build, and the marker file used to detect and refuse
   duplicate builds under the same `instance_name`. It is not consumed by
   the render step below; see `template_engine.py`.
4. Calls `template_engine.render_instance_templates()`, which renders the
   Jinja2 templates in `templates/*.j2` directly (via `jinja2.Environment`,
   no subprocess) into a fresh `instance_data/<instance_name>/` directory
   (Terraform config, access/build/kill/AMI shell scripts), building the
   render context straight from the in-memory `instance_parameters` dict,
   and symlinks the generated `kill-instance.<name>.sh` and
   `build-ami.<name>.sh` scripts back into the repo root for convenient
   operator access — all before Terraform ever runs.
5. Shells out to `terraform init/plan/apply` inside that
   `instance_data/<instance_name>/` directory to actually create resources.
   `terraform apply` itself never uses SSH — `DEFAULT_EC2_TEMPLATE.j2`'s
   only provisioner is a `local-exec` that runs `ssm_provision.<name>.sh`
   (rendered alongside everything else), which waits for the instance's
   SSM Agent to register, then pushes and runs `build_instance.sh` and any
   selected `custom_user_postboot_script.j2_<name>` scripts via
   `aws ssm send-command`/`get-command-invocation` — no inbound port needs
   to be reachable from wherever `terraform apply` runs, matching
   `access_instance.py`'s SSM-only access below.
6. Publishes an SNS notification.

**`aux_data.py`** holds shared validation logic, lookup tables (unsupported
instance/OS combos per `base_os`), and small utilities (`p_val`/`p_fail` for
parameter validation messaging, `ctrlC_Abort` for the safety-window teardown,
`refer_to_docs_and_quit` for user-facing fatal errors). `make-instance.py`
imports individual functions from here rather than the module as a whole —
when adding a new helper, add the corresponding `from aux_data import ...`
line in `make-instance.py`.

Instance-type validity, CPU architecture (x86_64 vs. Graviton/ARM64), EBS
optimization/encryption support, and placement group strategies are **not**
hardcoded lists — `get_instance_type_info(instance_type, region)` calls
`ec2:DescribeInstanceTypes` and returns them as ground truth from the AWS
API. This replaced a set of hand-maintained 2019-era allowlists
(`ec2_instances_full_list` and friends) that went stale and blocked every
Graviton instance type outright; the API-driven approach needs no updates
when AWS ships new instance families. `base_os` values are architecture-
agnostic except Windows, which AWS does not publish ARM64 AMIs for —
`base_os_instance_check()` rejects any `windows*` + Graviton combination
before `get_ami_info()` is ever called. `get_ami_info(base_os, region,
architecture)` and `check_custom_ami(custom_ami, aws_account_id, region,
architecture)` both take the detected architecture and select the matching
AMI; per-OS `Name` filters were adjusted so the `architecture` API filter
does the real narrowing (vendors encode arch differently in AMI names —
`x86_64`/`arm64` for AL2023, `amd64`/`arm64` for Ubuntu, `x86_64`/`aarch64`
for Rocky/AlmaLinux — so those tokens are wildcarded out of the `Name`
filter rather than hardcoded per architecture). `get_ami_info()` itself is
data-driven — `_AMI_CATALOG` maps each `base_os` to its `(owner,
name_pattern)` pair, and one shared `describe_images` call does the actual
lookup; adding a 14th `base_os` means adding a catalog entry, not a new
copy-pasted `if` block.

`BASE_OS_FAMILIES` / `get_base_os_family(base_os)` is the single source of
truth for `is_windows`, `package_manager` (`"yum"`/`"apt"`/`None`),
`ec2_user`, and `awscli_preinstalled`. Before this existed, each of these
facts was re-derived independently via `base_os` substring matching in
three unsynchronized places: a 6-branch `ec2_user` if-chain and several
`"windows" in base_os` checks in `make-instance.py`, *and* the same
`'windows' in base_os` / yum-vs-apt / family-OR-chain conditionals
duplicated again in Jinja across `DEFAULT_EC2_TEMPLATE.j2`,
`access_instance.j2`, and `build_instance.j2`.
`make-instance.py` now calls `get_base_os_family()` once (right after
`base_os_instance_check()`), computes `is_windows`/`package_manager`/
`awscli_preinstalled`/`ec2_user` from it, and threads all four into
`instance_parameters` — so templates read `{% if is_windows %}` /
`{% if package_manager == 'yum' %}` instead of re-deriving the same
substring checks a second time. When adding a new `base_os`, add one entry
to `BASE_OS_FAMILIES` (and one to `_AMI_CATALOG` above) rather than hunting
down every place that needs to know about it.

**`instance_builder.py`** holds the build-flow logic extracted out of
`make-instance.py`'s original ~1200-line, function-free linear script (see
CLAUDE-STATE.md for the extraction history). Every function here takes its
dependencies as explicit arguments — no hidden globals, no reliance on
`make-instance.py`'s execution order — and is covered by
`tests/test_instance_builder.py` (180+ tests). Covers: AZ/region
validation, instance serial number / SNS timestamp generation, EBS
size/IOPS validation, spot-price lookup, VPC/subnet/security-group
resolution, AMI resolution, EC2 keypair setup, the full IAM role/policy/
instance-profile setup (both the "create a new role" and "use a
pre-existing role" paths, including the previously-duplicated instance-
profile-creation logic now shared between them), the SNS notification
body, Terraform apply, security-group tagging, Windows instance-details/
Administrator-password retrieval, and vars-file writing.

**Deliberately left inline in `make-instance.py`, not extracted:** the
`instance_parameters` dict literal itself (a direct mapping of already-
extracted-and-tested local variables — wrapping it in a function would add
indirection without a testability gain), the thin
`template_engine.render_instance_templates()` call, and pure `print()`-only
blocks (e.g. the kill-instance/build-ami command reminders) with no logic
to test. If you're looking for where the *next* extraction should go,
these are exactly the kinds of blocks that don't need one.

**`template_engine.py`** renders `templates/*.j2` with plain Jinja2. Two
templates (`DEFAULT_EC2_TEMPLATE.j2`, `build_ami.j2`) use an Ansible-style
`bool` filter and every template but `instance_userdata.j2` uses
`lookup('pipe', ...)` for a build-date comment — both are small shims
registered on the `jinja2.Environment` here (`_bool_filter`, `_lookup`), not
supported natively by Jinja2. When editing a template, keep using only
these two non-native constructs (or extend the shims) rather than reaching
for other Ansible-only filters/tests — they won't be available.

**`templates/*.j2`** — the Terraform/shell/Python templates themselves. Key
ones:
- `DEFAULT_EC2_TEMPLATE.j2` — the Terraform EC2 resource definition.
- `provider_aws.j2`, `build_instance.j2`, `access_instance.j2`,
  `kill_instance.j2`, `build_ami.j2`, `instance_userdata.j2` — per-instance
  generated Terraform/shell/Python artifacts.
- `ssm_provision.j2` — the script `DEFAULT_EC2_TEMPLATE.j2`'s `local-exec`
  provisioner runs during `terraform apply`; waits for SSM Agent
  registration, then runs `build_instance.sh` and any selected
  `custom_user_postboot_script.j2_<name>` scripts via `aws ssm
  send-command`/`get-command-invocation` (one send-command call per
  script, so each runs in its own isolated shell session, matching the
  isolation the old per-script SSH provisioners had). Linux only, same as
  `build_instance.j2`/`instance_userdata.j2` — Windows has neither `user_data`
  nor any build-time provisioner at all.
- `*Ec2InstancePolicy.json` — IAM policy document choices
  (Minimal/Generic/Extended/Admin) selectable via `--iam_json_policy`; these
  are staged and rewritten per-instance (see `modify_iam_policy_document` in
  `aux_data.py`) to apply the `--iam_name_prefix`.

**`custom_user_scripts/`** is the user-owned customization point — a
separate top-level directory from `templates/` on purpose, so the
toolkit-controlled and user-controlled halves don't mix. Selected via
`--custom_user_scripts` (comma-separated list of names, default `default`);
each name resolves to `custom_user_prelogin_script.j2_<name>` (cloud-init,
before first login — content gets embedded into `instance_userdata.j2`'s
`write_files`/`runcmd`) and/or `custom_user_postboot_script.j2_<name>`
(pushed and run by `DEFAULT_EC2_TEMPLATE.j2`'s SSH remote-exec provisioner,
after `build_instance.sh`) — a name needs at least one of the two, and a
name matching neither is a hard failure
(`instance_builder.resolve_custom_user_scripts()`), not a silent no-op. Both
are real Jinja2 templates with the same variables every other template
gets. See `custom_user_scripts/README.md` for the full explanation of what
runs when and why, including why this is Linux-only (Windows has no
equivalent hook today — a known, documented gap, not an oversight).

**Generated, gitignored state** (never hand-edit; these are regenerated or
deleted by the scripts): `vars_files/`, `instance_data/`, `active_instances/`
(serial-number tracking files), and the root-level `kill-instance.*.sh` /
`build-ami.*.sh` symlinks. Cleaning these up is what `kill-instance.<name>.sh`
does — it deletes the EC2/IAM/SNS/security-group resources it tagged, then
deletes its own generated files including itself.

**`access_instance.py`** is a thin dispatcher: it just execs the
per-instance `instance_data/<name>/access_instance.<name>.py` that
`template_engine.py` generated from `templates/access_instance.j2`, so
instance-access behavior lives in that template, not in the top-level
script. Access goes through **AWS Systems Manager Session Manager**
(`aws ssm start-session`), not direct SSH/RDP: Linux connects with a
menu for families; Windows decrypts the Administrator password as before
but tunnels RDP through an SSM port-forwarding session
(`AWS-StartPortForwardingSession`, local port `13389`) instead of
requiring 3389 reachable from anywhere. This requires the Session Manager
plugin for the AWS CLI installed locally (separate from the CLI itself) and
the SSM Agent running on the instance — the instance's IAM role needs the
`AllowAccessToSSM` statement's `ssmmessages:*`/`ec2messages:*`/
`ssm:UpdateInstanceInformation` actions (present in all three
`*Ec2InstancePolicy.json` tiers). RHEL 9/10 and Rocky Linux 9/10's standard
AMIs don't preinstall the SSM Agent (unlike AL2023/Ubuntu/AlmaLinux/Windows,
per AWS's own docs) — `instance_userdata.j2` installs and enables it via
cloud-init for those four `base_os` values.

**`manage_instance.py`** starts/stops/reboots/terminates a previously-built
instance or family: `-n <instance_name> -a start|stop|reboot|terminate
[-c]` (`-c` skips the confirmation prompt). Boto3-direct against live AWS —
unlike `access_instance.py`/`kill-instance.<name>.sh`, it doesn't need
`instance_data/<name>/` to exist locally for `start`/`stop`/`reboot`. It
finds the instance(s) via `ec2:DescribeInstances`, filtered on `tag:Name`
(matching `<instance_name>` or `<instance_name>-*`, so one filter covers
both a single instance and a family) **and** `tag:ManagedBy =
Ec2InstanceMaker` — the safety check that keeps this from ever touching an
instance this toolkit didn't create, even if its `Name` tag happens to
collide with something else. `--region`/`-r` is optional; if omitted, it's
read from the `region:` field already recorded in
`./vars_files/<instance_name>.yml`. `-a terminate` does **not** call
`ec2:TerminateInstances` directly — it delegates entirely to
`./kill-instance.<instance_name>.sh` (which does exist only when
`instance_data/<name>/` is present), since a bare terminate call would
leave the security group, IAM role/policy/profile, SNS topic, and local
state behind. The `ManagedBy = "Ec2InstanceMaker"` tag this all depends on
is set in `DEFAULT_EC2_TEMPLATE.j2`'s `tags`/`volume_tags` blocks and the
spot-tagging `local-exec` command (needed there too — `aws_spot_instance_request`'s
own tags don't propagate to the instance it launches; that's what the
existing `create-tags` local-exec block is for) — keep it in all three
spots if either changes.

## Working conventions specific to this repo

- Resource lifecycle and identity hinges on `instance_serial_number`
  (timestamp + region-derived) — it's the tag value used to find everything
  belonging to one instance/family for teardown, so any new AWS resource
  type added to the create path must also be tagged with it and handled in
  the corresponding `kill_instance.j2` logic.
- IAM/SNS/security-group entity names are prefixed with `--iam_name_prefix`
  (default `Ec2InstanceMaker`) to let DevOps teams scope permissions; don't
  hardcode the `Ec2InstanceMaker` prefix in new code — thread the prefix
  through like the existing code does.
- Validation failures should go through `p_fail`/`refer_to_docs_and_quit`
  (aux_data.py) for consistent operator-facing error formatting, not bare
  `print`+`sys.exit`.
- The security group's SSH/RDP ingress rule is always scoped by
  `--ssh_allowed_ips` (`resolve_ssh_allowed_ips()` in `instance_builder.py`)
  — default is the instance's own VPC CIDR (looked up via `describe_vpcs`),
  and `0.0.0.0/0` is refused outright with a hard failure, not silently
  accepted. Never hardcode `0.0.0.0/0` for a new ingress rule; thread
  `ssh_allowed_ips` through like the existing rule does. This exists as
  defense-in-depth alongside SSM Session Manager (see `access_instance.py`
  above) — direct SSH/RDP is still possible for operators who want it, just
  never wide open.

## License

Apache License 2.0 with the Commons Clause restriction (withholds the
right to *sell* the software; the Licensor's clarification permits
charging for professional services performed on a customer's own behalf)
— source-available, not open source. Full terms in `LICENSE`.
`CONTRIBUTING.md` covers contribution terms (inbound license matches
outbound, plus a relicensing grant to the Licensor — without it, the
Commons Clause's Sell exception could never be exercised for the project
as a whole) and required DCO sign-off (`git commit -s`, certificate text
in `DCO.md`). AI-assisted contributions have their own disclosure policy
in `AI_POLICY.md` — see "AI Submissions" below for what additionally
governs an AI agent operating in this repo specifically.

`LICENSE`/`CONTRIBUTING.md`/`DCO.md`/`AI_POLICY.md` were brought over from
the sibling `../ParallelClusterMaker` repo (same author, same scheme) and
then corrected for this repo — the initial copy carried that project's
specific details verbatim (wrong project name, wrong issues URL, an
`aws-parallelcluster`/Node.js/AWS CDK toolchain description, `make
test`/`make lint`/`make shellcheck` targets with no `Makefile` here, a
false claim about `tests/conftest.py` enforcing AWS isolation at the
botocore HTTP layer, an irrelevant Slurm/`sacct` style exception, and
`main`-branch references for a repo whose default branch is `master`); see
CLAUDE-STATE.md for the full list as originally found. This file's own
"Linting and CI" section above and `tests/test_*.py` remain the actual
source of truth for how this repo tests and lints, independent of
`CONTRIBUTING.md`.

## Git

- **Never `git commit` or `git push` without explicit confirmation from the
  user first**, even mid-task and even if the work seems complete. Do not ask
  about committing proactively during work — wait to be asked, and when asked,
  confirm scope before running the command.
- Do not amend existing commits unless explicitly asked.

## AI Submissions

- Contributor-facing policy lives in `AI_POLICY.md` — read it before any
  change involving AI assistance. This section only adds what governs
  operating as an AI agent in this repo; it does not restate that file.
- **Never open a GitHub issue or PR without explicit user confirmation**,
  the same rule as `git commit`/`git push` above — proposing a change is
  fine, filing it is not.
- Commits that used AI assistance carry `Co-Authored-By: <Tool/Model>
  <email>`, matching this repo's existing convention (e.g.
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`) — do not invent a
  different trailer format.
