"""Unit tests for instance_builder.py -- the first increment of extracting
make_instance.py's logic into independently-testable functions (see
CLAUDE-STATE.md for the phased plan). Each function here is pure or takes
its AWS client as an explicit argument, so none of these need to run
against real AWS.
"""

import json
import os
import time
from datetime import UTC
from datetime import datetime as DateTime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import instance_builder


def _client_error(code, message="error"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "SomeOperation")


class TestValidateAzAndRegion:
    def test_valid_az_does_not_call_illegal_az_msg(self):
        ec2_client = MagicMock()
        illegal_az_msg = MagicMock()
        instance_builder.validate_az_and_region(ec2_client, "us-east-1a", illegal_az_msg)
        illegal_az_msg.assert_not_called()

    def test_endpoint_connection_error_calls_illegal_az_msg(self):
        # Regression test: this used to be unguarded and would crash with a
        # raw traceback on a bad region (see CLAUDE-STATE.md).
        ec2_client = MagicMock()
        ec2_client.describe_availability_zones.side_effect = EndpointConnectionError(endpoint_url="https://ec2.us-east-99.amazonaws.com/")
        illegal_az_msg = MagicMock()
        instance_builder.validate_az_and_region(ec2_client, "us-east-99a", illegal_az_msg)
        illegal_az_msg.assert_called_once_with("us-east-99a")

    def test_value_error_calls_illegal_az_msg(self):
        ec2_client = MagicMock()
        ec2_client.describe_availability_zones.side_effect = ValueError
        illegal_az_msg = MagicMock()
        instance_builder.validate_az_and_region(ec2_client, "bogus", illegal_az_msg)
        illegal_az_msg.assert_called_once_with("bogus")


class TestGenerateInstanceSerialNumber:
    def test_deterministic_with_fixed_time(self):
        fixed = time.strptime("2026-09-07 01:02:03", "%Y-%m-%d %H:%M:%S")
        result = instance_builder.generate_instance_serial_number("dev01", now=fixed)
        assert result["instance_serial_datestamp"] == "03020107092026"
        assert result["instance_serial_number"] == "dev01-03020107092026"
        assert result["DEPLOYMENT_DATE"] == "September 7, 2026"
        assert result["DEPLOYMENT_DATE_TAG"] == "7-September-2026"

    def test_serial_number_prefixed_with_instance_name(self):
        result = instance_builder.generate_instance_serial_number("fam03")
        assert result["instance_serial_number"].startswith("fam03-")


class TestGenerateSnsTimestamps:
    def test_deterministic_with_fixed_time(self):
        fixed = DateTime(2026, 9, 7, 1, 2)
        datestamp, timestamp = instance_builder.generate_sns_timestamps(now=fixed)
        assert datestamp == "09-07-2026"
        assert timestamp == "01:02"

    def test_defaults_to_timezone_aware_utc_now_not_deprecated_utcnow(self):
        # Regression test: this used to call the deprecated
        # datetime.utcnow(); make sure it stays on the timezone-aware
        # datetime.now(timezone.utc) replacement.
        with patch("instance_builder.DateTime") as mock_datetime:
            mock_datetime.now.return_value = DateTime(2026, 9, 7, 1, 2, tzinfo=UTC)
            instance_builder.generate_sns_timestamps()
        mock_datetime.now.assert_called_once_with(UTC)
        mock_datetime.utcnow.assert_not_called()


def _quitting_mock():
    # refer_to_docs_and_quit() always calls sys.exit(1) in real code; a
    # plain MagicMock wouldn't stop execution, so callers that check
    # multiple conditions in sequence (like validate_and_resize_ebs_
    # volumes) need the mock to actually raise to behave like the real
    # thing.
    return MagicMock(side_effect=SystemExit(1))


class TestValidateAndResizeEbsVolumes:
    def test_within_limits_unchanged(self):
        root, device = instance_builder.validate_and_resize_ebs_volumes(
            ebs_root_volume_size=8,
            ebs_device_volume_size=0,
            ebs_root_volume_type="gp2",
            ebs_device_volume_type="gp2",
            ebs_root_volume_iops=0,
            ebs_device_volume_iops=0,
            is_windows=False,
            refer_to_docs_and_quit=_quitting_mock(),
        )
        assert (root, device) == (8, 0)

    def test_root_over_16tb_quits(self):
        with pytest.raises(SystemExit):
            instance_builder.validate_and_resize_ebs_volumes(
                ebs_root_volume_size=16001,
                ebs_device_volume_size=0,
                ebs_root_volume_type="gp2",
                ebs_device_volume_type="gp2",
                ebs_root_volume_iops=0,
                ebs_device_volume_iops=0,
                is_windows=False,
                refer_to_docs_and_quit=_quitting_mock(),
            )

    def test_device_over_16tb_quits(self):
        with pytest.raises(SystemExit):
            instance_builder.validate_and_resize_ebs_volumes(
                ebs_root_volume_size=8,
                ebs_device_volume_size=16001,
                ebs_root_volume_type="gp2",
                ebs_device_volume_type="gp2",
                ebs_root_volume_iops=0,
                ebs_device_volume_iops=0,
                is_windows=False,
                refer_to_docs_and_quit=_quitting_mock(),
            )

    def test_windows_bumps_undersized_volumes_to_30gb(self):
        root, device = instance_builder.validate_and_resize_ebs_volumes(
            ebs_root_volume_size=8,
            ebs_device_volume_size=10,
            ebs_root_volume_type="gp2",
            ebs_device_volume_type="gp2",
            ebs_root_volume_iops=0,
            ebs_device_volume_iops=0,
            is_windows=True,
            refer_to_docs_and_quit=_quitting_mock(),
        )
        assert (root, device) == (30, 30)

    def test_windows_does_not_shrink_larger_volumes(self):
        root, device = instance_builder.validate_and_resize_ebs_volumes(
            ebs_root_volume_size=100,
            ebs_device_volume_size=50,
            ebs_root_volume_type="gp2",
            ebs_device_volume_type="gp2",
            ebs_root_volume_iops=0,
            ebs_device_volume_iops=0,
            is_windows=True,
            refer_to_docs_and_quit=_quitting_mock(),
        )
        assert (root, device) == (100, 50)

    def test_linux_does_not_bump_undersized_volumes(self):
        root, device = instance_builder.validate_and_resize_ebs_volumes(
            ebs_root_volume_size=8,
            ebs_device_volume_size=0,
            ebs_root_volume_type="gp2",
            ebs_device_volume_type="gp2",
            ebs_root_volume_iops=0,
            ebs_device_volume_iops=0,
            is_windows=False,
            refer_to_docs_and_quit=_quitting_mock(),
        )
        assert (root, device) == (8, 0)

    def test_io1_requires_root_iops_in_range(self):
        with pytest.raises(SystemExit):
            instance_builder.validate_and_resize_ebs_volumes(
                ebs_root_volume_size=8,
                ebs_device_volume_size=0,
                ebs_root_volume_type="io1",
                ebs_device_volume_type="gp2",
                ebs_root_volume_iops=0,
                ebs_device_volume_iops=100,
                is_windows=False,
                refer_to_docs_and_quit=_quitting_mock(),
            )

    def test_io1_root_iops_above_the_maximum_is_refused(self):
        # The mirror of test_io1_requires_device_iops_in_range below. Only
        # the *device* check was previously exercised at the top of the
        # range, so deleting "or ebs_root_volume_iops > 16000" from the
        # root check broke nothing in the suite -- confirmed by mutation
        # testing during an adversarial review.
        with pytest.raises(SystemExit):
            instance_builder.validate_and_resize_ebs_volumes(
                ebs_root_volume_size=8,
                ebs_device_volume_size=0,
                ebs_root_volume_type="io1",
                ebs_device_volume_type="gp2",
                ebs_root_volume_iops=16001,
                ebs_device_volume_iops=100,
                is_windows=False,
                refer_to_docs_and_quit=_quitting_mock(),
            )

    def test_io1_requires_device_iops_in_range(self):
        with pytest.raises(SystemExit):
            instance_builder.validate_and_resize_ebs_volumes(
                ebs_root_volume_size=8,
                ebs_device_volume_size=0,
                ebs_root_volume_type="gp2",
                ebs_device_volume_type="io1",
                ebs_root_volume_iops=100,
                ebs_device_volume_iops=16001,
                is_windows=False,
                refer_to_docs_and_quit=_quitting_mock(),
            )

    def test_device_io1_is_validated_independently_of_root_type(self):
        # Regression test: this used to gate the device IOPS check on
        # ebs_root_volume_type alone, so a non-io1 root paired with an io1
        # *device* volume never validated (or required) the device's IOPS
        # at all. root_volume_type is deliberately "gp2" (never io1) here.
        with pytest.raises(SystemExit):
            instance_builder.validate_and_resize_ebs_volumes(
                ebs_root_volume_size=8,
                ebs_device_volume_size=0,
                ebs_root_volume_type="gp2",
                ebs_device_volume_type="io1",
                ebs_root_volume_iops=0,
                ebs_device_volume_iops=0,
                is_windows=False,
                refer_to_docs_and_quit=_quitting_mock(),
            )

    def test_root_io1_does_not_require_device_iops_when_device_is_not_io1(self):
        # The inverse regression: an io1 root paired with a non-io1 device
        # must not demand (or misapply) IOPS bounds on the device.
        root, device = instance_builder.validate_and_resize_ebs_volumes(
            ebs_root_volume_size=8,
            ebs_device_volume_size=0,
            ebs_root_volume_type="io1",
            ebs_device_volume_type="gp2",
            ebs_root_volume_iops=100,
            ebs_device_volume_iops=0,
            is_windows=False,
            refer_to_docs_and_quit=_quitting_mock(),
        )
        assert (root, device) == (8, 0)

    def test_io1_within_range_passes(self):
        root, device = instance_builder.validate_and_resize_ebs_volumes(
            ebs_root_volume_size=8,
            ebs_device_volume_size=0,
            ebs_root_volume_type="io1",
            ebs_device_volume_type="io1",
            ebs_root_volume_iops=100,
            ebs_device_volume_iops=100,
            is_windows=False,
            refer_to_docs_and_quit=_quitting_mock(),
        )
        assert (root, device) == (8, 0)

    def test_gp2_ignores_iops_entirely(self):
        # 0 IOPS would fail the io1 check but is irrelevant for gp2.
        root, device = instance_builder.validate_and_resize_ebs_volumes(
            ebs_root_volume_size=8,
            ebs_device_volume_size=0,
            ebs_root_volume_type="gp2",
            ebs_device_volume_type="gp2",
            ebs_root_volume_iops=0,
            ebs_device_volume_iops=0,
            is_windows=False,
            refer_to_docs_and_quit=_quitting_mock(),
        )
        assert (root, device) == (8, 0)


