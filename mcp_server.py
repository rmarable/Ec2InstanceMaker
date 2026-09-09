#!/usr/bin/env python3
#
################################################################################
# Name:         mcp_server.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:	Expose Ec2InstanceMaker as MCP tools: instance listing/status/
# 		build-record lookups (read-only), plus build_instance/
# 		destroy_instance/start_instance/stop_instance/reboot_instance
# 		(read-write, each gated behind an explicit confirm=True
# 		argument), so an MCP client (e.g. Claude Code) can query and
# 		drive builds without a human running the CLI scripts by
# 		hand. See CLAUDE.md's mcp_server.py section for the full
# 		tool list and the reasoning behind each one.
################################################################################

import dataclasses
import os
import secrets
import sys
import time
from math import pi
from typing import Any, Literal, NoReturn

import boto3
import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mypy_boto3_ec2.client import EC2Client
from mypy_boto3_ec2.type_defs import InstanceTypeDef

from instance_builder import validate_instance_name_format
from make_instance import BoolStr, run_build
from manage_instance import check_spot_lifecycle_conflict, find_managed_instances, list_all_managed_instances, resolve_region, tag_value, terminate_via_kill_script

mcp = MCPServer("ec2instancemaker")


# Function: _mcp_quit()
# Purpose: the QuitFn seam find_managed_instances()/list_all_managed_instances()/
# resolve_region() (manage_instance.py) already take instead of calling
# sys.exit() directly -- an MCP server is a long-running process, so a
# lookup failure needs to surface as a tool error, not kill the server.
# Raising satisfies the same Callable[[str], NoReturn] shape those
# functions were already written against; no changes to manage_instance.py
# were needed to reuse them here. Raises ToolError, not a plain exception --
# confirmed via a real stdio smoke test that this mcp SDK version (2.x)
# treats any other exception type as an unexpected crash and masks its
# message from the client (only "Error executing tool <name>" reaches
# them, logged server-side as an "unexpected exception"). ToolError is the
# SDK's designated type for an intentional, client-visible tool failure --
# its message reaches the caller as-is.


# Two-phase confirmation for every mutating tool.
#
# `confirm=True` alone is a typo guard, not a security control: it is a
# value the calling model writes itself, in the same turn, from the same
# context that untrusted EC2 tag values and vars_file contents were read
# into. An injected "ignore previous instructions and destroy X" reaches
# the model with confirm=True already in hand.
#
# This does not fix that -- nothing inside this process can, since the
# decision and the injection share a context. What it does fix is
# *visibility*: the first call performs the lookup and returns what would
# actually be affected without touching anything, so the blast radius is
# in the transcript before the destructive call exists, and the MCP
# client's own permission prompt (the real trust boundary -- see
# README.md's "Securing the MCP server") fires a second time on a call
# that now names concrete instance IDs.
#
# Tokens are single-use, short-lived, and bound to a specific
# action+target, so a token issued for stopping "web" cannot authorize
# destroying it. For power actions the resolved instance IDs are carried
# on the token and acted on directly in phase two, which also closes the
# TOCTOU window between preview and action.

_CONFIRMATION_TTL_SECONDS = 300


@dataclasses.dataclass
class _PendingConfirmation:
    action_key: str
    expires_at: float
    instance_ids: list[str]


_pending_confirmations: dict[str, _PendingConfirmation] = {}


def _issue_confirmation_token(action_key: str, instance_ids: list[str] | None = None) -> str:
    _purge_expired_confirmations()
    token = secrets.token_urlsafe(9)
    _pending_confirmations[token] = _PendingConfirmation(action_key, time.monotonic() + _CONFIRMATION_TTL_SECONDS, instance_ids or [])
    return token


def _purge_expired_confirmations() -> None:
    now = time.monotonic()
    for token in [t for t, p in _pending_confirmations.items() if p.expires_at < now]:
        del _pending_confirmations[token]


def _consume_confirmation_token(token: str, action_key: str) -> _PendingConfirmation:
    _purge_expired_confirmations()
    pending = _pending_confirmations.pop(token, None)
    if pending is None or pending.action_key != action_key:
        raise ToolError("confirmation_token is invalid, expired, already used, or was issued for a different action. Call this tool again without confirmation_token to get a fresh preview and token.")
    return pending


def _mcp_quit(error_msg: str) -> NoReturn:
    raise ToolError(error_msg)


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
    validate_instance_name_format(instance_name, _mcp_quit)
    resolved_region = resolve_region(instance_name, region, _mcp_quit)
    return _get_instance_status(boto3.client("ec2", region_name=resolved_region), instance_name, resolved_region)


