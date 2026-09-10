"""Unit tests for mcp_server.py's MCP tools. The AWS-backed read-only tools
(list_instances, get_instance_status) are tested through their underlying
_list_instances/_get_instance_status functions with a mocked EC2Client --
same dependency-injection pattern as tests/test_manage_instance.py -- plus
one test per public @mcp.tool() wrapper confirming it actually constructs a
real boto3 client and delegates. get_build_record makes no AWS call, so
it's tested directly. build_instance/destroy_instance are tested by patching
run_build()/terminate_via_kill_script() directly on mcp_server's own
namespace (same monkeypatch.setattr(module, name, ...) convention
tests/test_make_instance_integration.py uses) -- neither test ever touches
real AWS or a real subprocess. TestBuildInstanceEndToEnd additionally
drives the real run_build() with only the AWS/Terraform boundary mocked
(same helper pattern as tests/test_make_instance_integration.py), proving
build_instance's argv actually reaches a working build, not just that the
argv itself looks right.
"""

import os
from unittest.mock import MagicMock, mock_open, patch

import pytest
from botocore.exceptions import ClientError
from mcp.server.mcpserver.exceptions import ToolError

import make_instance
import mcp_server
from instance_builder import AwsClients
from make_instance import BuildReport

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _client_error(code, message="error"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "SomeOperation")


def _ec2_client(instances, pages=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = pages if pages is not None else [{"Reservations": [{"Instances": instances}]}]
    return client


ONDEMAND_INSTANCE = {
    "InstanceId": "i-ondemand01",
    "InstanceType": "t3.micro",
    "State": {"Name": "running"},
    "Tags": [{"Key": "Name", "Value": "dev01"}, {"Key": "OperatingSystem", "Value": "al2023"}, {"Key": "InstanceOwner", "Value": "rmarable"}],
}

SPOT_INSTANCE = {
    "InstanceId": "i-spot01",
    "InstanceType": "t3.micro",
    "State": {"Name": "running"},
    "InstanceLifecycle": "spot",
    "Tags": [{"Key": "Name", "Value": "dev01-1"}],
}


class TestMcpQuit:
    def test_raises_runtime_error(self):
        with pytest.raises(ToolError, match="boom"):
            mcp_server._mcp_quit("boom")


class TestInstanceSummary:
    def test_ondemand_instance_reports_not_spot(self):
        summary = mcp_server._instance_summary(ONDEMAND_INSTANCE)
        assert summary == {
            "instance_id": "i-ondemand01",
            "name": "dev01",
            "state": "running",
            "instance_type": "t3.micro",
            "base_os": "al2023",
            "instance_owner": "rmarable",
            "is_spot": False,
        }

    def test_spot_instance_reports_spot_and_defaults_missing_tags(self):
        summary = mcp_server._instance_summary(SPOT_INSTANCE)
        assert summary["is_spot"] is True
        assert summary["base_os"] == "?"
        assert summary["instance_owner"] == "?"


class TestListInstances:
    def test_returns_summaries_for_every_managed_instance(self):
        client = _ec2_client([ONDEMAND_INSTANCE, SPOT_INSTANCE])
        result = mcp_server._list_instances(client, "us-east-1")
        assert [r["instance_id"] for r in result] == ["i-ondemand01", "i-spot01"]

    def test_empty_result_is_not_an_error(self):
        client = _ec2_client([])
        assert mcp_server._list_instances(client, "us-east-1") == []

    def test_api_error_raises_runtime_error(self):
        client = _ec2_client([])
        client.get_paginator.return_value.paginate.side_effect = _client_error("Throttling")
        with pytest.raises(ToolError):
            mcp_server._list_instances(client, "us-east-1")

    def test_tool_wrapper_constructs_real_client_for_region(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client:
            mock_boto3_client.return_value = _ec2_client([ONDEMAND_INSTANCE])
            result = mcp_server.list_instances("us-west-2")
        mock_boto3_client.assert_called_once_with("ec2", region_name="us-west-2")
        assert result[0]["instance_id"] == "i-ondemand01"


class TestGetInstanceStatus:
    def test_returns_matched_instances(self):
        client = _ec2_client([ONDEMAND_INSTANCE])
        result = mcp_server._get_instance_status(client, "dev01", "us-east-1")
        assert result[0]["name"] == "dev01"

    def test_no_matches_raises_runtime_error(self):
        client = _ec2_client([])
        with pytest.raises(ToolError):
            mcp_server._get_instance_status(client, "dev01", "us-east-1")

    def test_tool_wrapper_resolves_region_and_constructs_client(self):
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="region: us-east-2\n")),
            patch("mcp_server.boto3.client") as mock_boto3_client,
        ):
            mock_boto3_client.return_value = _ec2_client([ONDEMAND_INSTANCE])
            result = mcp_server.get_instance_status("dev01", None)
        mock_boto3_client.assert_called_once_with("ec2", region_name="us-east-2")
        assert result[0]["name"] == "dev01"

    def test_tool_wrapper_missing_region_raises_runtime_error(self):
        with patch("os.path.exists", return_value=False), pytest.raises(ToolError):
            mcp_server.get_instance_status("dev01", None)