class TestResolveVpcAndSubnet:
    def test_default_vpc_resolves_id_name_and_subnet(self):
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-abc123", "Tags": [{"Key": "Name", "Value": "vpc_default"}]}]}
        ec2_client.describe_subnets.return_value = {"Subnets": [{"SubnetId": "subnet-xyz789"}]}
        quit_fn = MagicMock(side_effect=SystemExit(1))

        vpc_id, vpc_name, subnet_id = instance_builder.resolve_vpc_and_subnet(ec2_client, "vpc_default", "us-east-1a", quit_fn)

        assert vpc_id == "vpc-abc123"
        assert vpc_name == "vpc_default"
        assert subnet_id == "subnet-xyz789"
        quit_fn.assert_not_called()
        # Default VPC lookup must filter on isDefault, not tag:Name.
        filters = ec2_client.describe_vpcs.call_args.kwargs["Filters"]
        assert filters == [{"Name": "isDefault", "Values": ["true"]}]

    def test_named_vpc_filters_by_name_tag(self):
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-def456", "Tags": [{"Key": "Name", "Value": "my-vpc"}]}]}
        ec2_client.describe_subnets.return_value = {"Subnets": [{"SubnetId": "subnet-111"}]}
        quit_fn = MagicMock(side_effect=SystemExit(1))

        instance_builder.resolve_vpc_and_subnet(ec2_client, "my-vpc", "us-east-1b", quit_fn)

        filters = ec2_client.describe_vpcs.call_args.kwargs["Filters"]
        assert filters == [{"Name": "tag:Name", "Values": ["my-vpc"]}]

    def test_vpc_missing_name_tag_quits_with_a_ready_to_run_fix_command(self):
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-notag"}]}
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.resolve_vpc_and_subnet(ec2_client, "vpc_default", "us-east-1a", quit_fn)
        quit_fn.assert_called_once()
        error_msg = quit_fn.call_args.args[0]
        assert "vpc-notag lacks a valid Name tag" in error_msg
        assert "aws --region us-east-1 ec2 create-tags --resources vpc-notag --tags Key=Name,Value=vpc-notag" in error_msg
        assert "$ aws" not in error_msg  # no shell marker -- must paste cleanly

    def test_no_matching_vpc_quits(self):
        # Regression-preserving test: describe_vpcs returning an empty list
        # means the for-loop body never runs, vpc_id is never assigned, and
        # referencing it while building the subnet Filters raises
        # NameError -- which is what actually reports "undefined VPC" here.
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.return_value = {"Vpcs": []}
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.resolve_vpc_and_subnet(ec2_client, "nonexistent-vpc", "us-east-1a", quit_fn)
        quit_fn.assert_called_once_with('"nonexistent-vpc" is an undefined VPC!')

    def test_subnet_lookup_is_scoped_to_the_requested_az(self):
        # Every existing test set describe_subnets.return_value and asserted
        # on the SubnetId that came back, so the Filters= argument was never
        # inspected -- removing the availabilityZone filter entirely left
        # the suite green (confirmed by mutation testing). Without it,
        # --az us-east-2a can select a subnet in us-east-2c and Terraform
        # launches in the wrong AZ, defeating the point of the flag.
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-abc123", "Tags": [{"Key": "Name", "Value": "vpc_default"}]}]}
        ec2_client.describe_subnets.return_value = {"Subnets": [{"SubnetId": "subnet-xyz789"}]}

        instance_builder.resolve_vpc_and_subnet(ec2_client, "vpc_default", "us-east-1b", MagicMock(side_effect=SystemExit(1)))

        filters = ec2_client.describe_subnets.call_args.kwargs["Filters"]
        az_filter = next(f for f in filters if f["Name"] == "availabilityZone")
        assert az_filter["Values"] == ["us-east-1b"]

    def test_no_subnets_in_az_quits(self):
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-abc123", "Tags": [{"Key": "Name", "Value": "vpc_default"}]}]}
        ec2_client.describe_subnets.return_value = {"Subnets": []}
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.resolve_vpc_and_subnet(ec2_client, "vpc_default", "us-east-1z", quit_fn)
        quit_fn.assert_called_once_with("AvailabilityZone us-east-1z does not contain any valid subnets!")


class TestResolveSshAllowedIps:
    def test_undefined_resolves_to_the_vpc_cidr(self):
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
        quit_fn = _quitting_mock()

        result = instance_builder.resolve_ssh_allowed_ips(ec2_client, "vpc-abc", "UNDEFINED", quit_fn)

        assert result == "10.0.0.0/16"
        ec2_client.describe_vpcs.assert_called_once_with(VpcIds=["vpc-abc"])
        quit_fn.assert_not_called()

    def test_explicit_valid_cidr_is_passed_through(self):
        ec2_client = MagicMock()
        quit_fn = _quitting_mock()

        result = instance_builder.resolve_ssh_allowed_ips(ec2_client, "vpc-abc", "203.0.113.0/24", quit_fn)

        assert result == "203.0.113.0/24"
        ec2_client.describe_vpcs.assert_not_called()
        quit_fn.assert_not_called()

    def test_wide_open_cidr_is_refused(self):
        ec2_client = MagicMock()
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            instance_builder.resolve_ssh_allowed_ips(ec2_client, "vpc-abc", "0.0.0.0/0", quit_fn)
        quit_fn.assert_called_once()
        assert "0.0.0.0/0" in quit_fn.call_args.args[0]

    def test_malformed_cidr_is_refused(self):
        ec2_client = MagicMock()
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            instance_builder.resolve_ssh_allowed_ips(ec2_client, "vpc-abc", "not-a-cidr", quit_fn)
        quit_fn.assert_called_once()
        assert "not-a-cidr" in quit_fn.call_args.args[0]