@mcp.tool()
def get_build_record(instance_name: str) -> dict[str, Any]:
    """Return the local build-time audit record
    (./vars_files/<instance_name>.yml) that make_instance.py wrote -- the
    parameters an instance/family was actually built with. Local file
    read only; makes no AWS API call."""
    validate_instance_name_format(instance_name, _mcp_quit)
    vars_file_path = os.path.join("vars_files", instance_name + ".yml")
    if not os.path.exists(vars_file_path):
        raise ToolError('No build record found at "' + vars_file_path + '".')
    with open(vars_file_path) as fh:
        content = yaml.safe_load(fh)
    if not content:
        raise ToolError('Build record at "' + vars_file_path + '" is empty or unreadable.')
    return dict(content)


# Type aliases mirroring make_instance.py's parse_args() choices -- gives
# MCP clients a real enum-constrained JSON schema for these parameters
# instead of an unconstrained string, even though the dataclasses
# run_build() threads them through (EbsRequest, BuildOptions, etc.) mostly
# just declare str/BoolStr. A Literal value is a valid BoolStr/str, so this
# is a pure schema improvement at the tool boundary, not a behavior change.
_BaseOs = Literal["al2023", "alinux2", "alma9", "alma10", "rhel9", "rhel10", "rocky9", "rocky10", "ubuntu2404", "ubuntu2604", "windows2019", "windows2022", "windows2025"]
_EbsVolumeType = Literal["gp2", "io1", "st1"]
_IamJsonPolicy = Literal["MinimalEc2InstancePolicy.json", "GenericEc2InstancePolicy.json", "ExtendedEc2InstancePolicy.json"]
_RequestType = Literal["ondemand", "spot"]
_ProdLevel = Literal["dev", "test", "stage", "prod"]
_PlacementGroupStrategy = Literal["cluster", "spread"]


