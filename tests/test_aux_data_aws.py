"""Tests for aux_data.py functions that call AWS, using mocked boto3 clients.

These exist because get_instance_type_info(), get_ami_info(), and
check_custom_ami() previously had zero test coverage of any kind -- if AWS
changed a response field name or filter behavior, or an edge case (e.g. an
unusual SupportedArchitectures value) wasn't handled, nothing would catch
it before a real operator did. Several tests below are direct regression
tests for bugs found during an adversarial review (see CLAUDE-STATE.md):
the missing Owners= pin on Ubuntu AMI lookups, and get_instance_type_info()
misreporting AWS errors other than "invalid instance type."
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import aux_data


def _client_error(code, message="error"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "SomeOperation")


def _instance_type_response(architectures, ebs_optimized="default", ebs_encryption="supported", strategies=None):
    return {
        "InstanceTypes": [
            {
                "ProcessorInfo": {"SupportedArchitectures": architectures},
                "EbsInfo": {"EbsOptimizedSupport": ebs_optimized, "EncryptionSupport": ebs_encryption},
                "PlacementGroupInfo": {"SupportedStrategies": strategies or ["cluster", "partition", "spread"]},
            }
        ]
    }


class TestGetInstanceTypeInfo:
    def test_x86_64_only(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_instance_types.return_value = _instance_type_response(["x86_64"])
            info = aux_data.get_instance_type_info("m5.large", "us-east-1")
        assert info["architecture"] == "x86_64"

    def test_arm64_preferred_when_both_listed(self):
        # Real AWS never actually reports both for one instance type, but the
        # function should still make a deterministic choice if it ever did.
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_instance_types.return_value = _instance_type_response(["x86_64", "arm64"])
            info = aux_data.get_instance_type_info("weird.type", "us-east-1")
        assert info["architecture"] == "arm64"

    def test_i386_only_returns_none(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_instance_types.return_value = _instance_type_response(["i386"])
            info = aux_data.get_instance_type_info("t1.micro", "us-east-1")
        assert info is None

    def test_invalid_instance_type_returns_none(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_instance_types.side_effect = _client_error("InvalidInstanceType")
            info = aux_data.get_instance_type_info("bogus.9xlarge", "us-east-1")
        assert info is None

    def test_throttling_does_not_masquerade_as_invalid_instance_type(self):
        # Regression test: this used to catch *any* ClientError and return
        # None, which make-instance.py then reported as '"m5.large" seems to
        # be missing as a valid instance_type' -- a wrong diagnosis for what
        # is actually an AWS API problem (throttling, bad credentials, etc).
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_instance_types.side_effect = _client_error("RequestLimitExceeded")
            with pytest.raises(SystemExit):
                aux_data.get_instance_type_info("m5.large", "us-east-1")

    def test_unhandled_instance_types_key_returns_none(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_instance_types.return_value = {"InstanceTypes": []}
            info = aux_data.get_instance_type_info("m5.large", "us-east-1")
        assert info is None

    def test_returns_ebs_and_placement_group_fields(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_instance_types.return_value = _instance_type_response(
                ["x86_64"], ebs_optimized="unsupported", ebs_encryption="supported", strategies=["partition", "spread"]
            )
            info = aux_data.get_instance_type_info("t2.micro", "us-east-1")
        assert info["ebs_optimized_support"] == "unsupported"
        assert info["ebs_encryption_support"] == "supported"
        assert info["placement_group_strategies"] == ["partition", "spread"]


def _describe_images_call_kwargs(mock_client):
    return mock_client.return_value.describe_images.call_args.kwargs


class TestGetAmiInfo:
    def _mock_images(self, mock_client, images):
        mock_client.return_value.describe_images.return_value = {"Images": images}

    def test_selects_most_recent_by_creation_date(self):
        with patch("boto3.client") as mock_client:
            self._mock_images(
                mock_client,
                [
                    {"ImageId": "ami-old", "CreationDate": "2024-01-01T00:00:00.000Z"},
                    {"ImageId": "ami-new", "CreationDate": "2026-01-01T00:00:00.000Z"},
                ],
            )
            ami = aux_data.get_ami_info("al2023", "us-east-1", "x86_64")
        assert ami == "ami-new"

    def test_ubuntu_pins_canonical_owner(self):
        # Regression test: ubuntu2404/ubuntu2604 used to have no Owners=
        # filter at all -- describe_images searched every AWS account for a
        # Name match, which could silently resolve to an unrelated AMI from
        # a different publisher.
        with patch("boto3.client") as mock_client:
            self._mock_images(mock_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
            aux_data.get_ami_info("ubuntu2404", "us-east-1", "x86_64")
        assert _describe_images_call_kwargs(mock_client)["Owners"] == ["099720109477"]

    def test_ubuntu2604_pins_canonical_owner(self):
        with patch("boto3.client") as mock_client:
            self._mock_images(mock_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
            aux_data.get_ami_info("ubuntu2604", "us-east-1", "x86_64")
        assert _describe_images_call_kwargs(mock_client)["Owners"] == ["099720109477"]

    @pytest.mark.parametrize(
        "base_os,expected_owner",
        [
            ("alinux2", "137112412989"),
            ("al2023", "137112412989"),
            ("alma9", "764336703387"),
            ("alma10", "764336703387"),
            ("rhel9", "309956199498"),
            ("rhel10", "309956199498"),
            ("rocky9", "679593333241"),
            ("rocky10", "679593333241"),
            ("windows2019", "801119661308"),
            ("windows2022", "801119661308"),
            ("windows2025", "801119661308"),
        ],
    )
    def test_every_non_ubuntu_base_os_pins_an_owner(self, base_os, expected_owner):
        with patch("boto3.client") as mock_client:
            self._mock_images(mock_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
            aux_data.get_ami_info(base_os, "us-east-1", "x86_64")
        assert _describe_images_call_kwargs(mock_client)["Owners"] == [expected_owner]

    def test_architecture_is_threaded_into_filter(self):
        with patch("boto3.client") as mock_client:
            self._mock_images(mock_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
            aux_data.get_ami_info("rhel9", "us-east-1", "arm64")
        filters = _describe_images_call_kwargs(mock_client)["Filters"]
        arch_filter = next(f for f in filters if f["Name"] == "architecture")
        assert arch_filter["Values"] == ["arm64"]


class TestCheckCustomAmi:
    def test_found_returns_ami_id(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_images.return_value = {"Images": [{"ImageId": "ami-mine", "CreationDate": "2026-01-01T00:00:00.000Z"}]}
            result = aux_data.check_custom_ami("ami-mine", "123456789012", "us-east-1", "x86_64")
        assert result == "ami-mine"

    def test_not_found_returns_false_string(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_images.return_value = {"Images": []}
            result = aux_data.check_custom_ami("ami-doesnotexist", "123456789012", "us-east-1", "x86_64")
        assert result == "false"

    def test_scoped_to_callers_own_account(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.describe_images.return_value = {"Images": []}
            aux_data.check_custom_ami("ami-x", "123456789012", "us-east-1", "x86_64")
        assert _describe_images_call_kwargs(mock_client)["Owners"] == ["123456789012"]


class TestCtrlCAbortIamNaming:
    """Regression tests: ctrlC_Abort() used to compute a completely
    different, wrong IAM naming scheme internally
    ("ec2-instance-role-<serial>") instead of using the real creation-time
    names, so a CTRL-C during the abort window could never successfully
    clean up (or accidentally target) the actual IAM entities -- it always
    hit NoSuchEntity on a name that was never real, silently leaving the
    genuine role/policy/profile orphaned while printing messages that
    looked like success.
    """

    def _call(self, tmp_path, mock_iam, **overrides):
        serial_file = tmp_path / "serial"
        vars_file = tmp_path / "vars.yml"
        serial_file.write_text("x")
        vars_file.write_text("x")
        # ctrlC_Abort() also tries to delete the EC2 keypair's .pem file;
        # the mocked ec2client.describe_key_pairs() implicitly "succeeds"
        # (a MagicMock doesn't raise), so give it a real file to remove.
        (tmp_path / "99999999999999_us-east-1_us-east-1.pem").write_text("x")
        kwargs = {
            "sleep_time": 0,
            "line_length": 40,
            "vars_file_path": str(vars_file),
            "instance_data_dir": str(tmp_path) + "/",
            "instance_serial_number_file": str(serial_file),
            "instance_serial_number": "99999999999999_us-east-1",
            "region": "us-east-1",
            "security_group_name": "fake-sg",
            "vpc_security_group_ids": "sg-fakefakefake",
            "iam_instance_role": "Ec2InstanceMaker-role-99999999999999_us-east-1",
            "iam_instance_policy": "Ec2InstanceMaker-policy-99999999999999_us-east-1",
            "iam_instance_profile": "Ec2InstanceMaker-profile-99999999999999_us-east-1",
            "preserve_iam_role": "false",
        }
        kwargs.update(overrides)
        with patch("time.sleep", side_effect=KeyboardInterrupt):
            with patch("boto3.client", side_effect=lambda service, **kw: mock_iam if service == "iam" else MagicMock()):
                with pytest.raises(SystemExit):
                    aux_data.ctrlC_Abort(**kwargs)

    def test_preserve_true_does_not_attempt_deletion(self, tmp_path):
        mock_iam = MagicMock()
        self._call(
            tmp_path,
            mock_iam,
            iam_instance_role="my-preexisting-role",
            iam_instance_policy="UNDEFINED",
            iam_instance_profile="my-preexisting-role_instance_profile",
            preserve_iam_role="true",
        )
        mock_iam.delete_role.assert_not_called()
        mock_iam.delete_instance_profile.assert_not_called()

    def test_preserve_false_deletes_the_real_creation_time_names(self, tmp_path):
        mock_iam = MagicMock()
        self._call(tmp_path, mock_iam, preserve_iam_role="false")
        mock_iam.delete_role.assert_called_with(RoleName="Ec2InstanceMaker-role-99999999999999_us-east-1")
        mock_iam.delete_instance_profile.assert_called_with(InstanceProfileName="Ec2InstanceMaker-profile-99999999999999_us-east-1")
