"""Unit tests for template_engine.py's three escaping filters
(shquote/tf_shquote/hcl_escape) -- these are the security-critical shims
that make it safe to interpolate free-text operator input (instance_owner_
email/instance_owner_department/project_id, none of which are restricted
to a safe charset the way instance_name/instance_owner are) into generated
shell scripts and Terraform config. An adversarial review found and fixed
a real injection because one interpolation site used none of these
filters at all (see TestHclInjectionRegression in
tests/test_template_behavior.py) -- these tests exist so a future edit
that weakens or bypasses one of these filters gets caught here, not
discovered live again.

Each test proves the filter's output is actually safe in the context it's
designed for (a real shell subprocess call, or a string embedded in a
larger double-quoted context), not just that it matches an expected
string -- string-equality alone wouldn't catch a subtly-wrong escaping
scheme that happens to produce a different-but-still-broken result.
"""

import subprocess

import jinja2
import pytest

from template_engine import _bool_filter, _hcl_escape_filter, _make_environment, _shquote_filter, _tf_shquote_filter


class TestShquoteFilter:
    def test_safe_for_direct_shell_interpolation(self, tmp_path):
        # The payload's "touch ..." text is meant to be echoed back as
        # inert literal text, not executed -- checking for the marker
        # FILE's absence (not a substring of stdout, which legitimately
        # contains this text either way) is what actually proves no
        # command injection happened.
        marker = tmp_path / "pwned_shquote"
        payload = f"tester; touch {marker}; echo"
        quoted = _shquote_filter(payload)
        result = subprocess.run(f"echo {quoted}", shell=True, capture_output=True, text=True)  # nosec B602 - test-only, fixed command string
        assert result.stdout.strip() == payload
        assert not marker.exists()

    def test_embedded_single_quote(self):
        payload = "O'Brien"
        quoted = _shquote_filter(payload)
        result = subprocess.run(f"echo {quoted}", shell=True, capture_output=True, text=True)  # nosec B602
        assert result.stdout.strip() == payload

    def test_dollar_and_backtick_are_not_expanded(self, tmp_path):
        marker2 = tmp_path / "pwned_shquote2"
        marker3 = tmp_path / "pwned_shquote3"
        payload = f"$(touch {marker2}) `touch {marker3}`"
        quoted = _shquote_filter(payload)
        result = subprocess.run(f"echo {quoted}", shell=True, capture_output=True, text=True)  # nosec B602
        assert result.stdout.strip() == payload
        assert not marker2.exists()
        assert not marker3.exists()

    def test_non_string_input_is_stringified(self):
        assert _shquote_filter(123) == "123"


class TestTfShquoteFilter:
    @staticmethod
    def _hcl_unquote(hcl_string):
        """Un-escape an HCL double-quoted string literal the way Terraform's
        own parser does: consume the opening quote, resolve backslash
        escapes, and STOP at the first *unescaped* closing quote. Returns
        (value, trailing) so a caller can assert nothing escaped the
        literal.

        This used to be `hcl_string[1:-1].replace(...)`, which blindly
        assumed the literal was well-formed. That made the test vacuous:
        a payload that closed the string early round-tripped unchanged,
        and deleting tf_shquote's entire HCL-escaping pass still left the
        suite green (verified by mutation testing during an adversarial
        review). Terminating at the first unescaped quote is the whole
        property being tested, so the simulated parser has to model it.
        """
        assert hcl_string.startswith('"')
        out = []
        i = 1
        while i < len(hcl_string):
            char = hcl_string[i]
            if char == "\\":
                out.append(hcl_string[i + 1])
                i += 2
                continue
            if char == '"':
                return "".join(out), hcl_string[i + 1 :]
            out.append(char)
            i += 1
        raise AssertionError("unterminated HCL string literal: " + hcl_string)

    def test_result_survives_an_hcl_double_quoted_wrapper_then_a_shell(self, tmp_path):
        # Simulates DEFAULT_EC2_TEMPLATE.j2's local-exec command = "..."
        # HCL string containing a shell command.
        marker = tmp_path / "pwned_tfshquote"
        payload = f'tester\'s "build"; touch {marker}'
        tf_quoted = _tf_shquote_filter(payload)
        # The escaping pass must actually have happened -- without this
        # assertion the test passes on a filter that does nothing but
        # shlex.quote().
        assert '\\"' in tf_quoted
        shell_command_str, trailing = self._hcl_unquote('"' + tf_quoted + '"')
        # Nothing escaped the HCL string literal.
        assert trailing == ""
        result = subprocess.run(f"echo {shell_command_str}", shell=True, capture_output=True, text=True)  # nosec B602
        assert result.stdout.strip() == payload
        assert not marker.exists()

    def test_hcl_interpolation_sequence_is_neutralized(self):
        # The real defect this closes: Terraform expands "${...}" in a
        # local-exec command string AFTER rendering and BEFORE handing it
        # to /bin/sh. An expansion result containing a single quote would
        # otherwise escape the '...' wrapping shlex.quote() put around the
        # value, turning operator free text into executed shell commands.
        tf_quoted = _tf_shquote_filter("${self.tags.InstanceOwnerDepartment}")
        assert "$${" in tf_quoted
        assert "${" not in tf_quoted.replace("$${", "")

    def test_hcl_directive_sequence_is_neutralized(self):
        # "%{ ... }" is HCL's other template sequence and evaluates just as
        # readily as "${ ... }".
        tf_quoted = _tf_shquote_filter("%{ for i in [1, 2] }x%{ endfor }")
        assert "%%{" in tf_quoted
        assert "%{" not in tf_quoted.replace("%%{", "")