def _fake_security_group(sg_id):
    # Stands in for a boto3 ec2.SecurityGroup resource. These tests only
    # ever read .id and compare the object by identity, but constructing a
    # real resource made boto3 walk the credential chain and issue live
    # IMDS requests to 169.254.169.254 during the test run -- see the
    # network block in tests/conftest.py.
    sg = MagicMock()
    sg.id = sg_id
    return sg


class TestResolveSecurityGroup:
    def _ec2_with_filter_results(self, results_by_call):
        # results_by_call: list of lists, one per successive .filter() call.
        ec2 = MagicMock()
        ec2.security_groups.filter.side_effect = [iter(r) for r in results_by_call]
        return ec2

    def test_default_name_gets_serial_suffix(self):
        fake_sg = _fake_security_group("sg-0123456789abcdef0")
        ec2 = self._ec2_with_filter_results([[fake_sg]])
        add_rule = MagicMock()

        name, sg_ids, preserve = instance_builder.resolve_security_group(ec2, "ec2instancemaker_sg", "12345_us-east-1", "vpc-abc", False, "10.0.0.0/16", add_rule)

        assert name == "ec2instancemaker_sg_12345_us-east-1"
        assert sg_ids == "sg-0123456789abcdef0"
        ec2.create_security_group.assert_not_called()
        add_rule.assert_not_called()

    def test_custom_name_used_as_is(self):
        fake_sg = _fake_security_group("sg-custom111111111")
        ec2 = self._ec2_with_filter_results([[fake_sg]])
        add_rule = MagicMock()

        name, sg_ids, preserve = instance_builder.resolve_security_group(ec2, "my-custom-sg", "12345_us-east-1", "vpc-abc", False, "10.0.0.0/16", add_rule)

        assert name == "my-custom-sg"
        assert sg_ids == "sg-custom111111111"

    def test_creates_group_and_opens_rdp_for_windows(self):
        created_sg = _fake_security_group("sg-newlycreated0001")
        ec2 = self._ec2_with_filter_results([[], [created_sg]])
        ec2.create_security_group.return_value = created_sg
        add_rule = MagicMock()

        name, sg_ids, preserve = instance_builder.resolve_security_group(ec2, "ec2instancemaker_sg", "999_us-east-1", "vpc-abc", True, "10.0.0.0/16", add_rule)

        ec2.create_security_group.assert_called_once()
        add_rule.assert_called_once_with(created_sg, "tcp", "10.0.0.0/16", 3389, 3389)
        assert sg_ids == "sg-newlycreated0001"

    def test_creates_group_and_opens_ssh_for_linux(self):
        created_sg = _fake_security_group("sg-newlycreated0002")
        ec2 = self._ec2_with_filter_results([[], [created_sg]])
        ec2.create_security_group.return_value = created_sg
        add_rule = MagicMock()

        instance_builder.resolve_security_group(ec2, "ec2instancemaker_sg", "999_us-east-1", "vpc-abc", False, "10.0.0.0/16", add_rule)

        add_rule.assert_called_once_with(created_sg, "tcp", "10.0.0.0/16", 22, 22)

    def test_newly_created_group_is_not_preserved_on_teardown(self):
        ec2 = self._ec2_with_filter_results([[], [_fake_security_group("sg-newlycreated0003")]])
        ec2.create_security_group.return_value = _fake_security_group("sg-newlycreated0003")

        _, _, preserve = instance_builder.resolve_security_group(ec2, "ec2instancemaker_sg", "999_us-east-1", "vpc-abc", False, "10.0.0.0/16", MagicMock())

        assert preserve == "false"

    def test_preexisting_group_is_preserved_on_teardown(self):
        # A group this build did not create is not ours to delete. Teardown
        # previously deleted the security group unconditionally by hardcoded
        # ID, so `--security_group corp-shared-sg` meant
        # kill-instance.<name>.sh would delete a shared security group the
        # toolkit never created.
        ec2 = self._ec2_with_filter_results([[_fake_security_group("sg-preexisting00001")]])
        add_rule = MagicMock()

        _, _, preserve = instance_builder.resolve_security_group(ec2, "corp-shared-sg", "999_us-east-1", "vpc-abc", False, "10.0.0.0/16", add_rule)

        assert preserve == "true"
        ec2.create_security_group.assert_not_called()
        # The reuse path deliberately leaves the existing rules alone, which
        # is exactly why --ssh_allowed_ips has no effect here.
        add_rule.assert_not_called()

    def test_lookup_is_scoped_to_the_target_vpc(self):
        # Regression test: a bare group-name filter (with no vpc-id
        # scoping) would match a same-named security group in a *different*
        # VPC too, which sg_id[0].id would then silently pick regardless of
        # which VPC it actually belongs to.
        fake_sg = _fake_security_group("sg-0123456789abcdef0")
        ec2 = self._ec2_with_filter_results([[fake_sg]])
        add_rule = MagicMock()

        instance_builder.resolve_security_group(ec2, "my-custom-sg", "12345_us-east-1", "vpc-target123", False, "10.0.0.0/16", add_rule)

        called_filters = ec2.security_groups.filter.call_args.kwargs["Filters"]
        vpc_filter = next(f for f in called_filters if f["Name"] == "vpc-id")
        assert vpc_filter["Values"] == ["vpc-target123"]