class TestGetBuildRecord:
    def test_returns_parsed_yaml_content(self):
        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="region: us-east-1\nbase_os: al2023\n")):
            result = mcp_server.get_build_record("dev01")
        assert result == {"region": "us-east-1", "base_os": "al2023"}

    def test_missing_vars_file_raises_runtime_error(self):
        with patch("os.path.exists", return_value=False), pytest.raises(ToolError, match="No build record"):
            mcp_server.get_build_record("dev01")

    def test_empty_vars_file_raises_runtime_error(self):
        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="")), pytest.raises(ToolError, match="empty"):
            mcp_server.get_build_record("dev01")


def _build_report(**overrides):
    defaults = {
        "instance_name": "dev01",
        "count": 1,
        "is_windows": False,
        "access_command": "./access_instance.py -N dev01",
        "windows_password_retrieval_command": None,
        "kill_script": "./kill-instance.dev01.sh",
        "build_ami_script": "./build-ami.dev01.sh",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev01",
    }
    defaults.update(overrides)
    return BuildReport(**defaults)


class TestBuildInstance:
    def test_confirm_false_raises_without_calling_run_build(self):
        run_build_mock = MagicMock()
        with patch("mcp_server.run_build", run_build_mock), pytest.raises(ToolError, match="confirm=True"):
            mcp_server.build_instance(az="us-east-2a", instance_name="dev01", instance_owner="tester", instance_owner_email="tester@example.com", confirm=False)
        run_build_mock.assert_not_called()

    def test_confirm_true_builds_argv_and_skips_ctrlc_window(self):
        run_build_mock = MagicMock(return_value=_build_report())
        with patch("mcp_server.run_build", run_build_mock):
            result = mcp_server.build_instance(az="us-east-2a", instance_name="dev01", instance_owner="tester", instance_owner_email="tester@example.com", confirm=True)

        (argv, quit_fn), kwargs = run_build_mock.call_args
        assert kwargs == {"ctrlc_abort_seconds": 0}
        assert quit_fn is mcp_server._mcp_quit
        assert argv[:8] == ["--az", "us-east-2a", "--instance_name", "dev01", "--instance_owner", "tester", "--instance_owner_email", "tester@example.com"]
        assert "--base_os" in argv and argv[argv.index("--base_os") + 1] == "al2023"
        assert result["instance_name"] == "dev01"
        assert result["access_command"] == "./access_instance.py -N dev01"

    def test_non_default_parameters_are_threaded_into_argv(self):
        run_build_mock = MagicMock(return_value=_build_report(instance_name="dev02", count=3))
        with patch("mcp_server.run_build", run_build_mock):
            mcp_server.build_instance(
                az="us-east-2a",
                instance_name="dev02",
                instance_owner="tester",
                instance_owner_email="tester@example.com",
                confirm=True,
                count=3,
                request_type="spot",
                base_os="ubuntu2404",
            )
        argv, _ = run_build_mock.call_args.args
        assert argv[argv.index("--count") + 1] == "3"
        assert argv[argv.index("--request_type") + 1] == "spot"
        assert argv[argv.index("--base_os") + 1] == "ubuntu2404"

    def test_confirm_false_message_reflects_blast_radius(self):
        with pytest.raises(ToolError, match=r"count=5.*instance_type=m5\.xlarge.*request_type=spot"):
            mcp_server.build_instance(
                az="us-east-2a",
                instance_name="dev01",
                instance_owner="tester",
                instance_owner_email="tester@example.com",
                confirm=False,
                count=5,
                instance_type="m5.xlarge",
                request_type="spot",
            )

    def test_invalid_instance_name_rejected_before_confirm_check(self):
        run_build_mock = MagicMock()
        with patch("mcp_server.run_build", run_build_mock), pytest.raises(ToolError, match="instance_name"):
            mcp_server.build_instance(az="us-east-2a", instance_name="../../etc/passwd", instance_owner="tester", instance_owner_email="tester@example.com", confirm=False)
        run_build_mock.assert_not_called()

    def test_argparse_system_exit_is_converted_to_tool_error_not_left_to_hang(self):
        run_build_mock = MagicMock(side_effect=SystemExit(2))
        with patch("mcp_server.run_build", run_build_mock), pytest.raises(ToolError, match="argparse exited with code 2"):
            mcp_server.build_instance(az="us-east-2a", instance_name="dev01", instance_owner="-rf", instance_owner_email="tester@example.com", confirm=True)


