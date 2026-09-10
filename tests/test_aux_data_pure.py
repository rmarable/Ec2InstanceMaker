"""Unit tests for aux_data.py functions that need no AWS calls.

These cover pure validation/formatting logic: base_os/instance_type
compatibility, EBS encryption/placement-group strategy checks, IAM policy
placeholder substitution, and small utilities. Several of these are
regression tests for bugs found and fixed during an adversarial review
(see CLAUDE-STATE.md) that previously had no automated coverage at all --
verification only ever happened via one-off interactive snippets.
"""

import json

import pytest

import aux_data


class TestGetBaseOsFamily:
    """get_base_os_family() is the single source of truth for is_windows/
    package_manager/ec2_user/awscli_preinstalled -- these tests exist to
    catch it drifting out of sync with what the rest of the codebase
    actually assumes (e.g. base_os_instance_check's windows+arm64 rule,
    the ec2_user values every prior base_os addition this session had to
    get right by hand).
    """

    def test_unrecognized_base_os_exits_cleanly(self):
        with pytest.raises(SystemExit):
            aux_data.get_base_os_family("centos7")

    @pytest.mark.parametrize(
        "base_os,expected_ec2_user",
        [
            ("al2023", "ec2-user"),
            ("alinux2", "ec2-user"),
            ("alma9", "ec2-user"),
            ("alma10", "ec2-user"),
            ("rhel9", "ec2-user"),
            ("rhel10", "ec2-user"),
            ("rocky9", "rocky"),
            ("rocky10", "rocky"),
            ("ubuntu2404", "ubuntu"),
            ("ubuntu2604", "ubuntu"),
            ("opensuse16", "ec2-user"),
            ("windows2019", "Administrator"),
            ("windows2022", "Administrator"),
            ("windows2025", "Administrator"),
        ],
    )
    def test_ec2_user_matches_every_base_os(self, base_os, expected_ec2_user):
        assert aux_data.get_base_os_family(base_os)["ec2_user"] == expected_ec2_user

    @pytest.mark.parametrize(
        "base_os,expected_is_windows",
        [(b, b.startswith("windows")) for b in aux_data.BASE_OS_FAMILIES],
    )
    def test_is_windows_matches_base_os_name(self, base_os, expected_is_windows):
        assert aux_data.get_base_os_family(base_os)["is_windows"] == expected_is_windows

    @pytest.mark.parametrize("base_os", ["ubuntu2404", "ubuntu2604"])
    def test_apt_family(self, base_os):
        assert aux_data.get_base_os_family(base_os)["package_manager"] == "apt"

    @pytest.mark.parametrize("base_os", ["al2023", "alinux2", "alma9", "alma10", "rhel9", "rhel10", "rocky9", "rocky10"])
    def test_yum_family(self, base_os):
        assert aux_data.get_base_os_family(base_os)["package_manager"] == "yum"

    @pytest.mark.parametrize("base_os", ["opensuse16"])
    def test_zypper_family(self, base_os):
        assert aux_data.get_base_os_family(base_os)["package_manager"] == "zypper"

    @pytest.mark.parametrize("base_os", ["al2023", "alinux2"])
    def test_awscli_preinstalled_only_on_amazon_linux(self, base_os):
        assert aux_data.get_base_os_family(base_os)["awscli_preinstalled"] is True

    @pytest.mark.parametrize("base_os", ["alma9", "alma10", "rhel9", "rhel10", "rocky9", "rocky10", "ubuntu2404", "ubuntu2604", "opensuse16"])
    def test_awscli_not_preinstalled_elsewhere_on_linux(self, base_os):
        assert aux_data.get_base_os_family(base_os)["awscli_preinstalled"] is False

    def test_every_base_os_family_has_all_four_attributes(self):
        required_keys = {"is_windows", "package_manager", "ec2_user", "awscli_preinstalled"}
        for base_os, family in aux_data.BASE_OS_FAMILIES.items():
            assert set(family.keys()) == required_keys, base_os


