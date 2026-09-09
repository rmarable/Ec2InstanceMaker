#!/usr/bin/env python3
#
################################################################################
# Name:         manage_instance.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:	Start, stop, reboot, terminate, check the status of, or list
# 		EC2 instance(s) built by Ec2InstanceMaker, identified safely
# 		via the ManagedBy tag so this can never act on an instance
# 		this toolkit didn't create.
################################################################################

# Load some required Python libraries

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Literal, NoReturn

import boto3
import yaml
from botocore.exceptions import ClientError, EndpointConnectionError
from mypy_boto3_ec2.client import EC2Client
from mypy_boto3_ec2.type_defs import FilterTypeDef, InstanceTypeDef
from prettytable import PrettyTable

# Import some external lists and functions.
# Source: aux_data.py
from aux_data import refer_to_docs_and_quit
from instance_builder import instance_lock, validate_instance_name_format

# Type aliases used throughout this module's signatures -- same duplicated
# convention as instance_builder.py/aux_data.py (see the comment there for
# why they're not shared via import).
QuitFn = Callable[[str], NoReturn]
Action = Literal["start", "stop", "reboot", "terminate", "status", "list-all"]

MANAGED_BY_TAG_VALUE = "Ec2InstanceMaker"


# Function: tag_value()
# Purpose: pull one tag's value off an EC2 instance dict, or a default if
# it isn't present.


def tag_value(instance: InstanceTypeDef, key: str, default: str = "?") -> str:
    return next((tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == key), default)


# Function: resolve_region()
# Purpose: use --region if given, otherwise fall back to the region
# recorded in ./vars_files/<instance_name>.yml at build time.


def resolve_region(instance_name: str, region: str | None, refer_to_docs_and_quit: QuitFn) -> str:
    if region is not None:
        return region
    vars_file_path = "./vars_files/" + instance_name + ".yml"
    if os.path.exists(vars_file_path):
        with open(vars_file_path) as fh:
            vars_file_content = yaml.safe_load(fh)
        if vars_file_content:
            region = vars_file_content.get("region")
    if region is None:
        refer_to_docs_and_quit("--region was not given and could not be read from " + vars_file_path + "! Pass --region explicitly.")
    return region


# Function: find_managed_instances()
# Purpose: look up instance(s) via the ManagedBy tag -- matches both a
# single instance (Name == instance_name) and a family
# (Name == instance_name-<index>) in one filter, and requires the
# ManagedBy tag so this can never touch an instance this toolkit didn't
# create, even if its Name happens to collide with something else.


def find_managed_instances(ec2_client: EC2Client, instance_name: str, region: str, refer_to_docs_and_quit: QuitFn) -> list[InstanceTypeDef]:
    filters: list[FilterTypeDef] = [
        {"Name": "tag:Name", "Values": [instance_name, instance_name + "-*"]},
        {"Name": "tag:ManagedBy", "Values": [MANAGED_BY_TAG_VALUE]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "shutting-down", "stopping", "stopped"]},
    ]
    try:
        pages = ec2_client.get_paginator("describe_instances").paginate(Filters=filters)
        instances = [instance for page in pages for reservation in page["Reservations"] for instance in reservation["Instances"]]
    except (ClientError, EndpointConnectionError) as e:
        refer_to_docs_and_quit("AWS API error while looking up " + instance_name + " in " + region + ": " + str(e))

    if not instances:
        refer_to_docs_and_quit('No Ec2InstanceMaker-managed instance(s) named "' + instance_name + '" were found in ' + region + "!")
    return instances


# Function: list_all_managed_instances()
# Purpose: look up every Ec2InstanceMaker-managed instance in a region --
# same ManagedBy-tag safety filter as find_managed_instances(), but with
# no Name filter at all, since this is a global listing, not one build.
# An empty result is a normal outcome here (there just aren't any
# instances right now), not a failure -- unlike find_managed_instances(),
# this never quits, it returns an empty list.


def list_all_managed_instances(ec2_client: EC2Client, region: str, refer_to_docs_and_quit: QuitFn) -> list[InstanceTypeDef]:
    filters: list[FilterTypeDef] = [
        {"Name": "tag:ManagedBy", "Values": [MANAGED_BY_TAG_VALUE]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "shutting-down", "stopping", "stopped"]},
    ]
    try:
        pages = ec2_client.get_paginator("describe_instances").paginate(Filters=filters)
        return [instance for page in pages for reservation in page["Reservations"] for instance in reservation["Instances"]]
    except (ClientError, EndpointConnectionError) as e:
        refer_to_docs_and_quit("AWS API error while listing Ec2InstanceMaker-managed instances in " + region + ": " + str(e))


