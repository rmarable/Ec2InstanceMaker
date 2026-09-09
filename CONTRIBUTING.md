# Contributing to Ec2InstanceMaker

Thanks for your interest.  This document covers what you need to know before
opening an issue or a pull request.

## Before you start

For anything larger than a bug fix, open an issue first and describe what you
want to change.  This toolkit provisions billable AWS infrastructure and
carries a lot of hard-won constraints that are not obvious from the code —
a short conversation up front is cheaper than a rejected pull request.

Bugs and features: https://github.com/rmarable/Ec2InstanceMaker/issues

## Licensing of contributions

This project is **source-available, not open source**: Apache License 2.0
with the Commons Clause restriction, which withholds the right to sell.  See
`LICENSE` for the full terms, including the Licensor's clarification
permitting fees for professional services.

By submitting a contribution you agree that:

- Your contribution is licensed to the project under the same terms as
  `LICENSE` — inbound matches outbound.
- You grant Rodney Marable a perpetual, worldwide, non-exclusive,
  royalty-free, irrevocable license to use, reproduce, modify, distribute,
  sublicense, and relicense your contribution, **including under terms that
  permit sale**.

The second point exists for a specific reason.  The Commons Clause reserves
the right to sell to the Licensor, who may grant it to others.  Without a
relicensing grant from contributors, that permission could not be granted for
the project as a whole — a single unlicensed patch would make part of the
codebase impossible to include, permanently.  The grant costs you nothing you
would otherwise retain: you keep the copyright in your work and may do
anything you like with it elsewhere.

If your employer owns your work, make sure you have permission to contribute
before you do.  If you are contributing on behalf of a company and need a
signed agreement rather than this clause, open an issue and say so.

## Sign your commits

Every commit must carry a `Signed-off-by` trailer certifying the Developer
Certificate of Origin.  Commit with `-s`:

```console
$ git commit -s -m "your message"
```

CI rejects a pull request with any unsigned commit.  See `DCO.md` for the
certificate text and how to sign off work you have already written.

## AI-assisted contributions

They are welcome, under a published policy: disclose the tool or model,
review every line you submit, and do not point an autonomous agent at the
issue tracker.  Read `AI_POLICY.md` before submitting — it is short, and
contributions that ignore it may be closed.

Note that `Co-Authored-By` (crediting a tool) and `Signed-off-by`
(certifying provenance) are different trailers doing different jobs.  A
commit written with AI assistance carries both; only a human can sign off.

## Development setup

**Python 3.12** is this project's documented minimum/default (see
`CLAUDE.md`).

```console
$ python3.12 -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements.txt
```

Also required, on `PATH`: `terraform` (any HCL2-capable version, i.e. 0.12 or newer; CI and the
template linter both run current 1.x. Note that nothing in the build
*enforces* a version -- `get_terraform_version()` only detects and prints
it. `linux-ec2-setup.sh` still installs 0.12.9, which is stale), `jq`,
and configured AWS credentials. `make_instance.py` and `template_engine.py`
both resolve paths (e.g. `templates/`) relative to the current working
directory, so commands must be run from the repo root. See `INSTALL.md`.

## The gate

`pre-commit run --all-files` must pass before a pull request is reviewed —
the same config runs locally (on `git commit`, once you've run
`pre-commit install`) and in CI (`.github/workflows/lint.yml`). See
`CLAUDE.md`'s "Linting and CI" section for the authoritative, current list
of what that covers (`pytest`, `ruff`, `bandit`, `shellcheck`,
`detect-secrets`, `pip-audit`, `scripts/lint_templates.py`'s template
render-and-lint pass, and `terraform fmt -check`) — don't rely on this file
for that list, it drifts; `CLAUDE.md` is what a change to the pre-commit
config gets kept in sync with.

## How this codebase tests

Worth knowing before you write a test here:

- **No test may reach AWS.** Tests mock `boto3` clients directly with
  `unittest.mock.MagicMock`/`patch` (see `tests/test_instance_builder.py`,
  `tests/test_aux_data_aws.py`) rather than stubbing at the HTTP layer.
- **Template behavior gets its own test class.**
  `scripts/lint_templates.py` (run via pre-commit) only checks that
  rendered `templates/*.j2` output is syntactically valid — it never
  asserts which conditional branch rendered or what it does.
  `tests/test_template_behavior.py` fills that gap with behavioral
  assertions on real rendered output.
- **A value stated on more than one surface needs a guard that they
  agree.** Copies drift. If you cannot generate the value from one source,
  pin the copies with a test.
- **A guard needs a vacuity guard.** A test that would pass even with the
  behavior removed is not protecting anything. Show that it fails when the
  thing it checks is broken.
- **Run the suite after any change to Python or the Jinja2 templates.**

`CLAUDE.md` documents the architectural constraints in detail.  It is written
for AI coding assistants but is the best available map of what will break if
you change it — read the sections touching whatever you are modifying.

## Style

- American English throughout — docs, comments, help text, error strings.
- No comments unless the *why* is non-obvious.
- No docstrings beyond a single short line.
- No backwards-compatibility shims.
- Prefer editing an existing file over creating a new one.
- No emojis.

## Submitting

Push your branch and open a pull request against `master`.

Describe what changed and why.  If you fixed a bug, say how you know it is
fixed; if the answer is a test, name it.