class TestBaseOsInstanceCheck:
    def test_valid_combo_passes(self):
        # Should not raise.
        aux_data.base_os_instance_check("al2023", "m5.large", "x86_64", "false")

    def test_windows_arm64_rejected(self, capsys):
        # Regression test: generic Windows+Graviton rejection added this
        # session, replacing the old a1.-only exclusion that missed every
        # other Graviton family (m6g, c6g, c8g, etc.).
        #
        # base_os_instance_check() has three distinct failure exits, so
        # asserting only that it exited would pass even if a change made it
        # reject this for entirely the wrong reason.
        with pytest.raises(SystemExit):
            aux_data.base_os_instance_check("windows2022", "m6g.xlarge", "arm64", "false")
        assert "does not support AWS Graviton" in capsys.readouterr().out

    def test_windows_x86_64_passes(self):
        aux_data.base_os_instance_check("windows2022", "m5.xlarge", "x86_64", "false")

    def test_opensuse16_graviton_passes(self):
        # Unlike Windows, openSUSE Leap 16.0 publishes a real arm64 AMI (a
        # separate "openSUSE Leap (ARM)" Marketplace listing -- verified
        # live, see aux_data.py's _AMI_CATALOG comment), so it must NOT hit
        # the windows-only Graviton rejection above.
        aux_data.base_os_instance_check("opensuse16", "m6g.large", "arm64", "false")

    def test_windows_f1_rejected(self, capsys):
        with pytest.raises(SystemExit):
            aux_data.base_os_instance_check("windows2019", "f1.2xlarge", "x86_64", "false")
        out = capsys.readouterr().out
        assert "does not support EC2 instance type f1.2xlarge" in out
        # Specifically NOT the Graviton branch, which is the neighbouring exit.
        assert "Graviton" not in out

    def test_unrecognized_base_os_exits_cleanly(self, capsys):
        # Regression test: this used to raise a raw KeyError instead of a
        # clean, user-facing error.
        with pytest.raises(SystemExit):
            aux_data.base_os_instance_check("centos7", "t3.micro", "x86_64", "false")
        assert "is not a recognized base_os" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "base_os",
        [
            "al2023",
            "alinux2",
            "alma9",
            "alma10",
            "rhel9",
            "rhel10",
            "rocky9",
            "rocky10",
            "ubuntu2404",
            "ubuntu2604",
            "opensuse16",
            "windows2019",
            "windows2022",
            "windows2025",
        ],
    )
    def test_every_supported_base_os_has_no_restrictions_on_a_generic_instance(self, base_os):
        # None of the 14 current base_os values have documented instance-type
        # restrictions beyond the generic Windows/Graviton rule, so a boring
        # x86_64 instance type should always pass.
        aux_data.base_os_instance_check(base_os, "m5.large", "x86_64", "false")


class TestEbsEncryptionCheck:
    def test_supported_passes(self, capsys):
        aux_data.ebs_encryption_check("m5.large", "supported", "dev01", "false")
        assert "Enabling: EBS encryption" in capsys.readouterr().out

    def test_unsupported_exits(self):
        with pytest.raises(SystemExit):
            aux_data.ebs_encryption_check("t2.micro", "unsupported", "dev01", "false")


class TestEc2PlacementGroupCheck:
    def test_supported_strategy_passes(self):
        aux_data.ec2_placement_group_check("m5.large", "cluster", ["cluster", "partition", "spread"], "false")

    def test_unsupported_strategy_exits(self):
        # Regression test: this used to only check "does this instance type
        # support placement groups at all," not the specific strategy
        # requested -- t2.micro supports partition/spread but not cluster.
        with pytest.raises(SystemExit):
            aux_data.ec2_placement_group_check("t2.micro", "cluster", ["partition", "spread"], "false")