class TestDestroyInstance:
    def test_confirm_false_raises_without_calling_kill_script(self):
        terminate_mock = MagicMock()
        with patch("mcp_server.terminate_via_kill_script", terminate_mock), pytest.raises(ToolError, match="confirm=True"):
            mcp_server.destroy_instance("dev01", confirm=False)
        terminate_mock.assert_not_called()

    def test_confirm_true_delegates_to_kill_script_with_auto_confirm(self):
        terminate_mock = MagicMock(return_value=0)
        with patch("mcp_server.terminate_via_kill_script", terminate_mock):
            preview = mcp_server.destroy_instance("dev01", confirm=True)
            # Phase one must not have run the kill script.
            terminate_mock.assert_not_called()
            assert preview["status"] == "confirmation_required"
            result = mcp_server.destroy_instance("dev01", confirm=True, confirmation_token=preview["confirmation_token"])
        terminate_mock.assert_called_once_with("dev01", True, mcp_server._mcp_quit)
        assert result == {"instance_name": "dev01", "kill_script_returncode": 0}

    def test_path_traversal_instance_name_rejected_before_kill_script(self):
        terminate_mock = MagicMock()
        with patch("mcp_server.terminate_via_kill_script", terminate_mock), pytest.raises(ToolError):
            mcp_server.destroy_instance("../../../../tmp/evil", confirm=True)
        terminate_mock.assert_not_called()


