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
        ec2_client = MagicMock()
        ec2_client.describe_instance_types.return_value = _instance_type_response(["x86_64"])
        info = aux_data.get_instance_type_info(ec2_client, "m5.large")
        assert info["architecture"] == "x86_64"

    def test_arm64_preferred_when_both_listed(self):
        # Real AWS never actually reports both for one instance type, but the
        # function should still make a deterministic choice if it ever did.
        ec2_client = MagicMock()
        ec2_client.describe_instance_types.return_value = _instance_type_response(["x86_64", "arm64"])
        info = aux_data.get_instance_type_info(ec2_client, "weird.type")
        assert info["architecture"] == "arm64"

    def test_i386_only_returns_none(self):
        ec2_client = MagicMock()
        ec2_client.describe_instance_types.return_value = _instance_type_response(["i386"])
        info = aux_data.get_instance_type_info(ec2_client, "t1.micro")
        assert info is None

    def test_invalid_instance_type_returns_none(self):
        ec2_client = MagicMock()
        ec2_client.describe_instance_types.side_effect = _client_error("InvalidInstanceType")
        info = aux_data.get_instance_type_info(ec2_client, "bogus.9xlarge")
        assert info is None

    def test_throttling_does_not_masquerade_as_invalid_instance_type(self):
        # Regression test: this used to catch *any* ClientError and return
        # None, which make_instance.py then reported as '"m5.large" seems to
        # be missing as a valid instance_type' -- a wrong diagnosis for what
        # is actually an AWS API problem (throttling, bad credentials, etc).
        ec2_client = MagicMock()
        ec2_client.describe_instance_types.side_effect = _client_error("RequestLimitExceeded")
        with pytest.raises(SystemExit):
            aux_data.get_instance_type_info(ec2_client, "m5.large")

    def test_unhandled_instance_types_key_returns_none(self):
        ec2_client = MagicMock()
        ec2_client.describe_instance_types.return_value = {"InstanceTypes": []}
        info = aux_data.get_instance_type_info(ec2_client, "m5.large")
        assert info is None

    def test_returns_ebs_and_placement_group_fields(self):
        ec2_client = MagicMock()
        ec2_client.describe_instance_types.return_value = _instance_type_response(["x86_64"], ebs_optimized="unsupported", ebs_encryption="supported", strategies=["partition", "spread"])
        info = aux_data.get_instance_type_info(ec2_client, "t2.micro")
        assert info["ebs_optimized_support"] == "unsupported"
        assert info["ebs_encryption_support"] == "supported"
        assert info["placement_group_strategies"] == ["partition", "spread"]


def _describe_images_call_kwargs(ec2_client):
    return ec2_client.describe_images.call_args.kwargs


