# custom_user_scripts/

This is the user-owned customization point for Ec2InstanceMaker. Everything
in `templates/` is toolkit-controlled -- don't edit it to customize a build.
Everything in here is yours.

## The two hooks

There are two separate hooks, because they run at genuinely different points
with genuinely different constraints. Which one you want depends on what
your custom command needs to be true when it runs.

### `custom_user_prelogin_script.j2_<name>` -- PRE-LOGIN

- Runs via **cloud-init**, during boot, **before an operator can log in at
  all** -- this is the real "before first login" hook.
- Runs as **root**. `$HOME` is `/root`, not the instance owner's home
  directory.
- Runs **before** `build_instance.sh` (the toolkit's own postboot script)
  has done any of its own setup: no guarantee the package manager has been
  updated, no guarantee git/gcc/zip/unzip are installed, no guarantee of
  anything beyond what the base AMI ships -- unless you install it yourself,
  right here. (The one exception: the AWS CLI and, on RHEL/Rocky, the SSM
  Agent are guaranteed present by the time *your* prelogin script runs --
  the toolkit installs those first, if the base_os doesn't ship them
  preinstalled.)
- **Keep it fast.** `build_instance.sh` explicitly waits for cloud-init to
  report `boot-finished` before it does anything, so whatever runs here
  delays the *entire* postboot flow by however long it takes. Seconds, not
  minutes. A real software install belongs in the postboot hook instead.
- Good fits: a sysctl tweak, an `/etc/hosts` entry, a MOTD banner, a ulimit,
  disabling a service you never want running -- anything that should
  already be true by the time anyone logs in.

### `custom_user_postboot_script.j2_<name>` -- POST-BOOT

- Runs via Terraform's SSH `remote-exec` provisioner, automatically as part
  of the build -- but *after* cloud-init reports `boot-finished` and after
  `build_instance.sh` has already run: package manager updated/upgraded,
  git/gcc/zip/unzip installed, the AWS CLI confirmed present and its
  default region set, SSH keepalive configured. **SSH is already up by the
  time this runs** -- despite happening automatically as part of the
  build, this is not a before-login hook.
- Runs as the connecting SSH user (`ec2_user`), with passwordless `sudo`
  available -- not root directly.
- Good fits: real software installs, cloning a repo, per-user dotfiles/
  config -- anything heavier or longer-running than the prelogin hook
  should be, or anything that benefits from git/gcc/the AWS CLI already
  being guaranteed present.

## Naming and selection

Drop a file in as `custom_user_prelogin_script.j2_<name>` and/or
`custom_user_postboot_script.j2_<name>` -- the same `<name>` ties the two
together, but a name only needs *one* of them. A module that's just a
software install (no reason to run before login) can be postboot-only; a
module that's just a system tweak (no reason to run after boot) can be
prelogin-only.

Select which modules run with `--custom_user_scripts`, a comma-separated
list of names (default: `default`):

```
./make_instance.py ... --custom_user_scripts default
./make_instance.py ... --custom_user_scripts monitoring,R
```

Each selected name must match at least one of the two files, or the build
fails fast (before touching AWS) with a clear error -- this is meant to
catch a typo'd name immediately, not silently no-op. Multiple names run in
the order given, for both hooks independently.

## Available variables

Both files are rendered as real Jinja2 templates, with the same variables
every other template in this toolkit gets: `instance_name`, `instance_owner`,
`instance_owner_email`, `region`, `base_os`, `architecture`, `package_manager`
(`"yum"`/`"apt"`/`None`), `is_windows`, `ec2_user`, `ec2_user_src`, and
everything else in `instance_parameters` (see `make_instance.py`). See
`custom_user_prelogin_script.j2_default`/`custom_user_postboot_script.j2_default`
in this directory for a real example of the same
`{% if package_manager == 'yum' %}` pattern used throughout this toolkit's
own templates.

## Windows

**Not supported.** Both hooks are Linux-only today -- `is_windows` builds get
neither `user_data` (cloud-init doesn't apply to Windows the same way; EC2's
Windows path uses EC2Launch v2 with a completely different `<script>`/
`<powershell>` syntax) nor the SSH remote-exec provisioner that runs the
postboot hook. This is a known, real gap, not an oversight to be quietly
worked around -- Windows customization would need its own, separate
mechanism, not an extension of this one.