def build_instance(
    az: str,
    instance_name: str,
    instance_owner: str,
    instance_owner_email: str,
    confirm: bool,
    base_os: _BaseOs = "al2023",
    count: int = 1,
    custom_ami: str = "UNDEFINED",
    custom_user_scripts: str = "default",
    debug_mode: BoolStr = "false",
    ebs_encryption: BoolStr = "false",
    ebs_optimized: BoolStr = "true",
    ebs_root_volume_iops: int = 0,
    ebs_root_volume_size: int = 8,
    ebs_root_volume_type: _EbsVolumeType = "gp2",
    ebs_device_volume_iops: int = 0,
    ebs_device_volume_size: int = 8,
    ebs_device_volume_type: _EbsVolumeType = "gp2",
    ec2_keypair: str = "ec2_keypair_default",
    enable_placement_group: BoolStr = "false",
    hyperthreading: BoolStr = "true",
    iam_json_policy: _IamJsonPolicy = "GenericEc2InstancePolicy.json",
    iam_name_prefix: str = "Ec2InstanceMaker",
    iam_role: str = "UNDEFINED",
    instance_owner_department: str = "compbio",
    request_type: _RequestType = "ondemand",
    instance_type: str = "t2.micro",
    prod_level: _ProdLevel = "dev",
    enable_cloudwatch_logs: BoolStr = "true",
    log_retention_days: int = 30,
    placement_group_strategy: _PlacementGroupStrategy = "cluster",
    preserve_ami: BoolStr = "true",
    preserve_cloudwatch_logs: BoolStr = "false",
    project_id: str = "UNDEFINED",
    public_ip: BoolStr = "true",
    security_group: str = "ec2instancemaker_sg",
    spot_buffer: float = round(1 / pi, 8),
    ssh_allowed_ips: str = "UNDEFINED",
    turbot_account: str = "DISABLED",
    vpc_name: str = "vpc_default",
) -> dict[str, Any]:
    """Build a new Ec2InstanceMaker EC2 instance or family -- the MCP
    equivalent of running make_instance.py. Creates real, billable AWS
    resources (EC2 instance(s), security group, IAM role/policy/profile,
    SNS topic, and, if enabled, a CloudWatch log group) and can take
    several minutes (Terraform apply, then SSM provisioning). Requires
    confirm=True: unlike the CLI, there is no interactive CTRL-C abort
    window here, and confirming does not by itself limit how many
    instances count builds -- check count against the actual budget
    before setting confirm=True.

    "UNDEFINED"/"DISABLED" sentinel defaults mean "not set", not literal
    values to pass through: custom_ami, iam_role, project_id,
    ssh_allowed_ips, turbot_account. ssh_allowed_ips="UNDEFINED" resolves
    to the instance's own VPC CIDR; 0.0.0.0/0 is always rejected.
    spot_buffer only applies when request_type="spot": a fractional
    markup applied to the current spot price to reduce interruption risk
    (spot_price = spot_price + spot_price*spot_buffer). turbot_account
    enables Turbot governance tagging when set to a real account ID.
    security_group/iam_name_prefix/iam_json_policy control the generated
    security group and IAM role, not a pre-existing one to attach --
    pass a real iam_role to reuse an existing IAM role instead.

    See make_instance.py --help / README.md for the remaining
    parameters."""
    validate_instance_name_format(instance_name, _mcp_quit)
    if not confirm:
        raise ToolError(
            'Set confirm=True to actually build "'
            + instance_name
            + '" (count='
            + str(count)
            + ", instance_type="
            + instance_type
            + ", request_type="
            + request_type
            + ") -- this creates real, billable AWS resources."
        )

    argv = [
        "--az",
        az,
        "--instance_name",
        instance_name,
        "--instance_owner",
        instance_owner,
        "--instance_owner_email",
        instance_owner_email,
        "--base_os",
        base_os,
        "--count",
        str(count),
        "--custom_ami",
        custom_ami,
        "--custom_user_scripts",
        custom_user_scripts,
        "--debug_mode",
        debug_mode,
        "--ebs_encryption",
        ebs_encryption,
        "--ebs_optimized",
        ebs_optimized,
        "--ebs_root_volume_iops",
        str(ebs_root_volume_iops),
        "--ebs_root_volume_size",
        str(ebs_root_volume_size),
        "--ebs_root_volume_type",
        ebs_root_volume_type,
        "--ebs_device_volume_iops",
        str(ebs_device_volume_iops),
        "--ebs_device_volume_size",
        str(ebs_device_volume_size),
        "--ebs_device_volume_type",
        ebs_device_volume_type,
        "--ec2_keypair",
        ec2_keypair,
        "--enable_placement_group",
        enable_placement_group,
        "--hyperthreading",
        hyperthreading,
        "--iam_json_policy",
        iam_json_policy,
        "--iam_name_prefix",
        iam_name_prefix,
        "--iam_role",
        iam_role,
        "--instance_owner_department",
        instance_owner_department,
        "--request_type",
        request_type,
        "--instance_type",
        instance_type,
        "--prod_level",
        prod_level,
        "--enable_cloudwatch_logs",
        enable_cloudwatch_logs,
        "--log_retention_days",
        str(log_retention_days),
        "--placement_group_strategy",
        placement_group_strategy,
        "--preserve_ami",
        preserve_ami,
        "--preserve_cloudwatch_logs",
        preserve_cloudwatch_logs,
        "--project_id",
        project_id,
        "--public_ip",
        public_ip,
        "--security_group",
        security_group,
        "--spot_buffer",
        str(spot_buffer),
        "--ssh_allowed_ips",
        ssh_allowed_ips,
        "--turbot_account",
        turbot_account,
        "--vpc_name",
        vpc_name,
    ]
    # run_build() ultimately calls argparse.parse_args() on the argv built
    # above -- any value argparse itself rejects (e.g. one that starts
    # with "-" and looks like a flag, or an invalid choice bypassing this
    # tool's own Literal-typed schema) makes argparse call sys.exit(),
    # raising SystemExit. Confirmed via a real stdio smoke test: SystemExit
    # is a BaseException, not an Exception, so it slips past every "except
    # Exception" guard in this mcp SDK's call stack instead of becoming a
    # clean ToolError -- the tool call just hangs, never returning a
    # response to the client. Caught and converted here instead.
    try:
        report = run_build(argv, _mcp_quit, ctrlc_abort_seconds=0)
    except SystemExit as e:
        raise ToolError("Invalid build_instance parameters (argparse exited with code " + str(e.code) + ') -- check for a value that starts with "-" or an invalid choice.') from e
    return dataclasses.asdict(report)


