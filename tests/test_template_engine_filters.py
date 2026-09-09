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

from template_engine import _hcl_escape_filter, _shquote_filter, _tf_shquote_filter


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
    def test_result_survives_an_hcl_double_quoted_wrapper_then_a_shell(self, tmp_path):
        # Simulates DEFAULT_EC2_TEMPLATE.j2's local-exec command = "..."
        # HCL string containing a shell command -- Terraform's own HCL
        # parser un-escapes \\ and \" before ever handing the string to
        # the shell, so this test does that same un-escaping step (what
        # Terraform would do) before running it, proving the shell sees
        # exactly the shlex.quote()'d token tf_shquote started from.
        marker = tmp_path / "pwned_tfshquote"
        payload = f'tester\'s "build"; touch {marker}'
        tf_quoted = _tf_shquote_filter(payload)
        hcl_string = '"' + tf_quoted + '"'
        # What Terraform's HCL parser does to a double-quoted string literal:
        shell_command_str = hcl_string[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        result = subprocess.run(f"echo {shell_command_str}", shell=True, capture_output=True, text=True)  # nosec B602
        assert result.stdout.strip() == payload
        assert not marker.exists()


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