class TestLogRetentionDaysCheck:
    def test_valid_value_passes(self):
        aux_data.log_retention_days_check(30, "false")

    def test_invalid_value_exits(self):
        # CloudWatch Logs' PutRetentionPolicy only accepts a fixed set of
        # values -- 45 isn't one of them (30 and 60 are, 45 is not).
        with pytest.raises(SystemExit):
            aux_data.log_retention_days_check(45, "false")


class TestModifyIamPolicyDocument:
    def _write_source(self, tmp_path, contents):
        src = tmp_path / "policy.json"
        src.write_text(contents)
        return str(src)

    def test_generic_placeholders_scoped_to_one_instance(self, tmp_path):
        src = self._write_source(
            tmp_path,
            '{"Resource": ["arn:aws:iam::*:policy/<EC2_POLICY>", "arn:aws:iam::*:role/<EC2_ROLE>", "arn:aws:iam::*:instance-profile/<EC2_INSTANCE_PROFILE>"]}',
        )
        stage = str(tmp_path / "stage.json")
        aux_data.modify_iam_policy_document(src, stage, "Ec2InstanceMaker", "12345_us-east-1")
        doc = json.loads(open(stage).read())
        assert doc["Resource"] == [
            "arn:aws:iam::*:policy/Ec2InstanceMaker-policy-12345_us-east-1",
            "arn:aws:iam::*:role/Ec2InstanceMaker-role-12345_us-east-1",
            "arn:aws:iam::*:instance-profile/Ec2InstanceMaker-profile-12345_us-east-1",
        ]

    def test_extended_prefix_placeholder_not_account_wide(self, tmp_path):
        # Regression test for the privilege-escalation fix: ExtendedEc2InstancePolicy.json
        # used to hardcode a literal "*" here (no placeholder at all), granting
        # iam:CreateRole/PassRole/etc. on every IAM entity in the account.
        src = self._write_source(
            tmp_path,
            '{"Resource": ["arn:aws:iam::*:role/<EC2_IAM_PREFIX>-*"]}',
        )
        stage = str(tmp_path / "stage.json")
        aux_data.modify_iam_policy_document(src, stage, "Ec2InstanceMaker", "12345_us-east-1")
        doc = json.loads(open(stage).read())
        assert doc["Resource"] == ["arn:aws:iam::*:role/Ec2InstanceMaker-*"]
        assert "arn:aws:iam::*:role/*" not in json.dumps(doc)

    def test_actual_extended_policy_file_is_scoped(self, tmp_path):
        # End-to-end check against the real shipped policy file, not a
        # synthetic fixture -- catches someone re-introducing a literal "*"
        # directly in templates/ExtendedEc2InstancePolicy.json.
        stage = str(tmp_path / "stage.json")
        aux_data.modify_iam_policy_document("templates/ExtendedEc2InstancePolicy.json", stage, "Ec2InstanceMaker", "12345_us-east-1")
        doc = json.loads(open(stage).read())
        restricted = next(s for s in doc["Statement"] if s["Sid"] == "IAMRestricted")
        for resource_arn in restricted["Resource"]:
            assert resource_arn != "arn:aws:iam::*:role/*"
            assert resource_arn != "arn:aws:iam::*:policy/*"
            assert resource_arn != "arn:aws:iam::*:instance-profile/*"
            assert "Ec2InstanceMaker-" in resource_arn


class TestPFailPVal:
    def test_p_fail_exits(self):
        with pytest.raises(SystemExit):
            aux_data.p_fail("bogus", "instance_type", "missing_element")

    def test_p_val_debug_true_prints(self, capsys):
        aux_data.p_val("base_os", "true")
        assert "base_os successfully validated" in capsys.readouterr().out

    def test_p_val_debug_false_silent(self, capsys):
        aux_data.p_val("base_os", "false")
        assert capsys.readouterr().out == ""


class TestIllegalAzMsg:
    def test_exits(self):
        with pytest.raises(SystemExit):
            aux_data.illegal_az_msg("us-east-99a")