class TestBoolFilter:
    """_bool_filter had no tests at all. It gates
    `encrypted = "{{ ebs_encryption | bool | lower }}"` in
    DEFAULT_EC2_TEMPLATE.j2 plus five tag sites, so a bug here silently
    flips EBS encryption on or off for every generated instance -- and
    scripts/lint_templates.py cannot see it, since the rendered .tf stays
    syntactically valid either way. Mutation testing confirmed the gap:
    making this filter return True unconditionally left the suite green.
    """

    def test_ansible_style_true_values(self):
        for value in ("true", "True", "TRUE", " true ", "yes", "t", "1", "on", True, 1):
            assert _bool_filter(value) is True, value

    def test_ansible_style_false_values(self):
        for value in ("false", "False", "FALSE", "no", "0", "off", "", None, False, 0, "banana"):
            assert _bool_filter(value) is False, value

    def test_rendered_form_is_the_lowercase_string_terraform_expects(self):
        # This is the actual `| bool | lower` chain the templates use.
        assert str(_bool_filter("True")).lower() == "true"
        assert str(_bool_filter("False")).lower() == "false"


class TestHclEscapeFilter:
    def test_plain_text_is_unchanged(self):
        assert _hcl_escape_filter("plain text") == "plain text"

    def test_embedded_double_quote_cannot_close_the_string_early(self):
        payload = 'tester"\n}\nresource "null_resource" "pwned" {\n  x = "y'
        escaped = _hcl_escape_filter(payload)
        # Simulate embedding the escaped value into a real HCL double-quoted
        # string literal, the same way DEFAULT_EC2_TEMPLATE.j2 does, and
        # confirm the quote is neutralized (still literally present as \").
        hcl_source = 'InstanceOwnerEmail = "' + escaped + '"'
        assert 'resource "null_resource" "pwned"' not in hcl_source
        assert '\\"' in escaped

    def test_backslash_is_escaped_before_quotes_are_added(self):
        assert _hcl_escape_filter("a\\b") == "a\\\\b"

    def test_interpolation_sequence_is_neutralized(self):
        assert _hcl_escape_filter("${aws_instance.evil.id}") == "$${aws_instance.evil.id}"

    def test_non_string_input_is_stringified(self):
        assert _hcl_escape_filter(42) == "42"


class TestNoShellExecutionInTheRenderPath:
    """The Jinja environment used to register a `lookup` global backed by
    subprocess.check_output(arg, shell=True). Its only purpose was stamping
    a build-date comment, but it made arbitrary shell execution reachable at
    *render* time -- before Terraform runs and before the CTRL-C window --
    from any template on the loader path. That path includes
    custom_user_scripts/, the documented user-owned drop-in directory, so a
    shared template could run commands on the operator's workstation just by
    being rendered.
    """

    def test_lookup_global_is_not_registered(self):
        assert "lookup" not in _make_environment(".").globals

    def test_a_template_calling_lookup_fails_instead_of_executing(self, tmp_path):
        env = _make_environment(".")
        marker = tmp_path / "pwned_render_time"
        template = env.from_string("{{ lookup('pipe','touch " + str(marker) + "') }}")
        # 'lookup' is simply not a name in the environment any more.
        with pytest.raises(jinja2.UndefinedError):
            template.render()
        assert not marker.exists()

    def test_template_engine_does_not_import_subprocess(self):
        import template_engine

        assert not hasattr(template_engine, "subprocess")