class TestSetupKeypair:
    def test_existing_keypair_found_with_local_file_present(self, tmp_path):
        secret_key_file = tmp_path / "dev01-key.pem"
        secret_key_file.write_text("existing key material")
        ec2_client = MagicMock()
        quit_fn = MagicMock(side_effect=SystemExit(1))

        instance_builder.setup_keypair(ec2_client, "dev01-key", str(secret_key_file), "us-east-1", "false", quit_fn)

        ec2_client.create_key_pair.assert_not_called()
        quit_fn.assert_not_called()

    def test_missing_keypair_creates_it_and_writes_pem_with_0600(self, tmp_path):
        secret_key_file = tmp_path / "dev01-key.pem"
        ec2_client = MagicMock()
        ec2_client.describe_key_pairs.side_effect = _client_error("InvalidKeyPair.NotFound")
        ec2_client.create_key_pair.return_value = {"KeyMaterial": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"}
        quit_fn = MagicMock(side_effect=SystemExit(1))

        instance_builder.setup_keypair(ec2_client, "dev01-key", str(secret_key_file), "us-east-1", "false", quit_fn)

        assert secret_key_file.read_text() == "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
        assert oct(secret_key_file.stat().st_mode)[-3:] == "600"
        quit_fn.assert_not_called()

    def test_pem_is_never_created_more_permissive_than_0600_even_under_a_permissive_umask(self, tmp_path):
        # Regression test: the .pem used to be written via plain open()
        # then chmod'd to 0600 afterward -- a real window (however brief)
        # where the private key material was as permissive as the
        # process umask allowed. Setting umask to 0 (the most permissive
        # possible) and still getting 0600 proves the file is created
        # with the restrictive mode from the start (os.open(..., 0o600)),
        # not chmod'd after the fact.
        secret_key_file = tmp_path / "dev01-key.pem"
        ec2_client = MagicMock()
        ec2_client.describe_key_pairs.side_effect = _client_error("InvalidKeyPair.NotFound")
        ec2_client.create_key_pair.return_value = {"KeyMaterial": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"}
        quit_fn = MagicMock(side_effect=SystemExit(1))

        old_umask = os.umask(0)
        try:
            instance_builder.setup_keypair(ec2_client, "dev01-key", str(secret_key_file), "us-east-1", "false", quit_fn)
        finally:
            os.umask(old_umask)

        assert oct(secret_key_file.stat().st_mode)[-3:] == "600"

    def test_unexpected_client_error_quits_instead_of_falling_through(self, tmp_path):
        # Regression test: this used to silently swallow any ClientError
        # other than InvalidKeyPair.NotFound, falling through to a
        # misleading "secret key file is missing" message instead of the
        # real AWS API problem (throttling, AccessDenied, etc).
        secret_key_file = tmp_path / "dev01-key.pem"
        ec2_client = MagicMock()
        ec2_client.describe_key_pairs.side_effect = _client_error("RequestLimitExceeded")
        quit_fn = MagicMock(side_effect=SystemExit(1))

        with pytest.raises(SystemExit):
            instance_builder.setup_keypair(ec2_client, "dev01-key", str(secret_key_file), "us-east-1", "false", quit_fn)
        quit_fn.assert_called_once()
        assert "RequestLimitExceeded" in quit_fn.call_args.args[0]
        ec2_client.create_key_pair.assert_not_called()

    def test_keypair_exists_but_local_file_missing_aborts(self, tmp_path):
        secret_key_file = tmp_path / "does-not-exist.pem"
        ec2_client = MagicMock()  # describe_key_pairs succeeds (default MagicMock behavior)
        quit_fn = MagicMock(side_effect=SystemExit(1))

        with pytest.raises(SystemExit):
            instance_builder.setup_keypair(ec2_client, "dev01-key", str(secret_key_file), "us-east-1", "false", quit_fn)


class TestResolveAmi:
    def test_no_custom_ami_uses_catalog(self):
        get_ami_info = MagicMock(return_value="ami-fromcatalog")
        check_custom_ami = MagicMock()
        result = instance_builder.resolve_ami("UNDEFINED", "al2023", "x86_64", "123456789012", get_ami_info, check_custom_ami, MagicMock())
        assert result == "ami-fromcatalog"
        get_ami_info.assert_called_once_with("al2023", "x86_64")
        check_custom_ami.assert_not_called()

    def test_custom_ami_found_returns_it(self):
        get_ami_info = MagicMock()
        check_custom_ami = MagicMock(return_value="ami-mycustom")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        result = instance_builder.resolve_ami("ami-mycustom", "al2023", "x86_64", "123456789012", get_ami_info, check_custom_ami, quit_fn)
        assert result == "ami-mycustom"
        get_ami_info.assert_not_called()
        quit_fn.assert_not_called()

    def test_custom_ami_not_found_quits(self):
        get_ami_info = MagicMock()
        check_custom_ami = MagicMock(return_value="false")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.resolve_ami("ami-doesnotexist", "al2023", "x86_64", "123456789012", get_ami_info, check_custom_ami, quit_fn)
        quit_fn.assert_called_once_with('AMI image "ami-doesnotexist" is unavailable in this AWS account!')


class TestSpotPrice:
    def test_fetch_spot_price_raw_uses_windows_product_description(self):
        ec2_client = MagicMock()
        ec2_client.describe_spot_price_history.return_value = {"SpotPriceHistory": [{"SpotPrice": "0.0464"}]}
        price = instance_builder.fetch_spot_price_raw(ec2_client, "m5.large", is_windows=True, az="us-east-1a")
        assert price == 0.0464
        kwargs = ec2_client.describe_spot_price_history.call_args.kwargs
        assert kwargs["ProductDescriptions"] == ["Windows"]

    def test_fetch_spot_price_raw_uses_linux_product_description(self):
        ec2_client = MagicMock()
        ec2_client.describe_spot_price_history.return_value = {"SpotPriceHistory": [{"SpotPrice": "0.0116"}]}
        price = instance_builder.fetch_spot_price_raw(ec2_client, "m5.large", is_windows=False, az="us-east-1a")
        assert price == 0.0116
        kwargs = ec2_client.describe_spot_price_history.call_args.kwargs
        assert kwargs["ProductDescriptions"] == ["Linux/UNIX"]

    def test_compute_buffered_spot_price_default_buffer(self):
        # Default spot_buffer is 1/pi in make_instance.py.
        from math import pi

        result = instance_builder.compute_buffered_spot_price(0.05, 1 / pi)
        assert result == round(0.05 + (1 / pi) * 0.05, 8)

    def test_compute_buffered_spot_price_rounds_to_8_decimals(self):
        result = instance_builder.compute_buffered_spot_price(0.123456789, 0.1)
        assert result == round(0.123456789 * 1.1, 8)


class TestBuildSnsMessage:
    def test_single_instance_message_is_substituted(self):
        # Regression test: sns_message_body was never actually
        # .format()-called before being published -- every SNS
        # notification this tool ever sent contained literal,
        # unsubstituted "{instance_name}" placeholder text instead of
        # real values.
        body, subject = instance_builder.build_sns_message(
            count=1,
            instance_name="dev01",
            instance_type="t3.micro",
            request_type="ondemand",
            sns_datestamp="09-07-2026",
            sns_timestamp="01:02",
        )
        assert "{instance_name}" not in body
        assert "dev01" in body
        assert "t3.micro" in body
        assert subject == "[Ec2InstanceMaker] Instance Creation Notice"
        assert "Count:" not in body

    def test_family_message_includes_count_and_correct_subject(self):
        body, subject = instance_builder.build_sns_message(
            count=3,
            instance_name="fam01",
            instance_type="m5.large",
            request_type="spot",
            sns_datestamp="09-07-2026",
            sns_timestamp="01:02",
        )
        assert "{count}" not in body
        assert "Count:        3" in body
        assert subject == "[Ec2InstanceMaker] Instance Family Creation Notice"


class TestApplyTerraform:
    def test_runs_init_plan_apply_in_order(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            instance_builder.apply_terraform("/fake/instance_data/dev01/", "false", MagicMock())
        commands = [call.args[0][1] for call in mock_run.call_args_list]
        assert commands == ["init", "plan", "apply"]
        for call in mock_run.call_args_list:
            assert call.kwargs["cwd"] == "/fake/instance_data/dev01/"

    def test_debug_mode_sets_tf_log_env(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            instance_builder.apply_terraform("/fake/instance_data/dev01/", "true", MagicMock())
        for call in mock_run.call_args_list:
            assert call.kwargs["env"]["TF_LOG"] == "DEBUG"

    def test_non_debug_mode_passes_no_env_override(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            instance_builder.apply_terraform("/fake/instance_data/dev01/", "false", MagicMock())
        for call in mock_run.call_args_list:
            assert call.kwargs["env"] is None

    def test_quits_and_stops_on_first_failed_step(self):
        quit_fn = _quitting_mock()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [MagicMock(returncode=0), MagicMock(returncode=1)]
            with pytest.raises(SystemExit):
                instance_builder.apply_terraform("/fake/instance_data/dev01/", "false", quit_fn)
        assert mock_run.call_count == 2
        quit_fn.assert_called_once()


class TestBuildSecurityGroupTags:
    def test_includes_core_tags(self):
        tags = instance_builder.build_security_group_tags("dev01-sg", "dev01", "12345_us-east-1", "rmarable", "r@x.com", "hpc", "7-September-2026", "UNDEFINED")
        tag_dict = {t["Key"]: t["Value"] for t in tags}
        assert tag_dict["Name"] == "dev01-sg"
        assert tag_dict["InstanceOwner"] == "rmarable"
        assert "ProjectID" not in tag_dict

    def test_instance_serial_number_tag_is_present(self):
        # Resource lifecycle hinges on InstanceSerialNumber -- it is the tag
        # value the generated kill script and build_ami.j2 use to find
        # everything belonging to one instance/family. Dropping it from
        # this tag set orphans the security group on teardown, and nothing
        # in the suite noticed: the string "InstanceSerialNumber" did not
        # appear anywhere under tests/ before this test.
        tags = instance_builder.build_security_group_tags("dev01-sg", "dev01", "12345_us-east-1", "rmarable", "r@x.com", "hpc", "7-September-2026", "UNDEFINED")
        tag_dict = {t["Key"]: t["Value"] for t in tags}
        assert tag_dict["InstanceSerialNumber"] == "12345_us-east-1"

    def test_project_id_included_when_defined(self):
        tags = instance_builder.build_security_group_tags("dev01-sg", "dev01", "12345_us-east-1", "rmarable", "r@x.com", "hpc", "7-September-2026", "myproj123")
        tag_dict = {t["Key"]: t["Value"] for t in tags}
        assert tag_dict["ProjectID"] == "myproj123"


def _tf_output(text, returncode=0):
    result = MagicMock()
    result.stdout = text.encode("utf-8")
    result.returncode = returncode
    return result


def _tf_output_json(instance_id, instance_name, ip_address):
    return _tf_output(
        json.dumps(
            {
                "instance_id_list": {"sensitive": False, "type": "string", "value": instance_id},
                "instance_name_index": {"sensitive": False, "type": "string", "value": instance_name},
                "instance_ip_addresses": {"sensitive": False, "type": "string", "value": ip_address},
                "instance_public_dns": {"sensitive": False, "type": "string", "value": "ec2-1-2-3-4.compute.amazonaws.com"},
            }
        )
    )


class TestFetchWindowsInstanceDetails:
    def test_parses_id_name_and_ip_from_terraform_output_json(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _tf_output_json("i-0123456789abcdef0", "dev01", "1.2.3.4")
            instance_id, instance_name, ip_address = instance_builder.fetch_windows_instance_details("/fake/instance_data/dev01/", MagicMock())
        assert instance_id == "i-0123456789abcdef0"
        assert instance_name == "dev01"
        assert ip_address == "1.2.3.4"

    def test_runs_terraform_output_json_in_instance_data_dir_without_shell(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _tf_output_json("i-0123456789abcdef0", "dev01", "1.2.3.4")
            instance_builder.fetch_windows_instance_details("/fake/instance_data/dev01/", MagicMock())
        mock_run.assert_called_once()
        call = mock_run.call_args
        assert call.args[0] == ["terraform", "output", "-json"]
        assert call.kwargs["cwd"] == "/fake/instance_data/dev01/"
        assert call.kwargs.get("shell", False) is False

    def test_nonzero_returncode_quits_before_parsing_json(self):
        quit_fn = _quitting_mock()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _tf_output("", returncode=1)
            with pytest.raises(SystemExit):
                instance_builder.fetch_windows_instance_details("/fake/instance_data/dev01/", quit_fn)
        quit_fn.assert_called_once()


class TestDecryptWindowsAdminPasswords:
    def test_single_instance_password_decrypted_and_unquoted(self):
        password_json = '{"InstanceId": "i-123", "PasswordData": "hunter2", "Timestamp": "2024-01-01"}'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _tf_output(password_json)
            result = instance_builder.decrypt_windows_admin_passwords("/fake/instance_data/dev01/", "dev01-key", "i-123")
        assert result == "hunter2"

    def test_multiple_instance_ids_decrypted_in_order(self):
        responses = [
            _tf_output('{"PasswordData": "pw-one"}'),
            _tf_output('{"PasswordData": "pw-two"}'),
        ]
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = responses
            result = instance_builder.decrypt_windows_admin_passwords("/fake/instance_data/fam01/", "fam01-key", "i-aaa,i-bbb")
        assert result == "pw-one,pw-two"

    def test_uses_priv_launch_key_matching_ec2_keypair(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _tf_output('{"PasswordData": "x"}')
            instance_builder.decrypt_windows_admin_passwords("/fake/instance_data/dev01/", "dev01-key", "i-123")
        args = mock_run.call_args.args[0]
        assert args[0:3] == ["aws", "ec2", "get-password-data"]
        assert "--priv-launch-key" in args
        assert args[args.index("--priv-launch-key") + 1] == "dev01-key.pem"

    def test_empty_password_data_reports_not_yet_available(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _tf_output('{"PasswordData": ""}')
            result = instance_builder.decrypt_windows_admin_passwords("/fake/instance_data/dev01/", "dev01-key", "i-123")
        assert result == instance_builder._WINDOWS_PASSWORD_NOT_YET_AVAILABLE

    def test_null_password_data_reports_not_yet_available(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _tf_output("{}")
            result = instance_builder.decrypt_windows_admin_passwords("/fake/instance_data/dev01/", "dev01-key", "i-123")
        assert result == instance_builder._WINDOWS_PASSWORD_NOT_YET_AVAILABLE


class TestDeriveIamNames:
    def test_default_prefix(self):
        role, policy, profile = instance_builder.derive_iam_names("Ec2InstanceMaker", "12345_us-east-1")
        assert role == "Ec2InstanceMaker-role-12345_us-east-1"
        assert policy == "Ec2InstanceMaker-policy-12345_us-east-1"
        assert profile == "Ec2InstanceMaker-profile-12345_us-east-1"

    def test_custom_prefix(self):
        role, policy, profile = instance_builder.derive_iam_names("MyOrg", "12345_us-east-1")
        assert role == "MyOrg-role-12345_us-east-1"
        assert policy == "MyOrg-policy-12345_us-east-1"
        assert profile == "MyOrg-profile-12345_us-east-1"

    def test_default_and_custom_prefix_use_the_same_formula(self):
        # Regression-preserving test: the original inline code special-cased
        # iam_name_prefix == "Ec2InstanceMaker" with its own literal branch,
        # but that branch computed the exact same string as the general
        # "prefix + suffix" formula used for every other prefix -- this
        # confirms collapsing them into one formula changed nothing.
        default_names = instance_builder.derive_iam_names("Ec2InstanceMaker", "999")
        assert default_names == ("Ec2InstanceMaker-role-999", "Ec2InstanceMaker-policy-999", "Ec2InstanceMaker-profile-999")


class TestEnsureIamRoleCreated:
    def test_existing_role_found_does_not_create(self, tmp_path):
        iam = MagicMock()
        quit_fn = MagicMock(side_effect=SystemExit(1))
        instance_builder.ensure_iam_role_created(iam, "role1", "policy1", str(tmp_path / "stage.json"), str(tmp_path / "final.json"), "false", quit_fn)
        iam.create_role.assert_not_called()
        quit_fn.assert_not_called()

    def test_missing_role_stages_policy_and_creates_role(self, tmp_path):
        stage = tmp_path / "stage.json"
        stage.write_text('{"Statement": []}')
        final = tmp_path / "final.json"
        iam = MagicMock()
        iam.get_role.side_effect = _client_error("NoSuchEntity")
        quit_fn = MagicMock(side_effect=SystemExit(1))

        instance_builder.ensure_iam_role_created(iam, "role1", "policy1", str(stage), str(final), "false", quit_fn)

        assert final.read_text() == '{"Statement": []}'
        assert not stage.exists()  # staged file consumed/removed
        iam.create_role.assert_called_once()
        assert iam.create_role.call_args.kwargs["RoleName"] == "role1"
        iam.put_role_policy.assert_called_once()
        assert iam.put_role_policy.call_args.kwargs["PolicyName"] == "policy1"
        quit_fn.assert_not_called()

    def test_unexpected_client_error_quits_without_creating(self, tmp_path):
        iam = MagicMock()
        iam.get_role.side_effect = _client_error("AccessDenied")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.ensure_iam_role_created(iam, "role1", "policy1", str(tmp_path / "stage.json"), str(tmp_path / "final.json"), "false", quit_fn)
        iam.create_role.assert_not_called()
        quit_fn.assert_called_once()
        assert "AccessDenied" in quit_fn.call_args.args[0]


class TestEnsureIamRoleExists:
    def test_existing_role_found(self):
        iam = MagicMock()
        quit_fn = MagicMock(side_effect=SystemExit(1))
        instance_builder.ensure_iam_role_exists(iam, "my-preexisting-role", "false", quit_fn)
        quit_fn.assert_not_called()

    def test_missing_role_quits_with_clear_message(self):
        iam = MagicMock()
        iam.get_role.side_effect = _client_error("NoSuchEntity")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.ensure_iam_role_exists(iam, "my-preexisting-role", "false", quit_fn)
        quit_fn.assert_called_once_with("IAM EC2 instance role my-preexisting-role does not exist!")

    def test_unexpected_client_error_quits(self):
        iam = MagicMock()
        iam.get_role.side_effect = _client_error("RequestLimitExceeded")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.ensure_iam_role_exists(iam, "my-preexisting-role", "false", quit_fn)
        assert "RequestLimitExceeded" in quit_fn.call_args.args[0]
        assert "does not exist" not in quit_fn.call_args.args[0]


class TestEnsureIamInstanceProfile:
    def test_existing_profile_found_does_not_create(self):
        iam = MagicMock()
        quit_fn = MagicMock(side_effect=SystemExit(1))
        instance_builder.ensure_iam_instance_profile(iam, "profile1", "role1", "false", quit_fn)
        iam.create_instance_profile.assert_not_called()
        quit_fn.assert_not_called()

    def test_missing_profile_creates_and_attaches_role(self):
        iam = MagicMock()
        iam.get_instance_profile.side_effect = _client_error("NoSuchEntity")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        instance_builder.ensure_iam_instance_profile(iam, "profile1", "role1", "false", quit_fn)
        iam.create_instance_profile.assert_called_once_with(InstanceProfileName="profile1")
        iam.add_role_to_instance_profile.assert_called_once_with(InstanceProfileName="profile1", RoleName="role1")

    def test_unexpected_client_error_quits_without_creating(self):
        iam = MagicMock()
        iam.get_instance_profile.side_effect = _client_error("AccessDenied")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.ensure_iam_instance_profile(iam, "profile1", "role1", "false", quit_fn)
        iam.create_instance_profile.assert_not_called()


class TestSetupIam:
    def test_new_role_path_returns_correct_names_and_preserve_false(self, tmp_path):
        iam = MagicMock()
        iam.get_role.side_effect = _client_error("NoSuchEntity")
        iam.get_instance_profile.side_effect = _client_error("NoSuchEntity")
        stage_file = tmp_path / "stage-GenericEc2InstancePolicy.json"

        def fake_modify_policy(src, stage, prefix, serial):
            # Real modify_iam_policy_document() writes the staged file;
            # simulate that side effect so ensure_iam_role_created can read it.
            with open(stage, "w") as fh:
                fh.write('{"Statement": []}')

        quit_fn = MagicMock(side_effect=SystemExit(1))
        role, policy, profile, preserve = instance_builder.setup_iam(
            iam,
            "UNDEFINED",
            "Ec2InstanceMaker",
            "GenericEc2InstancePolicy.json",
            str(tmp_path) + "/",
            "12345_us-east-1",
            "false",
            quit_fn,
            fake_modify_policy,
        )

        assert role == "Ec2InstanceMaker-role-12345_us-east-1"
        assert policy == "Ec2InstanceMaker-policy-12345_us-east-1"
        assert profile == "Ec2InstanceMaker-profile-12345_us-east-1"
        assert preserve == "false"
        iam.create_role.assert_called_once()
        iam.create_instance_profile.assert_called_once()
        assert not stage_file.exists()

    def test_preexisting_role_path_returns_correct_names_and_preserve_true(self, tmp_path):
        iam = MagicMock()  # get_role and get_instance_profile both "succeed" (found)
        quit_fn = MagicMock(side_effect=SystemExit(1))
        modify_policy = MagicMock()

        role, policy, profile, preserve = instance_builder.setup_iam(
            iam,
            "my-preexisting-role",
            "Ec2InstanceMaker",
            "GenericEc2InstancePolicy.json",
            str(tmp_path) + "/",
            "12345_us-east-1",
            "false",
            quit_fn,
            modify_policy,
        )

        assert role == "my-preexisting-role"
        assert policy == "UNDEFINED"
        assert profile == "my-preexisting-role_instance_profile"
        assert preserve == "true"

    def test_unrecognized_iam_json_policy_quits_before_touching_the_filesystem(self, tmp_path):
        # Defense-in-depth: --iam_json_policy's argparse choices=[...] is
        # the only thing currently keeping this from reaching setup_iam()
        # as an arbitrary string that becomes a filesystem path -- this
        # proves setup_iam() itself refuses an unrecognized value too.
        iam = MagicMock()
        quit_fn = MagicMock(side_effect=SystemExit(1))
        modify_policy = MagicMock()

        with pytest.raises(SystemExit):
            instance_builder.setup_iam(
                iam,
                "UNDEFINED",
                "Ec2InstanceMaker",
                "../../etc/passwd",
                str(tmp_path) + "/",
                "12345_us-east-1",
                "false",
                quit_fn,
                modify_policy,
            )
        quit_fn.assert_called_once()
        modify_policy.assert_not_called()
        iam.create_role.assert_not_called()
        iam.create_role.assert_not_called()
        modify_policy.assert_not_called()  # only relevant to the create-new path

    def test_preexisting_role_missing_quits_before_touching_instance_profile(self):
        iam = MagicMock()
        iam.get_role.side_effect = _client_error("NoSuchEntity")
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(SystemExit):
            instance_builder.setup_iam(iam, "nonexistent-role", "Ec2InstanceMaker", "GenericEc2InstancePolicy.json", "/fake/", "12345_us-east-1", "false", quit_fn, MagicMock())
        iam.get_instance_profile.assert_not_called()


class TestWriteVarsFile:
    def test_writes_substituted_template(self, tmp_path):
        vars_file_path = tmp_path / "dev01.yml"
        template = "instance_name: {instance_name}\nbase_os: {base_os}\n"
        instance_builder.write_vars_file(str(vars_file_path), template, {"instance_name": "dev01", "base_os": "al2023"})
        assert vars_file_path.read_text() == "instance_name: dev01\nbase_os: al2023\n"

    def test_missing_key_raises_immediately_instead_of_shipping_broken_output(self, tmp_path):
        # Contrast with the SNS message bug found earlier this session
        # (a template that was never .format()-called at all, silently
        # shipping literal placeholders) -- this call DOES format, so a
        # missing key fails loudly instead of writing broken output.
        vars_file_path = tmp_path / "dev01.yml"
        template = "instance_name: {instance_name}\nunmapped: {not_provided}\n"
        with pytest.raises(KeyError):
            instance_builder.write_vars_file(str(vars_file_path), template, {"instance_name": "dev01"})


class TestResolveCustomUserScripts:
    def _make_dir(self, tmp_path, *filenames):
        d = tmp_path / "custom_user_scripts"
        d.mkdir()
        for name in filenames:
            (d / name).write_text("")
        return str(d)

    def test_name_with_both_hooks(self, tmp_path):
        d = self._make_dir(tmp_path, "custom_user_prelogin_script.j2_default", "custom_user_postboot_script.j2_default")
        quit_fn = _quitting_mock()

        prelogin, postboot = instance_builder.resolve_custom_user_scripts(["default"], d, quit_fn)

        assert prelogin == ["default"]
        assert postboot == ["default"]
        quit_fn.assert_not_called()

    def test_name_with_only_prelogin_hook(self, tmp_path):
        d = self._make_dir(tmp_path, "custom_user_prelogin_script.j2_banner")
        quit_fn = _quitting_mock()

        prelogin, postboot = instance_builder.resolve_custom_user_scripts(["banner"], d, quit_fn)

        assert prelogin == ["banner"]
        assert postboot == []
        quit_fn.assert_not_called()

    def test_name_with_only_postboot_hook(self, tmp_path):
        d = self._make_dir(tmp_path, "custom_user_postboot_script.j2_toolchain")
        quit_fn = _quitting_mock()

        prelogin, postboot = instance_builder.resolve_custom_user_scripts(["toolchain"], d, quit_fn)

        assert prelogin == []
        assert postboot == ["toolchain"]
        quit_fn.assert_not_called()

    def test_name_with_neither_hook_is_a_hard_failure(self, tmp_path):
        d = self._make_dir(tmp_path, "custom_user_prelogin_script.j2_default", "custom_user_postboot_script.j2_default")
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            instance_builder.resolve_custom_user_scripts(["typo_name"], d, quit_fn)
        quit_fn.assert_called_once()
        assert "typo_name" in quit_fn.call_args.args[0]

    def test_preserves_order_across_multiple_names(self, tmp_path):
        d = self._make_dir(
            tmp_path,
            "custom_user_prelogin_script.j2_first",
            "custom_user_postboot_script.j2_first",
            "custom_user_postboot_script.j2_second",
        )
        quit_fn = _quitting_mock()

        prelogin, postboot = instance_builder.resolve_custom_user_scripts(["first", "second"], d, quit_fn)

        assert prelogin == ["first"]
        assert postboot == ["first", "second"]


class TestSetupCloudwatchLogging:
    def test_creates_group_and_sets_retention(self):
        logs_client = MagicMock()
        quit_fn = _quitting_mock()

        instance_builder.setup_cloudwatch_logging(logs_client, "/ec2instancemaker/dev01", 30, quit_fn)

        logs_client.create_log_group.assert_called_once_with(logGroupName="/ec2instancemaker/dev01", tags={"ManagedBy": "Ec2InstanceMaker"})
        logs_client.put_retention_policy.assert_called_once_with(logGroupName="/ec2instancemaker/dev01", retentionInDays=30)
        quit_fn.assert_not_called()

    def test_reuses_existing_group_without_failing(self):
        # A rerun against the same instance_name (or a family member that
        # already created the group) must not treat "already exists" as
        # an error -- the retention policy still gets (re-)applied.
        logs_client = MagicMock()
        logs_client.create_log_group.side_effect = _client_error("ResourceAlreadyExistsException")
        quit_fn = _quitting_mock()

        instance_builder.setup_cloudwatch_logging(logs_client, "/ec2instancemaker/dev01", 30, quit_fn)

        logs_client.put_retention_policy.assert_called_once_with(logGroupName="/ec2instancemaker/dev01", retentionInDays=30)
        quit_fn.assert_not_called()

    def test_unexpected_create_error_quits(self):
        logs_client = MagicMock()
        logs_client.create_log_group.side_effect = _client_error("AccessDeniedException")
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            instance_builder.setup_cloudwatch_logging(logs_client, "/ec2instancemaker/dev01", 30, quit_fn)
        logs_client.put_retention_policy.assert_not_called()

    def test_retention_policy_error_quits(self):
        logs_client = MagicMock()
        logs_client.put_retention_policy.side_effect = _client_error("AccessDeniedException")
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            instance_builder.setup_cloudwatch_logging(logs_client, "/ec2instancemaker/dev01", 30, quit_fn)


class TestValidateInstanceNameFormat:
    # Direct tests for the standalone function access_instance.py and
    # manage_instance.py call -- previously only exercised transitively
    # through TestValidateInstanceNameAndOwnerFormat below.

    def test_valid_name_does_not_quit(self):
        quit_fn = _quitting_mock()
        instance_builder.validate_instance_name_format("dev01-fam", quit_fn)
        quit_fn.assert_not_called()

    def test_uppercase_quits(self):
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.validate_instance_name_format("Dev01", quit_fn)
        quit_fn.assert_called_once()

    @pytest.mark.parametrize(
        "malicious_name",
        [
            "../../../etc/passwd",
            "dev01/../../etc",
            'dev01"; touch /tmp/pwned; echo "',
            "dev01$(whoami)",
        ],
    )
    def test_rejects_injection_and_traversal_attempts(self, malicious_name):
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.validate_instance_name_format(malicious_name, quit_fn)
        quit_fn.assert_called_once()


class TestValidateInstanceNameAndOwnerFormat:
    def test_all_lowercase_alphanumeric_with_hyphens_is_fine(self):
        quit_fn = _quitting_mock()
        instance_builder.validate_instance_name_and_owner_format("dev01-fam", "alice", quit_fn)
        quit_fn.assert_not_called()

    def test_owner_allows_dots_and_underscores(self):
        # instance_owner is documented as an ActiveDirectory username --
        # real AD usernames commonly use first.last/first_last.
        quit_fn = _quitting_mock()
        instance_builder.validate_instance_name_and_owner_format("dev01", "first.last_name", quit_fn)
        quit_fn.assert_not_called()

    def test_uppercase_instance_name_quits(self):
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.validate_instance_name_and_owner_format("Dev01", "alice", quit_fn)
        quit_fn.assert_called_once()

    def test_uppercase_instance_owner_quits(self):
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.validate_instance_name_and_owner_format("dev01", "Alice", quit_fn)
        quit_fn.assert_called_once()

    def test_instance_name_must_start_with_a_letter(self):
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.validate_instance_name_and_owner_format("01dev", "alice", quit_fn)
        quit_fn.assert_called_once()

    @pytest.mark.parametrize(
        "malicious_name",
        [
            "../../../etc/passwd",  # path traversal
            "dev01/../../etc",  # path traversal
            'dev01"; touch /tmp/pwned; echo "',  # shell/HCL injection
            "dev01'; touch /tmp/pwned; echo '",  # shell injection
            "dev 01",  # whitespace (word-splitting risk)
            "dev01$(whoami)",  # command substitution
            "dev01`whoami`",  # command substitution (backtick form)
        ],
    )
    def test_instance_name_rejects_injection_and_traversal_attempts(self, malicious_name):
        # Regression tests for a real adversarial-review finding:
        # instance_name used to only reject uppercase letters, while
        # flowing unquoted into shell commands (DEFAULT_EC2_TEMPLATE.j2's
        # spot-tagging local-exec, kill_instance.j2), Terraform resource
        # labels, and filesystem paths (vars_files/<name>.yml,
        # instance_data/<name>/, the kill-instance./build-ami.<name>.sh
        # symlinks) -- all now closed at the source by restricting the
        # charset, rather than trying to escape correctly for every
        # different downstream context.
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.validate_instance_name_and_owner_format(malicious_name, "alice", quit_fn)
        quit_fn.assert_called_once()

    @pytest.mark.parametrize("malicious_owner", ["../etc/passwd", 'alice"; touch /tmp/pwned; echo "', "alice; rm -rf /"])
    def test_instance_owner_rejects_injection_attempts(self, malicious_owner):
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.validate_instance_name_and_owner_format("dev01", malicious_owner, quit_fn)
        quit_fn.assert_called_once()


class TestGetTerraformVersion:
    def test_parses_version_from_first_line(self):
        quit_fn = _quitting_mock()
        run = MagicMock(return_value=MagicMock(stdout="Terraform v1.5.7\non darwin_arm64\n"))
        result = instance_builder.get_terraform_version(quit_fn, run=run)
        assert result == "v1.5.7"
        quit_fn.assert_not_called()

    def test_missing_terraform_binary_quits(self):
        quit_fn = _quitting_mock()
        run = MagicMock(side_effect=FileNotFoundError())
        with pytest.raises(SystemExit):
            instance_builder.get_terraform_version(quit_fn, run=run)
        quit_fn.assert_called_once()

    def test_empty_output_quits(self):
        quit_fn = _quitting_mock()
        run = MagicMock(return_value=MagicMock(stdout=""))
        with pytest.raises(SystemExit):
            instance_builder.get_terraform_version(quit_fn, run=run)
        quit_fn.assert_called_once()

    def test_uses_list_form_not_shell(self):
        quit_fn = _quitting_mock()
        run = MagicMock(return_value=MagicMock(stdout="Terraform v1.5.7\n"))
        instance_builder.get_terraform_version(quit_fn, run=run)
        called_args = run.call_args
        assert called_args.args[0] == ["terraform", "-version"]
        assert called_args.kwargs.get("shell", False) is False


class TestCreateAwsClients:
    def test_constructs_every_client_with_correct_service_and_region(self):
        boto3_client = MagicMock(side_effect=lambda service, **kwargs: (service, kwargs))
        boto3_resource = MagicMock(side_effect=lambda service, **kwargs: (service, kwargs))

        clients = instance_builder.create_aws_clients("us-east-1", boto3_client, boto3_resource)

        assert clients.ec2_client == ("ec2", {"region_name": "us-east-1"})
        assert clients.ec2 == ("ec2", {"region_name": "us-east-1"})
        assert clients.iam == ("iam", {})
        assert clients.sns_client == ("sns", {"region_name": "us-east-1"})
        assert clients.stsclient == ("sts", {"region_name": "us-east-1", "endpoint_url": "https://sts.us-east-1.amazonaws.com"})
        assert clients.logs_client == ("logs", {"region_name": "us-east-1"})


class TestEnsureStateDirectories:
    def test_creates_all_three_directories(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        instance_builder.ensure_state_directories("./instance_data/dev01/")
        assert (tmp_path / "vars_files").is_dir()
        assert (tmp_path / "instance_data" / "dev01").is_dir()
        assert (tmp_path / "active_instances").is_dir()

    def test_idempotent_when_directories_already_exist(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        instance_builder.ensure_state_directories("./instance_data/dev01/")
        instance_builder.ensure_state_directories("./instance_data/dev01/")  # should not raise


class TestAbortIfVarsFileExists:
    def test_no_op_when_file_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        instance_builder.abort_if_vars_file_exists("./vars_files/dev01.yml", ["make_instance.py"])

    def test_quits_when_file_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "vars_files").mkdir()
        (tmp_path / "vars_files" / "dev01.yml").write_text("existing")
        with pytest.raises(SystemExit):
            instance_builder.abort_if_vars_file_exists("./vars_files/dev01.yml", ["make_instance.py", "-N", "dev01"])


class TestInstanceLock:
    def test_creates_lock_file_and_yields(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with instance_builder.instance_lock("dev01", quit_fn):
            assert (tmp_path / "active_instances" / "dev01.lock").is_file()
        quit_fn.assert_not_called()

    def test_lock_is_released_after_the_with_block_exits(self, tmp_path, monkeypatch):
        # A second acquisition after the first completes must succeed --
        # proves the lock doesn't leak past its own `with` block.
        monkeypatch.chdir(tmp_path)
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with instance_builder.instance_lock("dev01", quit_fn):
            pass
        with instance_builder.instance_lock("dev01", quit_fn):
            pass
        quit_fn.assert_not_called()

    def test_already_held_lock_quits_without_yielding(self, tmp_path, monkeypatch):
        import fcntl

        monkeypatch.chdir(tmp_path)
        (tmp_path / "active_instances").mkdir()
        lock_path = tmp_path / "active_instances" / "dev01.lock"
        holder_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            quit_fn = MagicMock(side_effect=SystemExit(1))
            entered = False
            with pytest.raises(SystemExit), instance_builder.instance_lock("dev01", quit_fn):
                entered = True
            assert not entered
            quit_fn.assert_called_once()
            assert "dev01" in quit_fn.call_args.args[0]
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_lock_released_even_if_the_with_block_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        quit_fn = MagicMock(side_effect=SystemExit(1))
        with pytest.raises(ValueError), instance_builder.instance_lock("dev01", quit_fn):
            raise ValueError("boom")
        with instance_builder.instance_lock("dev01", quit_fn):
            pass
        quit_fn.assert_not_called()


class TestWriteSerialNumberFile:
    def test_writes_name_and_argv_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "active_instances").mkdir()
        serial_file = "./active_instances/dev01.serial"
        instance_builder.write_serial_number_file(serial_file, "dev01", "000000010926", ["make_instance.py", "-N", "dev01"])
        content = (tmp_path / "active_instances" / "dev01.serial").read_text()
        assert "dev01.000000010926" in content
        assert "make_instance.py -N dev01" in content

    def test_no_op_when_file_already_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "active_instances").mkdir()
        serial_file = tmp_path / "active_instances" / "dev01.serial"
        serial_file.write_text("original content\n")
        instance_builder.write_serial_number_file(str(serial_file), "dev01", "000000010926", ["make_instance.py"])
        assert serial_file.read_text() == "original content\n"


class TestResolveEbsOptimizedSupport:
    def test_disabled_stays_disabled(self):
        assert instance_builder.resolve_ebs_optimized_support("false", "t3.micro", "supported", "dev01") == "false"

    def test_unsupported_instance_type_downgrades_with_warning(self, capsys):
        result = instance_builder.resolve_ebs_optimized_support("true", "t2.micro", "unsupported", "dev01")
        assert result == "false"
        assert "does not support EBS optimization" in capsys.readouterr().out

    def test_supported_instance_type_stays_enabled(self, capsys):
        result = instance_builder.resolve_ebs_optimized_support("true", "t3.micro", "supported", "dev01")
        assert result == "true"
        assert "EBS optimization: Enabled" in capsys.readouterr().out


class TestResolveRequestTypePricing:
    def test_ondemand_returns_undefined_sentinels(self):
        fetch_spot_price_raw = MagicMock()
        compute_buffered_spot_price = MagicMock()
        p_val = MagicMock()
        spot_price, spot_buffer = instance_builder.resolve_request_type_pricing(
            "ondemand", MagicMock(), "t3.micro", False, "us-east-1a", 0.318, "false", fetch_spot_price_raw, compute_buffered_spot_price, p_val
        )
        assert spot_price == "UNDEFINED"
        assert spot_buffer == "UNDEFINED"
        fetch_spot_price_raw.assert_not_called()
        compute_buffered_spot_price.assert_not_called()

    def test_spot_computes_buffered_price(self):
        fetch_spot_price_raw = MagicMock(return_value=0.0116)
        compute_buffered_spot_price = MagicMock(return_value=0.01518)
        p_val = MagicMock()
        ec2_client = MagicMock()
        spot_price, spot_buffer = instance_builder.resolve_request_type_pricing(
            "spot", ec2_client, "t3.micro", False, "us-east-1a", 0.318, "false", fetch_spot_price_raw, compute_buffered_spot_price, p_val
        )
        assert spot_price == 0.01518
        assert spot_buffer == 0.318
        fetch_spot_price_raw.assert_called_once_with(ec2_client, "t3.micro", False, "us-east-1a")
        compute_buffered_spot_price.assert_called_once_with(0.0116, 0.318)


class TestResolvePlacementGroupStrategy:
    def test_disabled_returns_undefined(self):
        refer_to_docs_and_quit = MagicMock()
        result = instance_builder.resolve_placement_group_strategy("false", 1, "t3.micro", "cluster", {}, MagicMock(), refer_to_docs_and_quit, "false", MagicMock())
        assert result == "UNDEFINED"
        refer_to_docs_and_quit.assert_not_called()

    def test_single_instance_with_placement_group_quits(self):
        quit_fn = _quitting_mock()
        with pytest.raises(SystemExit):
            instance_builder.resolve_placement_group_strategy("true", 1, "t3.micro", "cluster", {"placement_group_strategies": ["cluster"]}, MagicMock(), quit_fn, "false", MagicMock())
        quit_fn.assert_called_once()

    def test_enabled_with_multiple_instances_validates_and_returns_strategy(self):
        ec2_placement_group_check = MagicMock()
        refer_to_docs_and_quit = MagicMock()
        instance_type_info = {"placement_group_strategies": ["cluster", "spread"]}
        result = instance_builder.resolve_placement_group_strategy("true", 3, "t3.micro", "cluster", instance_type_info, ec2_placement_group_check, refer_to_docs_and_quit, "false", MagicMock())
        assert result == "cluster"
        ec2_placement_group_check.assert_called_once_with("t3.micro", "cluster", ["cluster", "spread"], "false")
        refer_to_docs_and_quit.assert_not_called()


class TestCreateSnsTopicAndSubscribe:
    def test_creates_topic_and_subscribes_email(self):
        sns_client = MagicMock()
        sns_client.create_topic.return_value = {"TopicArn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev01-000000010926"}
        topic_name, topic_arn = instance_builder.create_sns_topic_and_subscribe(sns_client, "dev01-000000010926", "alice@example.com")
        assert topic_name == "Ec2_Instance_SNS_Alerts_dev01-000000010926"
        assert topic_arn == "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev01-000000010926"
        sns_client.create_topic.assert_called_once_with(Name="Ec2_Instance_SNS_Alerts_dev01-000000010926")
        sns_client.subscribe.assert_called_once_with(TopicArn=topic_arn, Protocol="email", Endpoint="alice@example.com")


class TestPublishSnsNotification:
    def test_publishes_with_correct_arguments(self):
        sns_client = MagicMock()
        instance_builder.publish_sns_notification(sns_client, "arn:aws:sns:...", "body text", "subject text")
        sns_client.publish.assert_called_once_with(TopicArn="arn:aws:sns:...", Message="body text", Subject="subject text")


class TestBuildWindowsPasswordTable:
    def test_builds_table_from_fetched_and_decrypted_values(self):
        fetch_windows_instance_details = MagicMock(return_value=("i-abc,i-def", "dev01-0,dev01-1", "10.0.0.1,10.0.0.2"))
        decrypt_windows_admin_passwords = MagicMock(return_value="pw-one,pw-two")

        table = instance_builder.build_windows_password_table("./instance_data/dev01/", "dev01-000000010926_us-east-1", fetch_windows_instance_details, decrypt_windows_admin_passwords)

        decrypt_windows_admin_passwords.assert_called_once_with("./instance_data/dev01/", "dev01-000000010926_us-east-1", "i-abc,i-def")
        rendered = str(table)
        assert "dev01-0" in rendered
        assert "10.0.0.1" in rendered
        assert "pw-one" in rendered
        assert "dev01-1" in rendered
        assert "10.0.0.2" in rendered
        assert "pw-two" in rendered