class TestInstanceNameValidationAcrossTools:
    """Regression coverage for the path-traversal finding: get_build_record's
    os.path.join("vars_files", instance_name + ".yml") and
    manage_instance.resolve_region()'s "./vars_files/" + instance_name +
    ".yml" (used by get_instance_status/start_instance/stop_instance/
    reboot_instance whenever region is omitted) both happily escape
    vars_files/ given a "../"-laden instance_name -- proven by planting a
    file outside vars_files/ and reading it back through get_build_record
    before this fix existed. Every tool taking instance_name must reject
    it via validate_instance_name_format() before doing anything else.
    """

    PATH_TRAVERSAL_NAME = "../../../../tmp/evil"

    def test_get_build_record_rejects_path_traversal(self):
        with pytest.raises(ToolError, match="instance_name"):
            mcp_server.get_build_record(self.PATH_TRAVERSAL_NAME)

    def test_get_instance_status_rejects_path_traversal_before_any_aws_call(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client, pytest.raises(ToolError):
            mcp_server.get_instance_status(self.PATH_TRAVERSAL_NAME)
        mock_boto3_client.assert_not_called()

    def test_start_instance_rejects_path_traversal_before_any_aws_call(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client, pytest.raises(ToolError):
            mcp_server.start_instance(self.PATH_TRAVERSAL_NAME, confirm=True)
        mock_boto3_client.assert_not_called()

    def test_stop_instance_rejects_path_traversal_before_any_aws_call(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client, pytest.raises(ToolError):
            mcp_server.stop_instance(self.PATH_TRAVERSAL_NAME, confirm=True)
        mock_boto3_client.assert_not_called()

    def test_reboot_instance_rejects_path_traversal_before_any_aws_call(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client, pytest.raises(ToolError):
            mcp_server.reboot_instance(self.PATH_TRAVERSAL_NAME, confirm=True)
        mock_boto3_client.assert_not_called()

    def test_uppercase_instance_name_also_rejected(self):
        # validate_instance_name_format() enforces the same
        # [a-z][a-z0-9-]* rule the CLI does -- not just a traversal
        # blocklist, the same allowlist manage_instance.py main() applies.
        with pytest.raises(ToolError, match="lowercase"):
            mcp_server.get_build_record("Dev01")


class TestPowerStatePreview:
    """Phase one resolves and returns the affected instances without
    touching any of them. This did not exist before: the mutating path
    acted on whatever find_managed_instances() returned and only revealed
    which instances were hit in the return value, after the fact."""

    def test_preview_returns_matches_and_touches_nothing(self):
        client = _ec2_client([ONDEMAND_INSTANCE])

        result = mcp_server._power_state_preview(client, "dev01", "us-east-1", "start")

        assert result["status"] == "confirmation_required"
        assert result["affected_count"] == 1
        assert result["confirmation_token"]
        client.start_instances.assert_not_called()
        client.stop_instances.assert_not_called()
        client.reboot_instances.assert_not_called()

    def test_spot_conflict_is_caught_in_the_preview(self):
        client = _ec2_client([SPOT_INSTANCE])
        with pytest.raises(ToolError):
            mcp_server._power_state_preview(client, "dev01", "us-east-1", "start")
        client.start_instances.assert_not_called()

    def test_reboot_against_spot_instance_is_fine(self):
        client = _ec2_client([SPOT_INSTANCE])
        result = mcp_server._power_state_preview(client, "dev01", "us-east-1", "reboot")
        assert result["affected_count"] == 1

    def test_no_matching_instances_raises(self):
        client = _ec2_client([])
        with pytest.raises(ToolError):
            mcp_server._power_state_preview(client, "dev01", "us-east-1", "start")


class TestChangePowerState:
    """Phase two acts on exactly the instance IDs the preview returned,
    which also closes the TOCTOU window between preview and action."""

    def test_start_calls_start_instances(self):
        client = _ec2_client([ONDEMAND_INSTANCE])
        result = mcp_server._change_power_state(client, "dev01", "us-east-1", "start", ["i-ondemand01"])
        client.start_instances.assert_called_once_with(InstanceIds=["i-ondemand01"])
        assert result == {"instance_name": "dev01", "action": "start", "instance_ids": ["i-ondemand01"]}

    def test_stop_calls_stop_instances(self):
        client = _ec2_client([ONDEMAND_INSTANCE])
        mcp_server._change_power_state(client, "dev01", "us-east-1", "stop", ["i-ondemand01"])
        client.stop_instances.assert_called_once_with(InstanceIds=["i-ondemand01"])

    def test_reboot_calls_reboot_instances(self):
        client = _ec2_client([ONDEMAND_INSTANCE])
        mcp_server._change_power_state(client, "dev01", "us-east-1", "reboot", ["i-ondemand01"])
        client.reboot_instances.assert_called_once_with(InstanceIds=["i-ondemand01"])

    def test_acts_on_the_previewed_ids_not_a_fresh_lookup(self):
        # The client returns a *different* instance than the one previewed;
        # phase two must ignore it and act on what was confirmed.
        client = _ec2_client([ONDEMAND_INSTANCE])
        mcp_server._change_power_state(client, "dev01", "us-east-1", "stop", ["i-previewed99"])
        client.stop_instances.assert_called_once_with(InstanceIds=["i-previewed99"])


class TestPowerStateTools:
    def test_start_instance_confirm_false_raises_without_aws_call(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client, pytest.raises(ToolError, match="confirm=True"):
            mcp_server.start_instance("dev01", confirm=False)
        mock_boto3_client.assert_not_called()

    def test_stop_instance_confirm_false_raises_without_aws_call(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client, pytest.raises(ToolError, match="confirm=True"):
            mcp_server.stop_instance("dev01", confirm=False)
        mock_boto3_client.assert_not_called()

    def test_reboot_instance_confirm_false_raises_without_aws_call(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client, pytest.raises(ToolError, match="confirm=True"):
            mcp_server.reboot_instance("dev01", confirm=False)
        mock_boto3_client.assert_not_called()

    def test_start_instance_confirm_true_resolves_region_and_constructs_client(self):
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="region: us-east-2\n")),
            patch("mcp_server.boto3.client") as mock_boto3_client,
        ):
            mock_boto3_client.return_value = _ec2_client([ONDEMAND_INSTANCE])
            result = mcp_server.start_instance("dev01", confirm=True, region=None)
        mock_boto3_client.assert_called_once_with("ec2", region_name="us-east-2")
        assert result["action"] == "start"

    def test_stop_instance_confirm_true_uses_explicit_region(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client:
            mock_boto3_client.return_value = _ec2_client([ONDEMAND_INSTANCE])
            result = mcp_server.stop_instance("dev01", confirm=True, region="us-west-2")
        mock_boto3_client.assert_called_once_with("ec2", region_name="us-west-2")
        assert result["action"] == "stop"

    def test_reboot_instance_confirm_true_uses_explicit_region(self):
        with patch("mcp_server.boto3.client") as mock_boto3_client:
            mock_boto3_client.return_value = _ec2_client([ONDEMAND_INSTANCE])
            result = mcp_server.reboot_instance("dev01", confirm=True, region="us-west-2")
        mock_boto3_client.assert_called_once_with("ec2", region_name="us-west-2")
        assert result["action"] == "reboot"


def _mock_aws_clients():
    sns_client = MagicMock()
    sns_client.create_topic.return_value = {"TopicArn": "arn:aws:sns:us-east-2:123456789012:Ec2_Instance_SNS_Alerts_mcpdev01"}
    stsclient = MagicMock()
    stsclient.get_caller_identity.return_value = {"Account": "123456789012"}
    return AwsClients(ec2_client=MagicMock(), ec2=MagicMock(), iam=MagicMock(), sns_client=sns_client, stsclient=stsclient, logs_client=MagicMock())


def _patch_aws_and_terraform_boundary(monkeypatch):
    """Same boundary tests/test_make_instance_integration.py patches --
    every function make_instance.run_build() would otherwise call against
    real AWS or a real Terraform binary, patched on make_instance's own
    namespace (not mcp_server's) since that's where run_build() actually
    calls them from.
    """
    aws_clients = _mock_aws_clients()
    monkeypatch.setattr(make_instance, "ctrlC_Abort", MagicMock())
    monkeypatch.setattr(
        make_instance,
        "get_instance_type_info",
        MagicMock(
            return_value={
                "architecture": "x86_64",
                "ebs_optimized_support": "default",
                "ebs_encryption_support": "supported",
                "placement_group_strategies": ["cluster", "spread"],
            }
        ),
    )
    monkeypatch.setattr(make_instance, "create_aws_clients", MagicMock(return_value=aws_clients))
    monkeypatch.setattr(make_instance, "validate_az_and_region", MagicMock())
    monkeypatch.setattr(make_instance, "setup_cloudwatch_logging", MagicMock())
    monkeypatch.setattr(make_instance, "resolve_vpc_and_subnet", MagicMock(return_value=("vpc-0123456789abcdef0", "vpc_default", "subnet-0123456789abcdef0")))
    monkeypatch.setattr(make_instance, "resolve_ssh_allowed_ips", MagicMock(return_value="10.0.0.0/16"))
    monkeypatch.setattr(make_instance, "resolve_security_group", MagicMock(return_value=("ec2instancemaker_sg_mcpdev01-000000010926", "sg-0123456789abcdef0", "false")))
    monkeypatch.setattr(make_instance, "resolve_ami", MagicMock(return_value="ami-0123456789abcdef0"))
    monkeypatch.setattr(make_instance, "setup_keypair", MagicMock())
    monkeypatch.setattr(make_instance, "setup_iam", MagicMock(return_value=("Ec2InstanceMaker-role-mcpdev01", "Ec2InstanceMaker-policy-mcpdev01", "Ec2InstanceMaker-profile-mcpdev01", "false")))
    monkeypatch.setattr(make_instance, "get_terraform_version", MagicMock(return_value="v1.5.7"))
    apply_terraform_mock = MagicMock()
    monkeypatch.setattr(make_instance, "apply_terraform", apply_terraform_mock)
    return aws_clients, apply_terraform_mock


class TestBuildInstanceEndToEnd:
    def test_full_mocked_build_renders_real_templates_and_skips_ctrlc_window(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.symlink(os.path.join(REPO_ROOT, "templates"), tmp_path / "templates")
        os.symlink(os.path.join(REPO_ROOT, "custom_user_scripts"), tmp_path / "custom_user_scripts")
        aws_clients, apply_terraform_mock = _patch_aws_and_terraform_boundary(monkeypatch)
        ctrlc_abort_mock = make_instance.ctrlC_Abort

        result = mcp_server.build_instance(az="us-east-2a", instance_name="mcpdev01", instance_owner="tester", instance_owner_email="tester@example.com", confirm=True)

        apply_terraform_mock.assert_called_once()
        aws_clients.sns_client.publish.assert_called_once()
        ctrlc_abort_mock.assert_called_once()
        assert ctrlc_abort_mock.call_args.args[0] == 0  # ctrlc_abort_seconds=0 -- no interactive window for an MCP-driven build

        assert result["instance_name"] == "mcpdev01"
        assert result["access_command"] == "./access_instance.py -N mcpdev01"
        assert result["sns_topic_arn"] == "arn:aws:sns:us-east-2:123456789012:Ec2_Instance_SNS_Alerts_mcpdev01"
        assert (tmp_path / "vars_files" / "mcpdev01.yml").is_file()
        rendered_dir = tmp_path / "instance_data" / "mcpdev01"
        assert (rendered_dir / "mcpdev01.tf").is_file()


class TestMutatingToolsAreOffByDefault:
    """Whether a session can create or destroy AWS resources is a decision
    for the operator launching the server, not for the model talking to it.
    A process-launch flag is one of the few decisions in this design the
    model genuinely cannot reach or revise mid-conversation.
    """

    READ_ONLY = {"list_instances", "get_instance_status", "get_build_record"}
    MUTATING = {"build_instance", "start_instance", "stop_instance", "reboot_instance", "destroy_instance"}

    def _registered(self):
        return set(mcp_server.mcp._tool_manager._tools)

    def test_only_read_only_tools_are_registered_by_default(self, monkeypatch):
        monkeypatch.delenv("EC2INSTANCEMAKER_ALLOW_MUTATING", raising=False)
        monkeypatch.setattr(mcp_server.sys, "argv", ["mcp_server.py"])
        assert not mcp_server._mutating_tools_enabled()
        # The module under test was imported without the opt-in.
        assert self.READ_ONLY <= self._registered()
        assert not (self.MUTATING & self._registered())

    def test_env_var_enables_them(self, monkeypatch):
        monkeypatch.setattr(mcp_server.sys, "argv", ["mcp_server.py"])
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("EC2INSTANCEMAKER_ALLOW_MUTATING", value)
            assert mcp_server._mutating_tools_enabled(), value

    def test_argv_flag_enables_them(self, monkeypatch):
        monkeypatch.delenv("EC2INSTANCEMAKER_ALLOW_MUTATING", raising=False)
        monkeypatch.setattr(mcp_server.sys, "argv", ["mcp_server.py", "--allow-mutating"])
        assert mcp_server._mutating_tools_enabled()

    def test_unset_and_junk_values_do_not_enable_them(self, monkeypatch):
        monkeypatch.setattr(mcp_server.sys, "argv", ["mcp_server.py"])
        for value in ("", "false", "no", "off", "0", "banana"):
            monkeypatch.setenv("EC2INSTANCEMAKER_ALLOW_MUTATING", value)
            assert not mcp_server._mutating_tools_enabled(), value


class TestConfirmationTokens:
    """The token does not defeat prompt injection -- nothing inside this
    process can, since the decision and the injected text share a context.
    It forces the blast radius into the transcript before the destructive
    call exists, and makes the client's permission prompt fire again on a
    call that names concrete resources.
    """

    def setup_method(self):
        mcp_server._pending_confirmations.clear()

    def test_token_is_single_use(self):
        token = mcp_server._issue_confirmation_token("destroy:dev01")
        mcp_server._consume_confirmation_token(token, "destroy:dev01")
        with pytest.raises(ToolError):
            mcp_server._consume_confirmation_token(token, "destroy:dev01")

    def test_token_is_bound_to_its_action_and_target(self):
        token = mcp_server._issue_confirmation_token("stop:web")
        # A token issued for stopping "web" must not authorize destroying it,
        # nor stopping something else.
        with pytest.raises(ToolError):
            mcp_server._consume_confirmation_token(token, "destroy:web")
        with pytest.raises(ToolError):
            mcp_server._consume_confirmation_token(token, "stop:other")

    def test_unknown_token_is_rejected(self):
        with pytest.raises(ToolError):
            mcp_server._consume_confirmation_token("not-a-real-token", "destroy:dev01")

    def test_expired_token_is_rejected(self, monkeypatch):
        token = mcp_server._issue_confirmation_token("destroy:dev01")
        real_monotonic = mcp_server.time.monotonic
        monkeypatch.setattr(mcp_server.time, "monotonic", lambda: real_monotonic() + mcp_server._CONFIRMATION_TTL_SECONDS + 1)
        with pytest.raises(ToolError):
            mcp_server._consume_confirmation_token(token, "destroy:dev01")

    def test_destroy_phase_one_previews_without_destroying(self):
        terminate_mock = MagicMock(return_value=0)
        with patch("mcp_server.terminate_via_kill_script", terminate_mock):
            result = mcp_server.destroy_instance("dev01", confirm=True)
        terminate_mock.assert_not_called()
        assert result["irreversible"] is True
        assert result["will_delete"]

    def test_destroy_with_a_stale_token_does_not_run_the_kill_script(self):
        terminate_mock = MagicMock(return_value=0)
        with patch("mcp_server.terminate_via_kill_script", terminate_mock):
            preview = mcp_server.destroy_instance("dev01", confirm=True)
            mcp_server.destroy_instance("dev01", confirm=True, confirmation_token=preview["confirmation_token"])
            terminate_mock.reset_mock()
            with pytest.raises(ToolError):
                mcp_server.destroy_instance("dev01", confirm=True, confirmation_token=preview["confirmation_token"])
        terminate_mock.assert_not_called()

    def test_confirm_false_still_short_circuits_before_any_token_is_issued(self):
        with pytest.raises(ToolError):
            mcp_server.destroy_instance("dev01", confirm=False)
        assert not mcp_server._pending_confirmations