# Function: _change_power_state()
# Purpose: the actual, unit-testable logic behind start_instance/
# stop_instance/reboot_instance -- kept separate from the @mcp.tool()-
# decorated functions for the same reason _list_instances()/
# _get_instance_status() are. check_spot_lifecycle_conflict() (already
# tested, already takes refer_to_docs_and_quit as a parameter -- zero
# manage_instance.py changes needed) blocks start/stop against one-time
# Spot Instances before any EC2 API call happens. Direct if/elif per
# action, not a dispatch dict, matching manage_instance.py main()'s own
# reasoning: start_instances()/stop_instances()/reboot_instances() have
# different boto3-stubs keyword-argument shapes mypy can't unify under one
# Callable type.

_PowerAction = Literal["start", "stop", "reboot"]


def _power_state_preview(ec2_client: EC2Client, instance_name: str, region: str, action: _PowerAction) -> dict[str, Any]:
    """Phase one: resolve and return exactly which instances would be hit,
    without touching any of them.

    This previously did not exist, and the mutating path acted on whatever
    find_managed_instances() returned without ever surfacing it -- so a
    stop aimed at one instance could take out a whole family and the
    caller only learned which instances were affected from the return
    value, after the fact."""
    instances = find_managed_instances(ec2_client, instance_name, region, _mcp_quit)
    check_spot_lifecycle_conflict(instances, action, _mcp_quit)
    instance_ids = [str(instance["InstanceId"]) for instance in instances]
    return {
        "status": "confirmation_required",
        "action": action,
        "instance_name": instance_name,
        "region": region,
        "affected_instances": [_instance_summary(instance) for instance in instances],
        "affected_count": len(instance_ids),
        "confirmation_token": _issue_confirmation_token(action + ":" + instance_name, instance_ids),
        "next_step": (
            "Review affected_instances above. To proceed, call this tool again with the same arguments plus "
            "confirmation_token set to the value above. The token is single-use and expires in " + str(_CONFIRMATION_TTL_SECONDS) + " seconds."
        ),
    }


def _change_power_state(ec2_client: EC2Client, instance_name: str, region: str, action: _PowerAction, instance_ids: list[str]) -> dict[str, Any]:
    if action == "start":
        ec2_client.start_instances(InstanceIds=instance_ids)
    elif action == "stop":
        ec2_client.stop_instances(InstanceIds=instance_ids)
    else:
        ec2_client.reboot_instances(InstanceIds=instance_ids)
    return {"instance_name": instance_name, "action": action, "instance_ids": instance_ids}


def start_instance(instance_name: str, confirm: bool, region: str | None = None, confirmation_token: str | None = None) -> dict[str, Any]:
    """Start a previously stopped Ec2InstanceMaker-managed instance or
    family. Blocked against one-time Spot Instances -- AWS does not allow
    restarting a stopped Spot Instance; use destroy_instance and
    build_instance instead. Requires confirm=True."""
    validate_instance_name_format(instance_name, _mcp_quit)
    if not confirm:
        raise ToolError('Set confirm=True to actually start "' + instance_name + '".')
    resolved_region = resolve_region(instance_name, region, _mcp_quit)
    ec2_client = boto3.client("ec2", region_name=resolved_region)
    if confirmation_token is None:
        return _power_state_preview(ec2_client, instance_name, resolved_region, "start")
    pending = _consume_confirmation_token(confirmation_token, "start:" + instance_name)
    return _change_power_state(ec2_client, instance_name, resolved_region, "start", pending.instance_ids)


def stop_instance(instance_name: str, confirm: bool, region: str | None = None, confirmation_token: str | None = None) -> dict[str, Any]:
    """Stop a running Ec2InstanceMaker-managed instance or family. Blocked
    against one-time Spot Instances -- AWS does not allow restarting a
    stopped Spot Instance; use destroy_instance instead. Requires
    confirm=True."""
    validate_instance_name_format(instance_name, _mcp_quit)
    if not confirm:
        raise ToolError('Set confirm=True to actually stop "' + instance_name + '".')
    resolved_region = resolve_region(instance_name, region, _mcp_quit)
    ec2_client = boto3.client("ec2", region_name=resolved_region)
    if confirmation_token is None:
        return _power_state_preview(ec2_client, instance_name, resolved_region, "stop")
    pending = _consume_confirmation_token(confirmation_token, "stop:" + instance_name)
    return _change_power_state(ec2_client, instance_name, resolved_region, "stop", pending.instance_ids)


