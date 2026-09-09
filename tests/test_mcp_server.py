"""Unit tests for mcp_server.py's read-only MCP tools. The AWS-backed tools
(list_instances, get_instance_status) are tested through their underlying
_list_instances/_get_instance_status functions with a mocked EC2Client --
same dependency-injection pattern as tests/test_manage_instance.py -- plus
one test per public @mcp.tool() wrapper confirming it actually constructs a
real boto3 client and delegates. get_build_record makes no AWS call, so
it's tested directly.
"""

from unittest.mock import mock_open, patch

import pytest
from botocore.exceptions import ClientError

import mcp_server


def _client_error(code, message="error"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "SomeOperation")


def _ec2_client(instances, pages=None):
    from unittest.mock import MagicMock

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
        with pytest.raises(RuntimeError, match="boom"):
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
        with pytest.raises(RuntimeError):
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
        with pytest.raises(RuntimeError):
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
        with patch("os.path.exists", return_value=False), pytest.raises(RuntimeError):
            mcp_server.get_instance_status("dev01", None)


class TestGetBuildRecord:
    def test_returns_parsed_yaml_content(self):
        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="region: us-east-1\nbase_os: al2023\n")):
            result = mcp_server.get_build_record("dev01")
        assert result == {"region": "us-east-1", "base_os": "al2023"}

    def test_missing_vars_file_raises_runtime_error(self):
        with patch("os.path.exists", return_value=False), pytest.raises(RuntimeError, match="No build record"):
            mcp_server.get_build_record("dev01")

    def test_empty_vars_file_raises_runtime_error(self):
        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="")), pytest.raises(RuntimeError, match="empty"):
            mcp_server.get_build_record("dev01")
