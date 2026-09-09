#!/usr/bin/env python3
#
################################################################################
# Name:         mcp_server.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:	Expose read-only Ec2InstanceMaker lookups (instance listing,
# 		status, and the local build-time record) as MCP tools, so an
# 		MCP client (e.g. Claude Code) can query what's built and
# 		what's running without a human running manage_instance.py
# 		by hand. Deliberately excludes anything that creates,
# 		modifies, or destroys AWS resources -- see CLAUDE.md for the
# 		read-only-first scope decision and why build/destroy tools
# 		are a separate, not-yet-built follow-up.
################################################################################

import os
from typing import Any, NoReturn

import boto3
import yaml
from mcp.server.mcpserver import MCPServer
from mypy_boto3_ec2.client import EC2Client
from mypy_boto3_ec2.type_defs import InstanceTypeDef

from manage_instance import find_managed_instances, list_all_managed_instances, resolve_region, tag_value

mcp = MCPServer("ec2instancemaker")


# Function: _mcp_quit()
# Purpose: the QuitFn seam find_managed_instances()/list_all_managed_instances()/
# resolve_region() (manage_instance.py) already take instead of calling
# sys.exit() directly -- an MCP server is a long-running process, so a
# lookup failure needs to surface as a tool error, not kill the server.
# Raising satisfies the same Callable[[str], NoReturn] shape those
# functions were already written against; no changes to manage_instance.py
# were needed to reuse them here.


def _mcp_quit(error_msg: str) -> NoReturn:
    raise RuntimeError(error_msg)


# Function: _instance_summary()
# Purpose: shared shape for one instance's tool-facing JSON, used by both
# list_instances and get_instance_status.


def _instance_summary(instance: InstanceTypeDef) -> dict[str, Any]:
    return {
        "instance_id": instance["InstanceId"],
        "name": tag_value(instance, "Name"),
        "state": instance["State"]["Name"],
        "instance_type": instance["InstanceType"],
        "base_os": tag_value(instance, "OperatingSystem"),
        "instance_owner": tag_value(instance, "InstanceOwner"),
        "is_spot": instance.get("InstanceLifecycle") == "spot",
    }


# Function: _list_instances()/_get_instance_status()
# Purpose: the actual, unit-testable logic behind the two AWS-backed tools
# below -- kept separate from the @mcp.tool()-decorated functions so tests
# can inject a mocked EC2Client directly (same dependency-injection pattern
# as tests/test_manage_instance.py) without the public tool signature
# exposing an ec2_client parameter no MCP client should ever pass.


def _list_instances(ec2_client: EC2Client, region: str) -> list[dict[str, Any]]:
    instances = list_all_managed_instances(ec2_client, region, _mcp_quit)
    return [_instance_summary(instance) for instance in instances]


def _get_instance_status(ec2_client: EC2Client, instance_name: str, region: str) -> list[dict[str, Any]]:
    instances = find_managed_instances(ec2_client, instance_name, region, _mcp_quit)
    return [_instance_summary(instance) for instance in instances]


@mcp.tool()
def list_instances(region: str) -> list[dict[str, Any]]:
    """List every Ec2InstanceMaker-managed EC2 instance in an AWS region."""
    return _list_instances(boto3.client("ec2", region_name=region), region)


@mcp.tool()
def get_instance_status(instance_name: str, region: str | None = None) -> list[dict[str, Any]]:
    """Report the status of one Ec2InstanceMaker-managed instance or family.
    `region` falls back to the region recorded in
    ./vars_files/<instance_name>.yml at build time if omitted."""
    resolved_region = resolve_region(instance_name, region, _mcp_quit)
    return _get_instance_status(boto3.client("ec2", region_name=resolved_region), instance_name, resolved_region)


@mcp.tool()
def get_build_record(instance_name: str) -> dict[str, Any]:
    """Return the local build-time audit record
    (./vars_files/<instance_name>.yml) that make_instance.py wrote -- the
    parameters an instance/family was actually built with. Local file
    read only; makes no AWS API call."""
    vars_file_path = os.path.join("vars_files", instance_name + ".yml")
    if not os.path.exists(vars_file_path):
        raise RuntimeError('No build record found at "' + vars_file_path + '".')
    with open(vars_file_path) as fh:
        content = yaml.safe_load(fh)
    if not content:
        raise RuntimeError('Build record at "' + vars_file_path + '" is empty or unreadable.')
    return dict(content)


if __name__ == "__main__":
    mcp.run()