def reboot_instance(instance_name: str, confirm: bool, region: str | None = None, confirmation_token: str | None = None) -> dict[str, Any]:
    """Reboot an Ec2InstanceMaker-managed instance or family. Safe for
    Spot Instances, unlike start/stop. Requires confirm=True."""
    validate_instance_name_format(instance_name, _mcp_quit)
    if not confirm:
        raise ToolError('Set confirm=True to actually reboot "' + instance_name + '".')
    resolved_region = resolve_region(instance_name, region, _mcp_quit)
    ec2_client = boto3.client("ec2", region_name=resolved_region)
    if confirmation_token is None:
        return _power_state_preview(ec2_client, instance_name, resolved_region, "reboot")
    pending = _consume_confirmation_token(confirmation_token, "reboot:" + instance_name)
    return _change_power_state(ec2_client, instance_name, resolved_region, "reboot", pending.instance_ids)


def destroy_instance(instance_name: str, confirm: bool, confirmation_token: str | None = None) -> dict[str, Any]:
    """Tear down an Ec2InstanceMaker-built instance or family: the EC2
    instance(s), security group, IAM role/policy/profile, SNS topic, and
    local state -- delegates to ./kill-instance.<instance_name>.sh, the
    same script manage_instance.py -A terminate uses. Only works from the
    repo checkout where the instance was built. Irreversible; requires
    confirm=True."""
    validate_instance_name_format(instance_name, _mcp_quit)
    if not confirm:
        raise ToolError('Set confirm=True to actually destroy "' + instance_name + '" -- this permanently deletes real AWS resources and cannot be undone.')
    if confirmation_token is None:
        return {
            "status": "confirmation_required",
            "action": "destroy",
            "instance_name": instance_name,
            "irreversible": True,
            "will_delete": [
                "the EC2 instance(s) and their EBS volumes",
                "the EC2 keypair and its local .pem file",
                "the security group, unless this build reused a pre-existing one",
                "the IAM role, policy and instance profile, unless --preserve_iam_role was set",
                "the SNS topic and its email subscription",
                "the CloudWatch Logs group, unless --preserve_cloudwatch_logs was set",
                "the local Terraform state, vars_file and generated scripts",
            ],
            "kill_script": "./kill-instance." + instance_name + ".sh",
            "confirmation_token": _issue_confirmation_token("destroy:" + instance_name),
            "next_step": (
                "This cannot be undone. To proceed, call destroy_instance again with the same instance_name plus "
                "confirmation_token set to the value above. The token is single-use and expires in " + str(_CONFIRMATION_TTL_SECONDS) + " seconds."
            ),
        }
    _consume_confirmation_token(confirmation_token, "destroy:" + instance_name)
    returncode = terminate_via_kill_script(instance_name, True, _mcp_quit)
    return {"instance_name": instance_name, "kill_script_returncode": returncode}


# Mutating tools are OFF by default.
#
# build_instance and destroy_instance create and destroy real, billable AWS
# resources; start/stop/reboot change the state of running ones. Whether a
# given session should be able to do any of that is a decision that belongs
# to the operator launching the server, not to the model talking to it --
# and unlike the `confirm` argument, a process-launch flag is a decision the
# model cannot reach or revise mid-conversation.
#
# So the default install exposes only the three read-only tools
# (list_instances, get_instance_status, get_build_record), which is all most
# sessions need. Opt in with either:
#
#   .venv/bin/python3 mcp_server.py --allow-mutating
#   EC2INSTANCEMAKER_ALLOW_MUTATING=true .venv/bin/python3 mcp_server.py
#
# The env var exists because a Claude Desktop "Local command" connector sets
# env more naturally than argv; .mcp.json can use either.
#
# This is a real reduction in blast radius, not theatre -- but be clear about
# what it is not: an operator who enables mutating tools is back to relying
# on the MCP client's own permission prompt, which is the actual trust
# boundary. See README.md's "Securing the MCP server".


def _mutating_tools_enabled() -> bool:
    if os.environ.get("EC2INSTANCEMAKER_ALLOW_MUTATING", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return "--allow-mutating" in sys.argv


def _register_mutating_tools() -> None:
    for tool in (build_instance, start_instance, stop_instance, reboot_instance, destroy_instance):
        mcp.tool()(tool)


if _mutating_tools_enabled():
    _register_mutating_tools()


if __name__ == "__main__":
    # Claude Code's .mcp.json runs this with the repo root as cwd already,
    # so this chdir is a no-op there. Claude Desktop's "Local command"
    # connector has no cwd concept -- it just runs command+args -- so
    # without this, every relative path here (vars_files/<name>.yml, etc.)
    # would resolve against whatever cwd the connector happens to launch
    # with instead of the repo root.
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    mcp.run()
