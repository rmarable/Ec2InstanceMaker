"""Tests for make_instance.print_debug_parameters().

This function had no coverage at all, which matters more than a
print()-only function usually would: it reads ~65 fields off the
InstanceParameters dataclass *by attribute name*. Rename or remove a field
without updating it here and the result is an AttributeError that fires
only under --debug_mode=true -- so it ships, and it breaks for the one
person who was already debugging something else.
"""

import dataclasses

import pytest

import make_instance
from instance_builder import InstanceParameters

# Fields whose value is a bool/int/list rather than a str, so the helper
# below has to supply something of the right shape.
_NON_STRING_DEFAULTS = {
    "count": 3,
    "ebs_root_volume_size": 8,
    "ebs_device_volume_size": 8,
    "ebs_root_volume_iops": 0,
    "ebs_device_volume_iops": 0,
    "log_retention_days": 30,
    "is_windows": False,
    "awscli_preinstalled": True,
    "custom_user_prelogin_scripts": ["default"],
    "custom_user_postboot_scripts": ["default"],
}

# Values that drive the conditional branches; the defaults here pick the
# branch that prints the most, so the happy path exercises the widest set.
_MEANINGFUL_DEFAULTS = {
    "instance_name": "dev01",
    # Both of these are hidden when they hold their sentinel value, so the
    # fixture uses real ones -- the widest-printing branch. The sentinel
    # behaviour is covered explicitly in TestConditionalBranches.
    "turbot_account": "acct-000000000000",
    "project_id": "apollo-17",
    "enable_placement_group": "true",
    "placement_group_strategy": "cluster",
    "enable_cloudwatch_logs": "true",
    "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev01-000000010926",
    "ec2_iam_instance_policy": "Ec2InstanceMaker-policy-dev01-000000010926",
    "debug_mode": "true",
    "package_manager": "yum",
}


def _full_instance_parameters(**overrides):
    """Build a complete InstanceParameters.

    Driven off dataclasses.fields() rather than a hand-written literal, so
    adding a 66th field to the dataclass cannot silently leave this fixture
    stale -- it just gets a placeholder and the drift test below decides
    whether that is acceptable.
    """
    values = {}
    for field in dataclasses.fields(InstanceParameters):
        if field.name in _NON_STRING_DEFAULTS:
            values[field.name] = _NON_STRING_DEFAULTS[field.name]
        elif field.name in _MEANINGFUL_DEFAULTS:
            values[field.name] = _MEANINGFUL_DEFAULTS[field.name]
        else:
            values[field.name] = f"<{field.name}>"
    values.update(overrides)
    return InstanceParameters(**values)


class TestEveryFieldIsReachable:
    def test_a_fully_populated_dataclass_prints_without_raising(self, capsys):
        # The AttributeError guard. This is the whole reason the file exists.
        make_instance.print_debug_parameters(_full_instance_parameters())
        assert capsys.readouterr().out

    def test_every_dataclass_field_appears_in_the_dump(self, capsys):
        # Drift detector: adding a field to InstanceParameters without
        # printing it here should be a deliberate decision, not an
        # oversight. 16 fields were unprinted before this test existed,
        # including ssh_allowed_ips -- which is exactly what you want to see
        # when debugging why an instance is or is not reachable.
        make_instance.print_debug_parameters(_full_instance_parameters())
        out = capsys.readouterr().out
        missing = [f.name for f in dataclasses.fields(InstanceParameters) if f.name not in out]
        assert not missing, f"not printed by --debug_mode: {missing}"

    def test_the_fixture_covers_every_field(self):
        # Guards the guard: if the helper ever stops supplying a field,
        # InstanceParameters(**values) would raise and the test above would
        # fail for the wrong reason.
        supplied = set(dataclasses.asdict(_full_instance_parameters()))
        assert supplied == {f.name for f in dataclasses.fields(InstanceParameters)}


class TestConditionalBranches:
    def _out(self, capsys, **overrides):
        make_instance.print_debug_parameters(_full_instance_parameters(**overrides))
        return capsys.readouterr().out

    @staticmethod
    def _printed(out, key):
        # Line-anchored: a bare substring check for "count = " also matches
        # "turbot_account = ", which is the kind of false pass that makes a
        # test look like it is asserting something when it is not.
        return any(line.startswith(key + " = ") for line in out.splitlines())

    def test_turbot_account_is_hidden_when_disabled(self, capsys):
        assert not self._printed(self._out(capsys, turbot_account="DISABLED"), "turbot_account")

    def test_turbot_account_is_shown_when_set(self, capsys):
        assert self._printed(self._out(capsys, turbot_account="acct-123"), "turbot_account")

    def test_count_is_hidden_for_a_single_instance(self, capsys):
        assert not self._printed(self._out(capsys, count=1), "count")

    def test_count_is_shown_for_a_family(self, capsys):
        assert self._printed(self._out(capsys, count=3), "count")

    def test_project_id_is_hidden_when_undefined(self, capsys):
        assert not self._printed(self._out(capsys, project_id="UNDEFINED"), "project_id")

    def test_project_id_is_shown_when_set(self, capsys):
        assert self._printed(self._out(capsys, project_id="apollo-17"), "project_id")

    def test_placement_group_strategy_only_appears_when_enabled(self, capsys):
        assert not self._printed(self._out(capsys, enable_placement_group="false"), "placement_group_strategy")
        assert self._printed(self._out(capsys, enable_placement_group="true"), "placement_group_strategy")

    def test_cloudwatch_detail_only_appears_when_logging_is_enabled(self, capsys):
        off = self._out(capsys, enable_cloudwatch_logs="false")
        assert "enable_cloudwatch_logs = false" in off
        assert not self._printed(off, "log_retention_days")
        on = self._out(capsys, enable_cloudwatch_logs="true")
        assert self._printed(on, "log_retention_days")

    def test_iam_entities_are_skipped_when_no_instance_profile_exists(self, capsys):
        out = self._out(capsys, ec2_iam_instance_profile="")
        assert not self._printed(out, "ec2_iam_instance_role")
        assert not self._printed(out, "preserve_iam_role")

    def test_an_undefined_iam_policy_is_skipped_but_the_rest_is_not(self, capsys):
        out = self._out(capsys, ec2_iam_instance_policy="UNDEFINED")
        assert not self._printed(out, "ec2_iam_instance_policy")
        assert self._printed(out, "ec2_iam_instance_role")


class TestSecurityRelevantFieldsAreVisible:
    """--debug_mode exists to answer "why did this build do that". These
    are the fields most often needed for that and all of them were
    previously absent.
    """

    @pytest.mark.parametrize(
        "field, expected",
        [
            ("ssh_allowed_ips", "ssh_allowed_ips = 10.0.0.0/16"),
            ("iam_name_prefix", "iam_name_prefix = CorpPrefix"),
            ("instance_data_dir", "instance_data_dir = ./instance_data/dev01/"),
        ],
    )
    def test_field_is_printed(self, capsys, field, expected):
        make_instance.print_debug_parameters(_full_instance_parameters(ssh_allowed_ips="10.0.0.0/16", iam_name_prefix="CorpPrefix", instance_data_dir="./instance_data/dev01/"))
        assert expected in capsys.readouterr().out

    def test_selected_custom_user_scripts_are_listed(self, capsys):
        make_instance.print_debug_parameters(_full_instance_parameters(custom_user_prelogin_scripts=["default", "motd"]))
        assert "custom_user_prelogin_scripts = default, motd" in capsys.readouterr().out