# Function: check_spot_lifecycle_conflict()
# Purpose: this toolkit always requests one-time Spot Instances
# (spot_type = "one-time" in DEFAULT_EC2_TEMPLATE.j2), which AWS does not
# allow to be stopped and later restarted -- reboot is fine, but start/stop
# against one either fails outright or behaves unexpectedly depending on
# instance state, with an AWS error message that doesn't explain why.
# Catch it here instead, before ever calling the EC2 API, with a message
# that actually explains the constraint.


def check_spot_lifecycle_conflict(instances: list[InstanceTypeDef], action: Action, refer_to_docs_and_quit: QuitFn) -> None:
    if action not in ("start", "stop"):
        return
    spot_instance_ids = [instance["InstanceId"] for instance in instances if instance.get("InstanceLifecycle") == "spot"]
    if spot_instance_ids:
        refer_to_docs_and_quit(
            action.capitalize()
            + " is not supported for one-time Spot Instances ("
            + ", ".join(spot_instance_ids)
            + ") -- this toolkit always requests one-time Spot Instances, which AWS does not allow to be stopped and later restarted."
            + " Use --action terminate instead."
        )


# Function: print_status_table()
# Purpose: --status output -- one line per matched instance, including
# whether it's a Spot Instance (relevant context for whether start/stop
# will even work against it -- see check_spot_lifecycle_conflict()).


def print_status_table(instances: list[InstanceTypeDef]) -> None:
    print("")
    for instance in instances:
        is_spot = "Yes" if instance.get("InstanceLifecycle") == "spot" else "No"
        print("  " + instance["InstanceId"] + "  " + tag_value(instance, "Name") + "  (" + instance["State"]["Name"] + ")" + "  Spot: " + is_spot)
    print("")


# Function: print_all_instances_table()
# Purpose: --list-all output. The Spot column is conditional -- only
# added if at least one instance in *this* result set is actually a Spot
# Instance, so an all-ondemand account/region doesn't show a column of
# nothing but "No".


def print_all_instances_table(instances: list[InstanceTypeDef]) -> None:
    if not instances:
        print("No Ec2InstanceMaker-managed instances were found.")
        return
    any_spot = any(instance.get("InstanceLifecycle") == "spot" for instance in instances)
    table = PrettyTable()
    table.field_names = ["Name", "Instance ID", "Instance Type", "Base OS", "Instance Owner"] + (["Spot"] if any_spot else [])
    for instance in instances:
        row = [
            tag_value(instance, "Name"),
            instance["InstanceId"],
            instance["InstanceType"],
            tag_value(instance, "OperatingSystem"),
            tag_value(instance, "InstanceOwner"),
        ]
        if any_spot:
            row.append("Yes" if instance.get("InstanceLifecycle") == "spot" else "No")
        table.add_row(row)
    print(table)


# Function: terminate_via_kill_script()
# Purpose: "terminate" delegates entirely to kill-instance.<name>.sh, which
# does the full teardown (security group, IAM role/policy/profile, SNS
# topic, AMI/snapshot, local state files and symlinks) that a bare
# ec2:TerminateInstances call would leave dangling. This only works from
# the repo checkout where the instance was built -- same dispatch model
# access_instance.py already uses. Returns the subprocess exit code.


def terminate_via_kill_script(
    instance_name: str,
    auto_confirm: bool,
    refer_to_docs_and_quit: QuitFn,
    run_kill_script: Callable[[list[str]], subprocess.CompletedProcess[bytes]] = subprocess.run,
    confirm_input: Callable[[str], str] = input,
) -> int:
    kill_script = "kill-instance." + instance_name + ".sh"
    if not os.path.exists(kill_script):
        refer_to_docs_and_quit('kill-instance script "' + kill_script + '" was not found! Run manage_instance.py from the repo checkout where "' + instance_name + '" was built.')
    if not auto_confirm:
        print("")
        print('This will fully tear down "' + instance_name + '": the instance(s), security group, IAM role, SNS topic, and local state.')
        confirmation = confirm_input('Type "yes" to continue: ')
        if confirmation.strip().lower() != "yes":
            print("Aborting...")
            sys.exit(1)
    with instance_lock(instance_name, refer_to_docs_and_quit):
        return run_kill_script(["bash", kill_script]).returncode