class TestGetAmiInfo:
    def _mock_images(self, ec2_client, images):
        ec2_client.describe_images.return_value = {"Images": images}

    def test_selects_most_recent_by_creation_date(self):
        ec2_client = MagicMock()
        self._mock_images(
            ec2_client,
            [
                {"ImageId": "ami-old", "CreationDate": "2024-01-01T00:00:00.000Z"},
                {"ImageId": "ami-new", "CreationDate": "2026-01-01T00:00:00.000Z"},
            ],
        )
        ami = aux_data.get_ami_info(ec2_client, "al2023", "x86_64")
        assert ami == "ami-new"

    def test_ubuntu_pins_canonical_owner(self):
        # Regression test: ubuntu2404/ubuntu2604 used to have no Owners=
        # filter at all -- describe_images searched every AWS account for a
        # Name match, which could silently resolve to an unrelated AMI from
        # a different publisher.
        ec2_client = MagicMock()
        self._mock_images(ec2_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
        aux_data.get_ami_info(ec2_client, "ubuntu2404", "x86_64")
        assert _describe_images_call_kwargs(ec2_client)["Owners"] == ["099720109477"]

    def test_ubuntu2604_pins_canonical_owner(self):
        ec2_client = MagicMock()
        self._mock_images(ec2_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
        aux_data.get_ami_info(ec2_client, "ubuntu2604", "x86_64")
        assert _describe_images_call_kwargs(ec2_client)["Owners"] == ["099720109477"]

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
            ("opensuse16", "679593333241"),
            ("debian12", "136693071363"),
            ("debian13", "136693071363"),
            ("windows2019", "801119661308"),
            ("windows2022", "801119661308"),
            ("windows2025", "801119661308"),
        ],
    )
    def test_every_non_ubuntu_base_os_pins_an_owner(self, base_os, expected_owner):
        ec2_client = MagicMock()
        self._mock_images(ec2_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
        aux_data.get_ami_info(ec2_client, base_os, "x86_64")
        assert _describe_images_call_kwargs(ec2_client)["Owners"] == [expected_owner]

    def test_architecture_is_threaded_into_filter(self):
        ec2_client = MagicMock()
        self._mock_images(ec2_client, [{"ImageId": "ami-x", "CreationDate": "2026-01-01T00:00:00.000Z"}])
        aux_data.get_ami_info(ec2_client, "rhel9", "arm64")
        filters = _describe_images_call_kwargs(ec2_client)["Filters"]
        arch_filter = next(f for f in filters if f["Name"] == "architecture")
        assert arch_filter["Values"] == ["arm64"]

    def test_aws_api_error_quits_instead_of_propagating_raw(self, capsys):
        # Regression test: get_ami_info() used to have no error handling at
        # all -- a real AWS API problem (throttling, AccessDenied) propagated
        # as a raw, unhandled ClientError/traceback instead of the clean
        # operator-facing message get_instance_type_info() already gives for
        # the same class of failure.
        ec2_client = MagicMock()
        ec2_client.describe_images.side_effect = _client_error("RequestLimitExceeded")
        with pytest.raises(SystemExit):
            aux_data.get_ami_info(ec2_client, "al2023", "x86_64")
        # get_ami_info() has three exits (unknown base_os, API error, no
        # matching AMI). This must be the API-error one, and it must carry
        # the underlying AWS code rather than swallowing it.
        out = capsys.readouterr().out
        assert "AWS API error" in out
        assert "RequestLimitExceeded" in out


class TestAddInboundSecurityGroupRule:
    def test_calls_authorize_ingress_with_the_right_arguments(self):
        sec_grp = MagicMock()
        aux_data.add_inbound_security_group_rule(sec_grp, "tcp", "10.0.0.0/16", 22, 22)
        sec_grp.authorize_ingress.assert_called_once_with(IpProtocol="tcp", CidrIp="10.0.0.0/16", FromPort=22, ToPort=22)


class TestCheckCustomAmi:
    def test_found_returns_ami_id(self):
        ec2_client = MagicMock()
        ec2_client.describe_images.return_value = {"Images": [{"ImageId": "ami-mine", "CreationDate": "2026-01-01T00:00:00.000Z"}]}
        result = aux_data.check_custom_ami(ec2_client, "ami-mine", "123456789012", "x86_64")
        assert result == "ami-mine"

    def test_not_found_returns_false_string(self):
        ec2_client = MagicMock()
        ec2_client.describe_images.return_value = {"Images": []}
        result = aux_data.check_custom_ami(ec2_client, "ami-doesnotexist", "123456789012", "x86_64")
        assert result == "false"

    def test_scoped_to_callers_own_account(self):
        ec2_client = MagicMock()
        ec2_client.describe_images.return_value = {"Images": []}
        aux_data.check_custom_ami(ec2_client, "ami-x", "123456789012", "x86_64")
        assert _describe_images_call_kwargs(ec2_client)["Owners"] == ["123456789012"]

    def test_aws_api_error_quits_instead_of_propagating_raw(self):
        ec2_client = MagicMock()
        ec2_client.describe_images.side_effect = _client_error("AccessDeniedException")
        with pytest.raises(SystemExit):
            aux_data.check_custom_ami(ec2_client, "ami-x", "123456789012", "x86_64")


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

    def _call(self, tmp_path, mock_iam, mock_ec2=None, **overrides):
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
            "ec2client": mock_ec2 if mock_ec2 is not None else MagicMock(),
            "iam": mock_iam,
            "security_group_name": "fake-sg",
            "vpc_security_group_ids": "sg-fakefakefake",
            "iam_instance_role": "Ec2InstanceMaker-role-99999999999999_us-east-1",
            "iam_instance_policy": "Ec2InstanceMaker-policy-99999999999999_us-east-1",
            "iam_instance_profile": "Ec2InstanceMaker-profile-99999999999999_us-east-1",
            "preserve_iam_role": "false",
            "ec2_keypair": "99999999999999_us-east-1_us-east-1",
            "sns_client": MagicMock(),
            "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:fake-topic",
            "logs_client": MagicMock(),
            "cloudwatch_log_group": "/ec2instancemaker/fake",
            "enable_cloudwatch_logs": "true",
            "preserve_cloudwatch_logs": "false",
            "preserve_security_group": "false",
            "instance_name": "fake01",
        }
        kwargs.update(overrides)
        with patch("time.sleep", side_effect=KeyboardInterrupt):
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

    def test_uses_the_real_creation_time_ec2_keypair_not_a_recomputed_default(self, tmp_path):
        # ctrlC_Abort() used to recompute ec2_keypair internally as
        # "<instance_serial_number>_<region>", ignoring a custom
        # --ec2_keypair the operator actually built with -- meaning CTRL-C
        # cleanup would look for (and fail to find/delete) a keypair name
        # that was never real, silently leaving the genuine one orphaned.
        (tmp_path / "my-custom-keypair.pem").write_text("x")
        mock_ec2 = MagicMock()
        mock_iam = MagicMock()
        self._call(
            tmp_path,
            mock_iam,
            mock_ec2=mock_ec2,
            iam_instance_role="my-preexisting-role",
            iam_instance_policy="UNDEFINED",
            iam_instance_profile="my-preexisting-role_instance_profile",
            preserve_iam_role="true",
            ec2_keypair="my-custom-keypair",
        )
        mock_ec2.delete_key_pair.assert_called_with(KeyName="my-custom-keypair")


