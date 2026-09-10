"""Unit tests for scripts/lint_templates.py's own lint_shell/lint_python/
lint_terraform_dir functions -- this script is the safety net that's
supposed to catch broken generated output (including template-injection
regressions like the one fixed in template_engine.py's hcl_escape
filter), so these prove the safety net actually reports failure on bad
input, not just that it's been observed to pass on good input via the
script's own real runs against templates/*.j2.

A real terraform init/validate (network-dependent, slow) is intentionally
not exercised here -- that's already covered by the separate
render-and-lint-templates pre-commit hook, which runs the real script
against all 15 CONTEXTS scenarios. The fast, offline `terraform fmt
-check` path is tested directly against the real binary; the
cached_init_dir branching (copy an already-initialized .terraform/
instead of running init again -- see lint_terraform_dir()) is tested with
subprocess.run mocked out entirely, since the point there is proving
*this* function's control flow, not re-testing terraform's own behavior.
"""

import shutil
import subprocess

import pytest

from scripts.lint_templates import lint_python, lint_shell, lint_terraform_dir


class TestLintShell:
    def test_valid_shell_script_passes(self, tmp_path):
        script = tmp_path / "good.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        ok, output = lint_shell(str(script))
        assert ok, output

    def test_broken_shell_script_fails(self, tmp_path):
        script = tmp_path / "bad.sh"
        # Unquoted command substitution feeding a case that's never closed --
        # a real shellcheck error (SC1073/SC1009-class), not a style nit.
        script.write_text("#!/bin/bash\ncase $1 in\n  foo)\n    echo hi\n")
        ok, output = lint_shell(str(script))
        assert not ok
        assert output.strip() != ""


class TestLintPython:
    def test_valid_python_passes(self, tmp_path):
        script = tmp_path / "good.py"
        script.write_text("x = 1\nprint(x)\n")
        ok, output = lint_python(str(script))
        assert ok, output

    def test_syntax_error_fails(self, tmp_path):
        script = tmp_path / "bad_syntax.py"
        script.write_text("def broken(:\n    pass\n")
        ok, output = lint_python(str(script))
        assert not ok
        assert output.strip() != ""

    def test_undefined_name_fails(self, tmp_path):
        # py_compile alone wouldn't catch this (it's syntactically valid) --
        # this is what the ruff F821 pass exists for.
        script = tmp_path / "bad_undefined.py"
        script.write_text("print(this_name_was_never_defined)\n")
        ok, output = lint_python(str(script))
        assert not ok
        assert output.strip() != ""


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
class TestLintTerraformDir:
    def test_correctly_formatted_tf_passes_fmt_check(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "null_resource" "example" {\n}\n')
        failures = lint_terraform_dir(str(tmp_path))
        fmt_failures = [f for f in failures if f[0] == "terraform fmt"]
        assert fmt_failures == []

    def test_misformatted_tf_fails_fmt_check(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource   "null_resource"    "example" {\n      }\n')
        failures = lint_terraform_dir(str(tmp_path))
        fmt_failures = [f for f in failures if f[0] == "terraform fmt"]
        assert len(fmt_failures) == 1
        assert fmt_failures[0][1].strip() != ""


class TestLintTerraformDirCachedInit:
    """cached_init_dir lets lint_terraform_dir() skip a real `terraform
    init` (~8s of subprocess overhead even with a warm plugin cache,
    confirmed by timing it directly -- see the comment on
    lint_terraform_dir()) by copying an already-initialized .terraform/ +
    lock file from elsewhere instead. subprocess.run is mocked out
    entirely here, unlike the fmt tests above -- terraform's own behavior
    is already proven by the real scripts/lint_templates.py run against
    all 15 CONTEXTS scenarios; this only needs to prove *this* function
    takes the copy branch instead of the init branch when given one.
    """

    def _fake_run(self, calls):
        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["terraform", "fmt"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["terraform", "init"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["terraform", "validate"]:
                return subprocess.CompletedProcess(args, 0, '{"valid": true, "diagnostics": []}', "")
            raise AssertionError(f"unexpected subprocess call: {args}")

        return run

    def test_copies_cached_init_instead_of_running_terraform_init(self, tmp_path, monkeypatch):
        cached_dir = tmp_path / "cached"
        (cached_dir / ".terraform" / "providers").mkdir(parents=True)
        (cached_dir / ".terraform" / "providers" / "marker.txt").write_text("fake provider")
        (cached_dir / ".terraform.lock.hcl").write_text("# fake lock file\n")

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "main.tf").write_text('resource "null_resource" "example" {\n}\n')

        calls: list[list[str]] = []
        monkeypatch.setattr("scripts.lint_templates.subprocess.run", self._fake_run(calls))

        failures = lint_terraform_dir(str(target_dir), cached_init_dir=str(cached_dir))

        assert failures == []
        assert not any(c[:2] == ["terraform", "init"] for c in calls)
        assert (target_dir / ".terraform" / "providers" / "marker.txt").read_text() == "fake provider"
        assert (target_dir / ".terraform.lock.hcl").read_text() == "# fake lock file\n"

    def test_real_init_path_still_used_when_no_cache_given(self, tmp_path, monkeypatch):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "main.tf").write_text('resource "null_resource" "example" {\n}\n')

        calls: list[list[str]] = []
        monkeypatch.setattr("scripts.lint_templates.subprocess.run", self._fake_run(calls))

        failures = lint_terraform_dir(str(target_dir))

        assert failures == []
        assert any(c[:2] == ["terraform", "init"] for c in calls)

    def test_init_failure_short_circuits_before_validate(self, tmp_path, monkeypatch):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "main.tf").write_text('resource "null_resource" "example" {\n}\n')

        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["terraform", "fmt"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["terraform", "init"]:
                return subprocess.CompletedProcess(args, 1, "", "network unreachable")
            raise AssertionError(f"unexpected subprocess call: {args}")

        monkeypatch.setattr("scripts.lint_templates.subprocess.run", run)

        failures = lint_terraform_dir(str(target_dir))

        assert len(failures) == 1
        assert failures[0][0] == "terraform init"
        assert not any(c[:2] == ["terraform", "validate"] for c in calls)