def main() -> NoReturn:
    parser = argparse.ArgumentParser(description="manage_instance.py: start, stop, reboot, terminate, check status, or list Ec2InstanceMaker-built EC2 instances")
    parser.add_argument("--instance_name", "-N", help="name of the EC2 instance or family (required for all actions except --list-all)", required=False, default=None)
    parser.add_argument("--region", "-r", help="AWS region (default: read from ./vars_files/<instance_name>.yml; required for --list-all)", required=False, default=None)
    parser.add_argument("--auto_confirm", "-c", help="skip the confirmation prompt", action="store_true", required=False, default=False)

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--action", "-A", choices=["start", "stop", "reboot", "terminate"], dest="action", help="action to perform on --instance_name")
    action_group.add_argument("--status", "-S", action="store_const", dest="action", const="status", help="report the status (including whether it's Spot) of --instance_name")
    action_group.add_argument("--list-all", "-l", action="store_const", dest="action", const="list-all", help="list every Ec2InstanceMaker-managed instance in --region")

    args = parser.parse_args()
    instance_name: str | None = args.instance_name
    # argparse's own choices/const values are the only ones action can ever
    # hold, but argparse itself has no way to express that statically --
    # this annotated assignment documents (and lets mypy enforce downstream)
    # the closed set every other function in this file expects.
    action: Action = args.action
    region: str | None = args.region
    auto_confirm: bool = args.auto_confirm

    if instance_name:
        validate_instance_name_format(instance_name, refer_to_docs_and_quit)

    if action == "terminate":
        # instance_name is required for every action except --list-all;
        # checked here (rather than once upfront) so the None-check is
        # directly adjacent to each use site -- lets a type checker narrow
        # instance_name from str | None to str for the rest of each branch,
        # instead of a single combined "action != list-all and not
        # instance_name" condition it can't use for narrowing at all.
        if instance_name is None:
            refer_to_docs_and_quit("--instance_name/-N is required for --action=" + str(action) + "!")
        sys.exit(terminate_via_kill_script(instance_name, auto_confirm, refer_to_docs_and_quit))

    if action == "list-all":
        if region is None:
            refer_to_docs_and_quit("--region/-r is required for --list-all!")
        ec2_client = boto3.client("ec2", region_name=region)
        instances = list_all_managed_instances(ec2_client, region, refer_to_docs_and_quit)
        print_all_instances_table(instances)
        sys.exit(0)

    if instance_name is None:
        refer_to_docs_and_quit("--instance_name/-N is required for --action=" + str(action) + "!")
    region = resolve_region(instance_name, region, refer_to_docs_and_quit)
    ec2_client = boto3.client("ec2", region_name=region)
    instances = find_managed_instances(ec2_client, instance_name, region, refer_to_docs_and_quit)

    if action == "status":
        print_status_table(instances)
        sys.exit(0)

    check_spot_lifecycle_conflict(instances, action, refer_to_docs_and_quit)

    instance_ids = [instance["InstanceId"] for instance in instances]

    print("")
    print("The following instance(s) will be " + action + "ed:")
    for instance in instances:
        print("  " + instance["InstanceId"] + "  " + tag_value(instance, "Name") + "  (" + instance["State"]["Name"] + ")")
    print("")

    if not auto_confirm:
        confirmation = input('Type "yes" to continue: ')
        if confirmation.strip().lower() != "yes":
            print("Aborting...")
            sys.exit(1)

    # Each EC2 client method below has its own boto3-stubs keyword-argument
    # shape (different optional params per action) -- a dict-dispatch table
    # of the three bound methods used to type as "callable with unknown
    # signature" under mypy, since it can't unify them. A direct if/elif
    # keeps every call fully type-checked with no Protocol/cast needed.
    try:
        if action == "start":
            ec2_client.start_instances(InstanceIds=instance_ids)
        elif action == "stop":
            ec2_client.stop_instances(InstanceIds=instance_ids)
        else:
            ec2_client.reboot_instances(InstanceIds=instance_ids)
    except (ClientError, EndpointConnectionError) as e:
        refer_to_docs_and_quit("AWS API error while trying to " + action + " " + instance_name + ": " + str(e))

    print(action.capitalize() + " request sent for: " + ", ".join(instance_ids))
    sys.exit(0)


if __name__ == "__main__":
    main()