class TestCtrlCAbortCleanupReporting:
    """ctrlC_Abort() caught ClientError and printed something only when the
    error code was the "already gone" one. Any other code (AccessDenied,
    DeleteConflict, Throttling, DependencyViolation) fell off the end of the
    handler with no message and no re-raise -- so the abort printed
    "Aborting..." and exited 1 while the security group, keypair, role,
    policy and instance profile were all still there, and the operator was
    told cleanup had happened.

    It also never deleted the SNS topic or the CloudWatch Logs group, both
    of which are created in the phase immediately before the abort window.
    """

    def _call(self, tmp_path, **overrides):
        (tmp_path / "kp.pem").write_text("x")
        kwargs = {
            "sleep_time": 0,
            "line_length": 40,
            "vars_file_path": str(tmp_path / "vars.yml"),
            "instance_data_dir": str(tmp_path) + "/",
            "instance_serial_number_file": str(tmp_path / "serial"),
            "ec2client": MagicMock(),
            "iam": MagicMock(),
            "security_group_name": "fake-sg",
            "vpc_security_group_ids": "sg-fakefakefake",
            "iam_instance_role": "role",
            "iam_instance_policy": "policy",
            "iam_instance_profile": "profile",
            "preserve_iam_role": "true",
            "ec2_keypair": "kp",
            "sns_client": MagicMock(),
            "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:fake",
            "logs_client": MagicMock(),
            "cloudwatch_log_group": "/ec2instancemaker/fake",
            "enable_cloudwatch_logs": "true",
            "preserve_cloudwatch_logs": "false",
            "preserve_security_group": "false",
            "instance_name": "fake01",
        }
        kwargs.update(overrides)
        with patch("time.sleep", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit):
                aux_data.ctrlC_Abort(**kwargs)
        return kwargs

    @staticmethod
    def _client_error(code):
        return ClientError({"Error": {"Code": code, "Message": code}}, "Delete")

    def test_sns_topic_is_deleted(self, tmp_path):
        kwargs = self._call(tmp_path)
        kwargs["sns_client"].delete_topic.assert_called_once_with(TopicArn="arn:aws:sns:us-east-1:123456789012:fake")

    def test_cloudwatch_log_group_is_deleted(self, tmp_path):
        kwargs = self._call(tmp_path)
        kwargs["logs_client"].delete_log_group.assert_called_once_with(logGroupName="/ec2instancemaker/fake")

    def test_cloudwatch_log_group_is_kept_when_preserved(self, tmp_path):
        kwargs = self._call(tmp_path, preserve_cloudwatch_logs="true")
        kwargs["logs_client"].delete_log_group.assert_not_called()

    def test_preexisting_security_group_is_not_deleted(self, tmp_path):
        kwargs = self._call(tmp_path, preserve_security_group="true")
        kwargs["ec2client"].delete_security_group.assert_not_called()

    def test_unexpected_error_is_reported_not_swallowed(self, tmp_path, capsys):
        ec2client = MagicMock()
        ec2client.delete_security_group.side_effect = self._client_error("DependencyViolation")
        self._call(tmp_path, ec2client=ec2client)
        out = capsys.readouterr().out
        assert "FAILED" in out
        assert "DependencyViolation" in out
        assert "could NOT be deleted" in out
        # The operator is told how to finish the job.
        assert "kill-instance.fake01.sh" in out

    def test_already_gone_is_not_reported_as_a_failure(self, tmp_path, capsys):
        ec2client = MagicMock()
        ec2client.delete_security_group.side_effect = self._client_error("InvalidGroup.NotFound")
        self._call(tmp_path, ec2client=ec2client)
        out = capsys.readouterr().out
        assert "could NOT be deleted" not in out
        assert "Nothing to delete" in out

    def test_one_failure_does_not_stop_later_cleanup_steps(self, tmp_path):
        # The whole point of counting failures instead of aborting.
        ec2client = MagicMock()
        ec2client.delete_security_group.side_effect = self._client_error("DependencyViolation")
        kwargs = self._call(tmp_path, ec2client=ec2client)
        ec2client.delete_key_pair.assert_called_once()
        kwargs["sns_client"].delete_topic.assert_called_once()
        kwargs["logs_client"].delete_log_group.assert_called_once()
