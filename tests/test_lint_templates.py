"""Unit tests for scripts/lint_templates.py's own lint_shell/lint_python/
lint_terraform_dir functions -- this script is the safety net that's
supposed to catch broken generated output (including template-injection
regressions like the one fixed in template_engine.py's hcl_escape
filter), so these prove the safety net actually reports failure on bad
input, not just that it's been observed to pass on good input via the
script's own real runs against templates/*.j2.

terraform init/validate (network-dependent, slow) is intentionally not
exercised here -- that's already covered by the separate
render-and-lint-templates pre-commit hook, which runs the real script
against all 14 CONTEXTS scenarios. Only the fast, offline `terraform fmt
-check` path is tested directly.
"""

import shutil

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
