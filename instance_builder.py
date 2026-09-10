################################################################################
# Name:		instance_builder.py
# Purpose:	Pure, independently-testable pieces of make_instance.py's build
# 		flow, extracted incrementally (see CLAUDE-STATE.md for the
# 		rationale and the phased extraction plan this is part of).
#
# make_instance.py is a ~1200-line linear script with no functions and heavy
# implicit state-threading between sections -- there was previously no way
# to unit-test any of its logic without mocking the entire script. This
# module is where extracted pieces land: each function here takes its
# dependencies as explicit arguments (no hidden globals, no reliance on
# execution order) and is covered by tests/test_instance_builder.py.
#
# Not every phase belongs here yet -- only the ones extracted so far. See
# CLAUDE-STATE.md for what's been moved and what's still inline in
# make_instance.py.
################################################################################

import contextlib
import errno
import fcntl
import ipaddress
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime as DateTime
from typing import Any, Literal, NoReturn, cast

import boto3
from botocore.exceptions import ClientError
from mypy_boto3_ec2.client import EC2Client
from mypy_boto3_ec2.literals import InstanceTypeType
from mypy_boto3_ec2.service_resource import EC2ServiceResource, SecurityGroup
from mypy_boto3_ec2.type_defs import FilterTypeDef, TagTypeDef
from mypy_boto3_iam.client import IAMClient
from mypy_boto3_logs.client import CloudWatchLogsClient
from mypy_boto3_sns.client import SNSClient
from mypy_boto3_sts.client import STSClient

# Type aliases used throughout this module's signatures:
# - QuitFn: the shape of refer_to_docs_and_quit/illegal_az_msg and every
#   other operator-facing fatal-error callback -- always prints a message
#   and calls sys.exit(1), so it never returns to its caller.
# - BoolStr: this codebase's pervasive Ansible-style string-boolean
#   convention ("true"/"false" as literal strings, not real bool values --
#   they get serialized directly into generated shell/Terraform templates).
#   Literal instead of bare str so a typo'd "True"/"yes" is a type error,
#   not a silently-false runtime comparison.
# boto3/botocore clients and resources are typed via boto3-stubs
# (mypy_boto3_*), a dev-only dependency (requirements-test.txt) -- it ships
# real, importable runtime modules (not stub-only), generated directly from
# each AWS service's API model, so a wrong method name or kwarg on any of
# these is now a type error, not a runtime AttributeError/ClientError.
QuitFn = Callable[[str], NoReturn]
BoolStr = Literal["true", "false"]

# The narrowest CIDR prefix length --ssh_allowed_ips will accept. /8 is a
# deliberately conservative floor: it still permits a legitimately large
# corporate range (16.7M addresses) while rejecting /0 through /7, which
# are all "a meaningful fraction of the entire internet" -- see
# resolve_ssh_allowed_ips() below for why a string comparison against
# "0.0.0.0/0" was not enough on its own.
MINIMUM_SSH_ALLOWED_IPS_PREFIXLEN = 8

# Function: validate_az_and_region()
# Purpose: abort cleanly if the selected AWS Region/Availability Zone is
# invalid. Must run before any other AWS API call that doesn't itself
# handle a bad region/AZ cleanly -- describe_availability_zones is the
# first call to hit a bogus region with a recognizable, catchable error.


def validate_az_and_region(ec2_client: EC2Client, az: str, illegal_az_msg: QuitFn) -> None:
    import botocore

    try:
        zone_information = ec2_client.describe_availability_zones()
    except ValueError:
        illegal_az_msg(az)
        return
    except botocore.exceptions.EndpointConnectionError:
        illegal_az_msg(az)
        return
    # This used to check only that the *region endpoint* resolved, never
    # that the AZ existed -- so "us-east-1z" sailed through here and
    # surfaced much later as an empty describe_subnets result, reported as
    # "does not contain any valid subnets", which points at the wrong
    # problem entirely.
    zone_names = [str(zone["ZoneName"]) for zone in zone_information.get("AvailabilityZones", [])]
    if zone_names and az not in zone_names:
        illegal_az_msg(az)


# Function: generate_instance_serial_number()
# Purpose: derive the instance_serial_number and the human-readable
# deployment date/tag strings used throughout the build. `now` defaults to
# the real current time; tests pass a fixed value for determinism.


def generate_instance_serial_number(instance_name: str, now: time.struct_time | None = None) -> dict[str, str]:
    if now is None:
        now = time.localtime()
    return {
        "DEPLOYMENT_DATE": time.strftime("%B %-d, %Y", now),
        "DEPLOYMENT_DATE_TAG": time.strftime("%-d-%B-%Y", now),
        "instance_serial_datestamp": time.strftime("%S%M%H%d%m%Y", now),
        "instance_serial_number": instance_name + "-" + time.strftime("%S%M%H%d%m%Y", now),
    }


# Function: generate_sns_timestamps()
# Purpose: derive the datestamp/timestamp strings used in the SNS
# notification message. `now` defaults to the real current UTC time; tests
# pass a fixed value for determinism.


def generate_sns_timestamps(now: DateTime | None = None) -> tuple[str, str]:
    if now is None:
        now = DateTime.now(UTC)
    sns_datestamp = now.strftime("%m") + "-" + now.strftime("%d") + "-" + now.strftime("%Y")
    sns_timestamp = now.strftime("%H") + ":" + now.strftime("%M")
    return sns_datestamp, sns_timestamp


# Function: validate_and_resize_ebs_volumes()
# Purpose: enforce the 16 TB EBS size ceiling, bump undersized root/device
# volumes up to AWS's recommended 30 GB minimum for Windows Server, and
# validate provisioned-IOPS bounds for whichever of the root/device volumes
# is actually "io1" -- root_volume_type and device_volume_type are
# independently selectable CLI flags, so each must be checked against its
# own type, not the other's (a real bug found during an adversarial
# review: this used to gate both IOPS checks on ebs_root_volume_type alone,
# so an io1 *device* volume paired with a non-io1 root never got its IOPS
# bounds validated -- or an IOPS value at all, since
# DEFAULT_EC2_TEMPLATE.j2's device ebs_block_device had the identical bug,
# fixed alongside this).
# Returns the (possibly Windows-adjusted) root/device volume sizes.


def validate_and_resize_ebs_volumes(
    ebs_root_volume_size: int,
    ebs_device_volume_size: int,
    ebs_root_volume_type: str,
    ebs_device_volume_type: str,
    ebs_root_volume_iops: int,
    ebs_device_volume_iops: int,
    is_windows: bool,
    refer_to_docs_and_quit: QuitFn,
) -> tuple[int, int]:
    if ebs_root_volume_size > 16000:
        refer_to_docs_and_quit("Maximum allowed EBS volume size is 16 TB (16000 GB)!")
    if ebs_device_volume_size > 16000:
        refer_to_docs_and_quit("Maximum allowed secondary EBS device volume size is 16 TB (16000 GB)!")
    if is_windows:
        if ebs_root_volume_size <= 30:
            ebs_root_volume_size = 30
        if ebs_device_volume_size <= 30:
            ebs_device_volume_size = 30
    # These checks used to say "between 100 and 16,000" while only rejecting
    # 0 and >16000 -- so iops=1 and iops=-5 both sailed through and failed
    # later inside AWS with exactly the opaque error this function exists to
    # pre-empt, by which point a security group and keypair already exist.
    for label, volume_type, volume_iops, volume_size in (
        ("ebs_root_volume", ebs_root_volume_type, ebs_root_volume_iops, ebs_root_volume_size),
        ("ebs_device_volume", ebs_device_volume_type, ebs_device_volume_iops, ebs_device_volume_size),
    ):
        if volume_size < 0:
            refer_to_docs_and_quit(label + "_size may not be negative!")
        if label == "ebs_root_volume" and volume_size < 1:
            refer_to_docs_and_quit("ebs_root_volume_size must be at least 1 GB!")
        if volume_type == "io1" and (volume_iops < 100 or volume_iops > 16000):
            refer_to_docs_and_quit(label + "_iops must be set to a value between 100 and 16,000!")
        # 0 is the "no secondary volume configured" sentinel for the device
        # volume, so the remaining size-dependent checks only apply once a
        # volume actually exists.
        if volume_size == 0:
            continue
        if volume_type == "st1" and volume_size < 125:
            refer_to_docs_and_quit(label + "_size must be at least 125 GB for an st1 volume -- that is AWS's minimum for the type.")
        if volume_type != "io1":
            continue
        # io1 caps provisioned IOPS at 50 per GiB. Without this, something
        # like iops=16000 on an 8 GB volume (a 2000:1 ratio) validates here
        # and is refused by EC2 at apply time.
        if volume_iops > volume_size * 50:
            refer_to_docs_and_quit(
                label + "_iops of " + str(volume_iops) + " exceeds 50 IOPS per GB, which is AWS's io1 limit. A " + str(volume_size) + " GB volume allows at most " + str(volume_size * 50) + " IOPS."
            )
    # EC2 cannot boot from a throughput-optimized volume.
    if ebs_root_volume_type == "st1":
        refer_to_docs_and_quit("ebs_root_volume_type may not be st1 -- EC2 does not support st1 as a root device. Use gp2 or io1.")
    return ebs_root_volume_size, ebs_device_volume_size


# Function: resolve_vpc_and_subnet()
# Purpose: resolve vpc_id/vpc_name/subnet_id from the selected AWS Region
# and Availability Zone. If vpc_name is the default sentinel, uses the
# account's default VPC; otherwise looks up the named VPC by its Name tag.
#
# The `except NameError` below is intentional, preserved as-is from the
# original inline code: if no VPC matches (describe_vpcs returns an empty
# list), the `for vpc in vpc_information["Vpcs"]:` loop body never runs, so
# vpc_id is never assigned -- referencing it while building the subnet
# Filters raises NameError, which is what actually catches "no such VPC"
# here. Fragile relative to an explicit emptiness check, but this is a
# faithful extraction, not a rewrite; a cleaner rewrite is a candidate for
# a future increment, not bundled into this one.


def resolve_vpc_and_subnet(ec2_client: EC2Client, vpc_name: str, az: str, refer_to_docs_and_quit: QuitFn) -> tuple[str, str, str]:
    if vpc_name == "vpc_default":
        vpc_information = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    else:
        vpc_information = ec2_client.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [vpc_name]}])

    vpcs = vpc_information["Vpcs"]
    if not vpcs:
        refer_to_docs_and_quit('"' + vpc_name + '" is an undefined VPC!')
    if len(vpcs) > 1:
        refer_to_docs_and_quit(
            'More than one VPC matches "'
            + vpc_name
            + '" ('
            + ", ".join(str(vpc["VpcId"]) for vpc in vpcs)
            + "). Refusing to guess which one you meant -- give the VPCs distinct Name tags, or pass --vpc_name to select one."
        )
    vpc = vpcs[0]
    vpc_id = vpc["VpcId"]
    # Read the tag whose Key is literally "Name", not whatever happens to
    # sort first. This used to be Tags[0], so *any* tag on the VPC could
    # become vpc_name -- meaning anyone with ec2:CreateTags on the VPC
    # (including the default VPC, which every build touches by default)
    # controlled a value that gets interpolated into generated Terraform.
    # The old loop also reassigned vpc_id per iteration while reading the
    # name from Vpcs[0], so with more than one match it could return an id
    # and a name belonging to different VPCs.
    name_tag = next((str(tag["Value"]) for tag in vpc.get("Tags", []) if tag.get("Key") == "Name"), "")
    if not name_tag:
        refer_to_docs_and_quit(
            str(vpc_id)
            + " lacks a valid Name tag! This will break Terraform. Tag it and retry:\n\n"
            + "aws --region "
            + az[:-1]
            + " ec2 create-tags --resources "
            + str(vpc_id)
            + " --tags Key=Name,Value="
            + str(vpc_id)
        )
    validate_vpc_name_format(name_tag, str(vpc_id), refer_to_docs_and_quit)
    vpc_name = name_tag

    subnet_information = ec2_client.describe_subnets(
        Filters=[
            {"Name": "availabilityZone", "Values": [az]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ],
    )
    try:
        subnet_id = subnet_information["Subnets"][0]["SubnetId"]
    except IndexError:
        refer_to_docs_and_quit("AvailabilityZone " + az + " does not contain any valid subnets!")

    return vpc_id, vpc_name, subnet_id


# Function: resolve_ssh_allowed_ips()
# Purpose: resolve the CIDR block that will be allowed to reach the
# instance's SSH/RDP port. "UNDEFINED" (the CLI default) resolves to the
# instance's own VPC CIDR, so an operator who never touches the flag still
# gets a scoped security group rather than the open internet. 0.0.0.0/0 is
# refused outright -- there is no legitimate reason to open SSH/RDP to the
# entire internet from this tool, and silently accepting it would defeat
# the point of this function existing.
#
# Refusing only the literal string "0.0.0.0/0" is NOT sufficient, which is
# what this originally did: an adversarial review showed "0.0.0.0/1" plus
# "128.0.0.0/1" covers the whole internet in two rules, and "0.0.0.0/0.0.0.0"
# and "1.2.3.4/0" are both /0 by another spelling. The check is therefore on
# the parsed network's prefix length, not on any string, and the normalized
# network is what gets returned.


def resolve_ssh_allowed_ips(ec2_client: EC2Client, vpc_id: str, ssh_allowed_ips: str, refer_to_docs_and_quit: QuitFn) -> str:
    if ssh_allowed_ips == "UNDEFINED":
        vpc_info = ec2_client.describe_vpcs(VpcIds=[vpc_id])
        return vpc_info["Vpcs"][0]["CidrBlock"]
    try:
        network = ipaddress.ip_network(ssh_allowed_ips, strict=False)
    except ValueError:
        refer_to_docs_and_quit('"' + ssh_allowed_ips + '" is not a valid CIDR block!')
    if network.version != 4:
        refer_to_docs_and_quit(
            '"'
            + ssh_allowed_ips
            + '" is not an IPv4 CIDR block! The security group ingress rule this feeds is IPv4-only (CidrIp), so an IPv6 value would fail inside AWS with a much less obvious error.'
        )
    if network.prefixlen < MINIMUM_SSH_ALLOWED_IPS_PREFIXLEN:
        refer_to_docs_and_quit(
            '"'
            + ssh_allowed_ips
            + '" is too broad -- it covers '
            + str(network.num_addresses)
            + " addresses. --ssh_allowed_ips must be /"
            + str(MINIMUM_SSH_ALLOWED_IPS_PREFIXLEN)
            + " or narrower so the instance's SSH/RDP port is not exposed to a large swath of the internet. (0.0.0.0/0 is the extreme case, but /1 through /"
            + str(MINIMUM_SSH_ALLOWED_IPS_PREFIXLEN - 1)
            + " are barely better -- two /1 rules cover the entire internet.)"
        )
    # Return the *normalized* network rather than the operator's raw
    # string. ip_network(strict=False) accepts host bits set and
    # alternative spellings ("1.2.3.4/0", "0.0.0.0/0.0.0.0"), so echoing
    # the input back would let a value that passed the checks above reach
    # AWS looking different from what was actually validated.
    return str(network)


# Function: resolve_security_group()
# Purpose: reuse the named EC2 security group if it already exists,
# otherwise create it with the appropriate inbound rule for the base_os.
#
# Also reports which of those two happened, as preserve_security_group,
# which is threaded into the generated kill script the same way
# preserve_iam_role already is. Teardown previously deleted the security
# group unconditionally, by hardcoded ID -- so `--security_group
# corp-shared-sg` against an existing group meant kill-instance.<name>.sh
# would delete a security group this toolkit never created, and which
# other things may well still be using. Only a group this build actually
# created is ours to delete.
#
# Note that the reuse path deliberately does not touch the existing
# group's rules, which also means --ssh_allowed_ips has no effect there;
# make_instance.py warns about that at the call site, since silently
# ignoring a security flag is worse than saying so.
# (RDP/3389 for Windows, SSH/22 otherwise), scoped to ssh_allowed_ips
# (see resolve_ssh_allowed_ips() above -- never 0.0.0.0/0). Returns the
# resolved security_group_name (never the boto3 resource object -- the
# original inline code reassigned a `security_group` local from a string
# to a resource object mid-flow, which this extraction deliberately avoids
# propagating) and the parsed vpc_security_group_ids string.


def resolve_security_group(
    ec2: EC2ServiceResource,
    security_group_name: str,
    instance_serial_number: str,
    vpc_id: str,
    is_windows: bool,
    ssh_allowed_ips: str,
    add_inbound_security_group_rule: Callable[[SecurityGroup, str, str, int, int], None],
) -> tuple[str, str, BoolStr]:
    if security_group_name == "ec2instancemaker_sg":
        security_group_name = security_group_name + "_" + instance_serial_number
    filters: list[FilterTypeDef] = [{"Name": "group-name", "Values": [security_group_name]}, {"Name": "vpc-id", "Values": [vpc_id]}]
    sg_id = list(ec2.security_groups.filter(Filters=filters))
    preserve_security_group: BoolStr = "true"
    if not sg_id:
        security_group = ec2.create_security_group(GroupName=security_group_name, Description="EC2 security group - created by Ec2InstanceMaker", VpcId=vpc_id)
        if is_windows:
            add_inbound_security_group_rule(security_group, "tcp", ssh_allowed_ips, 3389, 3389)
        else:
            add_inbound_security_group_rule(security_group, "tcp", ssh_allowed_ips, 22, 22)
        sg_id = list(ec2.security_groups.filter(Filters=filters))
        preserve_security_group = "false"
    vpc_security_group_ids = sg_id[0].id
    return security_group_name, vpc_security_group_ids, preserve_security_group


# Function: setup_keypair()
# Purpose: reuse the named EC2 keypair if it already exists in this region,
# otherwise create it and write its private key material to
# secret_key_file with 0600 permissions. Aborts (via sys.exit, matching
# the original inline code's operator-facing guidance message, not
# refer_to_docs_and_quit) if the keypair exists in AWS but the local
# secret_key_file is missing -- that mismatch needs a human decision
# (delete and recreate the AWS-side keypair, or find the missing file),
# not a generic error.


def setup_keypair(ec2_client: EC2Client, ec2_keypair: str, secret_key_file: str, region: str, debug_mode: BoolStr, refer_to_docs_and_quit: QuitFn) -> None:
    try:
        ec2_client.describe_key_pairs(KeyNames=[ec2_keypair])
        if debug_mode == "true":
            print("")
        print("Found EC2 keypair: " + ec2_keypair)
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidKeyPair.NotFound":
            new_ec2_keypair = ec2_client.create_key_pair(KeyName=ec2_keypair)
            # Open with mode 0o600 from creation (os.open, not open()+chmod)
            # so the private key material is never briefly world/group-
            # readable under a permissive umask between being written and
            # being locked down.
            fd = os.open(secret_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                print(new_ec2_keypair["KeyMaterial"], file=fh)
            print("Created EC2 keypair: " + ec2_keypair)
        else:
            # Regression-preventing fix, same class as the IAM-creation
            # ClientError swallows fixed earlier this session: any other
            # AWS API error here (throttling, AccessDenied) used to be
            # silently ignored, falling through to a misleading "secret
            # key file is missing" message instead of the real problem.
            refer_to_docs_and_quit("AWS API error while checking EC2 keypair " + ec2_keypair + ": " + str(e))

    if not os.path.isfile(secret_key_file):
        print("")
        print("*** ERROR ***")
        print("Missing: " + secret_key_file)
        print("")
        print("If you are sure this is an error, please delete the original key pair")
        print("by pasting this command into the shell and retrying:")
        print("")
        print("aws --region " + region + " ec2 delete-key-pair --key-name " + ec2_keypair)
        print("")
        print("Aborting...")
        sys.exit(1)
    if debug_mode == "true":
        print("")


# Function: resolve_ami()
# Purpose: resolve aws_ami -- either from the base_os/architecture AMI
# catalog, or by verifying an operator-supplied custom_ami exists in this
# AWS account. get_ami_info/check_custom_ami are dependency-injected
# (aux_data.py's already-tested functions of the same name) so this
# doesn't need to import aux_data.py directly.


def resolve_ami(
    custom_ami: str,
    base_os: str,
    architecture: str,
    aws_account_id: str,
    get_ami_info: Callable[[str, str], str],
    check_custom_ami: Callable[[str, str, str], str],
    refer_to_docs_and_quit: QuitFn,
) -> str:
    if custom_ami == "UNDEFINED":
        return get_ami_info(base_os, architecture)
    aws_ami = check_custom_ami(custom_ami, aws_account_id, architecture)
    if aws_ami == "false":
        refer_to_docs_and_quit('AMI image "' + custom_ami + '" is unavailable in this AWS account!')
    return aws_ami


# Function: fetch_spot_price_raw()
# Purpose: look up the most recent EC2 Spot price for instance_type/az.


def fetch_spot_price_raw(ec2_client: EC2Client, instance_type: str, is_windows: bool, az: str, refer_to_docs_and_quit: QuitFn) -> float:
    product_description = "Windows" if is_windows else "Linux/UNIX"
    # See the matching cast in aux_data.get_instance_type_info() -- same
    # reasoning: instance_type stays a plain str so a new AWS instance
    # family works without waiting on a boto3-stubs update.
    prices = ec2_client.describe_spot_price_history(InstanceTypes=[cast(InstanceTypeType, instance_type)], MaxResults=1, ProductDescriptions=[product_description], AvailabilityZone=az)
    history = prices["SpotPriceHistory"]
    if not history:
        # Empty means AWS offers no Spot capacity for this instance type in
        # this AZ. That used to be an IndexError traceback.
        refer_to_docs_and_quit(
            "No Spot price history for " + instance_type + " (" + product_description + ") in " + az + "."
            " AWS does not offer this instance type as a Spot Instance there -- pick another instance type or AZ, or use --request_type=ondemand."
        )
    return float(history[0]["SpotPrice"])


# Function: compute_buffered_spot_price()
# Purpose: apply spot_buffer to a raw historical Spot price, rounded to 8
# decimal places, to protect against Spot market fluctuations:
# spot_price = spot_price_raw + (spot_buffer * spot_price_raw)


def compute_buffered_spot_price(spot_price_raw: float, spot_buffer: float) -> float:
    return round(spot_price_raw + (spot_buffer * spot_price_raw), 8)


# Function: build_sns_message()
# Purpose: construct the SNS notification body/subject announcing instance
# (or instance family) creation.


def build_sns_message(count: int, instance_name: str, instance_type: str, request_type: str, sns_datestamp: str, sns_timestamp: str) -> tuple[str, str]:
    if count > 1:
        sns_message_body = f"""\
Ec2InstanceMaker has created a new instance family.

InstanceName: {instance_name}
InstanceType: {instance_type}
RequestType:  {request_type}
Count:        {count}
DateStamp:    {sns_datestamp}
TimeStamp:    {sns_timestamp}
"""
        sns_instance_subject = "[Ec2InstanceMaker] Instance Family Creation Notice"
    else:
        sns_message_body = f"""\
Ec2InstanceMaker has created a new instance.

InstanceName: {instance_name}
InstanceType: {instance_type}
RequestType:  {request_type}
DateStamp:    {sns_datestamp}
TimeStamp:    {sns_timestamp}
"""
        sns_instance_subject = "[Ec2InstanceMaker] Instance Creation Notice"
    return sns_message_body, sns_instance_subject


# Function: apply_terraform()
# Purpose: run terraform init/plan/apply in instance_data_dir to actually
# create the EC2 instance(s). Sets TF_LOG=DEBUG when debug_mode is enabled.
# This performs real, potentially destructive infrastructure changes --
# tests must always patch subprocess.run rather than let this run for real.
# Each step's return code is checked before proceeding to the next -- a
# failed "terraform init" (e.g. a stale plugin cache) must never be allowed
# to fall through into "plan"/"apply" against a half-initialized working
# directory.


def apply_terraform(instance_data_dir: str, debug_mode: BoolStr, refer_to_docs_and_quit: QuitFn) -> None:
    import subprocess

    tf_env = {**os.environ, "TF_LOG": "DEBUG"} if debug_mode == "true" else None
    steps = [
        ["terraform", "init", "-input=false"],
        ["terraform", "plan", "-out", "terraform_environment"],
        ["terraform", "apply", "terraform_environment"],
    ]
    for step in steps:
        result = subprocess.run(step, cwd=instance_data_dir, env=tf_env)
        if result.returncode != 0:
            refer_to_docs_and_quit('"' + " ".join(step) + '" failed with exit code ' + str(result.returncode) + "!")


# Function: build_security_group_tags()
# Purpose: construct the EC2 tag set applied to the security group after
# the Terraform apply completes.


def build_security_group_tags(
    security_group_name: str,
    instance_name: str,
    instance_serial_number: str,
    instance_owner: str,
    instance_owner_email: str,
    instance_owner_department: str,
    deployment_date_tag: str,
    project_id: str,
) -> list[TagTypeDef]:
    tags: list[TagTypeDef] = [
        {"Key": "Name", "Value": security_group_name},
        {"Key": "Purpose", "Value": "EC2 security group for " + instance_name},
        {"Key": "Ec2InstanceBuilderTool", "Value": "boto3"},
        {"Key": "InstanceSerialNumber", "Value": instance_serial_number},
        {"Key": "InstanceOwner", "Value": instance_owner},
        {"Key": "InstanceOwnerEmail", "Value": instance_owner_email},
        {"Key": "InstanceOwnerDepartment", "Value": instance_owner_department},
        {"Key": "DEPLOYMENT_DATE_TAG", "Value": deployment_date_tag},
    ]
    if "UNDEFINED" not in project_id:
        tags.append({"Key": "ProjectID", "Value": project_id})
    return tags


# Function: fetch_windows_instance_details()
# Purpose: fetch instance_id/instance_name/ip_address for Windows
# instance(s) from Terraform's own structured output
# (`terraform output -json`) -- DEFAULT_EC2_TEMPLATE.j2 defines
# instance_id_list/instance_name_index/instance_ip_addresses as real
# Terraform outputs, so this reads them directly instead of grep/awk-ing
# `terraform show`'s human-readable text. One list-form subprocess call,
# no shell=True, no dependency on grep/awk being on PATH, and no fragile
# text-format parsing that Terraform's human-readable output isn't
# actually guaranteed to preserve across versions the way `-json` is.


def fetch_windows_instance_details(instance_data_dir: str, refer_to_docs_and_quit: QuitFn) -> tuple[str, str, str]:
    import json
    import subprocess

    result = subprocess.run(["terraform", "output", "-json"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=instance_data_dir)
    if result.returncode != 0:
        refer_to_docs_and_quit('"terraform output -json" failed with exit code ' + str(result.returncode) + "!")
    outputs = json.loads(result.stdout.decode("utf-8"))
    instance_id = outputs["instance_id_list"]["value"]
    instance_name = outputs["instance_name_index"]["value"]
    ip_address = outputs["instance_ip_addresses"]["value"]
    return instance_id, instance_name, ip_address


# Function: decrypt_windows_admin_passwords()
# Purpose: decrypt the Windows Administrator password for each instance ID
# in instance_ids_csv (a comma-separated string, matching Terraform's
# multi-instance output format), using the EC2 keypair's private key.
# Returns a comma-joined string in the same order as instance_ids_csv, so
# it zips positionally against instance names/IPs parsed separately.
# AWS doesn't populate PasswordData until Windows has finished generating
# it, which can take several minutes after launch -- until then,
# get-password-data returns an empty PasswordData field, which jq renders
# as the literal string "null" rather than raising an error. Detecting
# that case explicitly (instead of silently printing "null" as if it were
# a real password) tells the operator to wait and retry.

_WINDOWS_PASSWORD_NOT_YET_AVAILABLE = "(not yet available -- Windows password generation can take several minutes after launch; try again shortly)"
_WINDOWS_PASSWORD_RETRIEVAL_FAILED = "(retrieval FAILED -- `aws ec2 get-password-data` returned an error; check your credentials, IAM permissions, and that the keypair .pem is present)"


def decrypt_windows_admin_passwords(instance_data_dir: str, ec2_keypair: str, instance_ids_csv: str) -> str:
    import subprocess

    from jq import jq

    passwords = []
    for instance_id in str(instance_ids_csv).split(","):
        password_tf = subprocess.run(
            ["aws", "ec2", "get-password-data", "--instance-id", instance_id, "--priv-launch-key", ec2_keypair + ".pem"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=instance_data_dir,
        )
        if password_tf.returncode != 0:
            # The return code was ignored and stderr sent to DEVNULL, so a
            # failed call (expired credentials, no permission, a missing
            # .pem) produced empty stdout and was reported as "not yet
            # available -- try again shortly", which is advice that will
            # never come true.
            password = _WINDOWS_PASSWORD_RETRIEVAL_FAILED
        else:
            password = jq("..|.PasswordData?").transform(text=password_tf.stdout.decode("utf-8"), text_output=True)
            password = password.replace('"', "").strip()
            if not password or password == "null":
                password = _WINDOWS_PASSWORD_NOT_YET_AVAILABLE
        passwords.append(password)
    return ",".join(passwords)


# Function: derive_iam_names()
# Purpose: derive the role/policy/instance-profile names for the "create a
# new IAM role" path (iam_role == "UNDEFINED").
#
# Simplification note: the original inline code branched on
# `if iam_name_prefix == "Ec2InstanceMaker": ... else: ...`, but both
# branches compute the exact same string (the "if" branch is just the
# "else" branch's general formula with the specific value substituted in)
# -- confirmed by hand and preserved here as a single unconditional
# formula, not a behavior change.


def derive_iam_names(iam_name_prefix: str, instance_serial_number: str) -> tuple[str, str, str]:
    return (
        iam_name_prefix + "-role-" + instance_serial_number,
        iam_name_prefix + "-policy-" + instance_serial_number,
        iam_name_prefix + "-profile-" + instance_serial_number,
    )


# Function: ensure_iam_role_created()
# Purpose: create the IAM role + policy from the staged JSON policy
# document if it doesn't already exist (the iam_role == "UNDEFINED" path).


def ensure_iam_role_created(
    iam: IAMClient, role_name: str, policy_name: str, instance_json_policy_stage: str, instance_json_policy_template: str, debug_mode: BoolStr, refer_to_docs_and_quit: QuitFn
) -> None:
    try:
        iam.get_role(RoleName=role_name)
        if debug_mode == "true":
            print("")
        print("Found IAM EC2 instance role: " + role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            with open(instance_json_policy_stage) as src:
                filedata = src.read()
            with open(instance_json_policy_template, "w") as dest:
                dest.write(filedata)
            os.remove(instance_json_policy_stage)
            iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument='{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow", "Principal": { "Service": [ "ec2.amazonaws.com" ] }, "Action": "sts:AssumeRole" } ] }',
                Description="Generic Ec2InstanceMaker role",
            )
            with open(instance_json_policy_template) as policy_input:
                iam.put_role_policy(RoleName=role_name, PolicyName=policy_name, PolicyDocument=policy_input.read())
            if debug_mode == "true":
                print("")
            print("Created EC2 instance role: " + role_name)
        else:
            refer_to_docs_and_quit("AWS API error while checking IAM role " + role_name + ": " + str(e))


# Function: ensure_iam_role_exists()
# Purpose: verify an operator-supplied pre-existing IAM role exists (the
# iam_role != "UNDEFINED" path). Never creates anything -- a missing role
# here is a hard, immediate failure, since this role wasn't ours to create.


def ensure_iam_role_exists(iam: IAMClient, role_name: str, debug_mode: BoolStr, refer_to_docs_and_quit: QuitFn) -> None:
    try:
        iam.get_role(RoleName=role_name)
        if debug_mode == "true":
            print("")
        print("Found IAM EC2 instance role: " + role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            refer_to_docs_and_quit("IAM EC2 instance role " + role_name + " does not exist!")
        else:
            refer_to_docs_and_quit("AWS API error while checking IAM role " + role_name + ": " + str(e))


# Function: ensure_iam_instance_profile()
# Purpose: create the IAM instance profile and attach role_name to it if it
# doesn't already exist. Identical logic used to be copy-pasted in both
# the create-new-role and pre-existing-role branches; consolidated here
# since both need exactly this.


def ensure_iam_instance_profile(iam: IAMClient, profile_name: str, role_name: str, debug_mode: BoolStr, refer_to_docs_and_quit: QuitFn) -> None:
    try:
        iam.get_instance_profile(InstanceProfileName=profile_name)
        print("Found IAM EC2 instance profile: " + profile_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            iam.create_instance_profile(InstanceProfileName=profile_name)
            print("Created EC2 instance profile: " + profile_name)
            iam.add_role_to_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
            print("Added: " + role_name + " to " + profile_name)
        else:
            refer_to_docs_and_quit("AWS API error while checking IAM instance profile " + profile_name + ": " + str(e))


# Function: setup_iam()
# Purpose: orchestrate IAM role/policy/instance-profile setup for the new
# instance(s) -- either creating a fresh role scoped to this build
# (iam_role == "UNDEFINED"), or verifying an operator-supplied pre-existing
# role (which won't be deleted on teardown -- see preserve_iam_role).
# Returns (ec2_iam_instance_role, ec2_iam_instance_policy,
# ec2_iam_instance_profile, preserve_iam_role).


def setup_iam(
    iam: IAMClient,
    iam_role: str,
    iam_name_prefix: str,
    iam_json_policy: str,
    instance_data_dir: str,
    instance_serial_number: str,
    debug_mode: BoolStr,
    refer_to_docs_and_quit: QuitFn,
    modify_iam_policy_document: Callable[[str, str, str, str], None],
) -> tuple[str, str, str, BoolStr]:
    if iam_role == "UNDEFINED":
        # iam_json_policy is currently only ever one of these three
        # filenames -- enforced today by argparse's choices=[...] on
        # --iam_json_policy (make_instance.py's parse_args()), which every
        # call path (CLI and mcp_server.py's build_instance) goes through.
        # Checked again here, explicitly, rather than trusting that
        # invariant to hold forever: this string is about to become a
        # filesystem path, and an adversarial review found exactly this
        # "caller-validates, callee trusts" pattern exploitable elsewhere
        # in this codebase (mcp_server.py's instance_name handling) when
        # the calling convention changed out from under it.
        if iam_json_policy not in ("MinimalEc2InstancePolicy.json", "GenericEc2InstancePolicy.json", "ExtendedEc2InstancePolicy.json"):
            refer_to_docs_and_quit('"' + iam_json_policy + '" is not a recognized IAM policy document!')
        role_name, policy_name, profile_name = derive_iam_names(iam_name_prefix, instance_serial_number)
        instance_json_policy_src = "templates/" + iam_json_policy
        instance_json_policy_stage = instance_data_dir + "stage-" + iam_json_policy
        instance_json_policy_template = instance_data_dir + iam_json_policy
        preserve_iam_role: BoolStr = "false"
        modify_iam_policy_document(instance_json_policy_src, instance_json_policy_stage, iam_name_prefix, instance_serial_number)
        ensure_iam_role_created(iam, role_name, policy_name, instance_json_policy_stage, instance_json_policy_template, debug_mode, refer_to_docs_and_quit)
    else:
        role_name = iam_role
        policy_name = "UNDEFINED"
        profile_name = iam_role + "_instance_profile"
        preserve_iam_role = "true"
        ensure_iam_role_exists(iam, role_name, debug_mode, refer_to_docs_and_quit)
    ensure_iam_instance_profile(iam, profile_name, role_name, debug_mode, refer_to_docs_and_quit)
    return role_name, policy_name, profile_name, preserve_iam_role


# Class: InstanceParameters
# Purpose: the full set of values make_instance.py assembles from its build
# flow and hands to write_vars_file()/render_instance_templates() -- was a
# bare 58-key dict literal built by hand in make_instance.py, with no
# static check that every key a template or the vars_file format string
# expects was actually supplied (a missing one fails much later, inside
# template_engine.py's StrictUndefined render or a KeyError from
# .format(), far from where the dict was actually assembled). A dataclass
# catches a missing/misspelled field at construction instead, in
# make_instance.py itself. write_vars_file()/render_instance_templates()
# both stay dict[str, Any]-typed (they're general-purpose, used nowhere
# else with a typed structure) -- make_instance.py bridges via
# dataclasses.asdict() at each call site instead.


@dataclass
class InstanceParameters:
    architecture: str
    awscli_preinstalled: bool | None
    az: str
    aws_ami: str
    aws_account_id: str
    base_os: str
    is_windows: bool
    package_manager: str | None
    count: int
    custom_user_prelogin_scripts: list[str]
    custom_user_postboot_scripts: list[str]
    debug_mode: BoolStr
    ebs_encryption: BoolStr
    ebs_optimized: BoolStr
    ebs_root_volume_size: int
    ebs_root_volume_type: str
    ebs_root_volume_iops: int
    ebs_device_volume_size: int
    ebs_device_volume_type: str
    ebs_device_volume_iops: int
    instance_type: str
    ec2_keypair: str
    ec2_user: str
    ec2_user_home: str
    ec2_iam_instance_policy: str
    ec2_iam_instance_profile: str
    ec2_iam_instance_role: str
    enable_placement_group: BoolStr
    hyperthreading: BoolStr
    iam_name_prefix: str
    instance_data_dir: str
    instance_owner: str
    instance_owner_email: str
    instance_owner_department: str
    instance_name: str
    request_type: Literal["ondemand", "spot"]
    instance_serial_number: str
    instance_serial_number_file: str
    cloudwatch_log_group: str
    enable_cloudwatch_logs: BoolStr
    log_retention_days: int
    placement_group_strategy: str
    preserve_ami: BoolStr
    preserve_cloudwatch_logs: BoolStr
    prod_level: Literal["dev", "test", "stage", "prod"]
    project_id: str
    preserve_iam_role: BoolStr
    preserve_security_group: BoolStr
    public_ip: BoolStr
    region: str
    security_group_name: str
    spot_price: str | float
    ssh_allowed_ips: str
    vpc_security_group_ids: str
    sns_topic_arn: str
    sns_datestamp: str
    sns_timestamp: str
    subnet_id: str
    turbot_account: str
    vars_file_path: str
    vpc_id: str
    vpc_name: str
    DEPLOYMENT_DATE: str
    DEPLOYMENT_DATE_TAG: str
    TERRAFORM_VERSION: str


# Function: write_vars_file()
# Purpose: render vars_file_template with instance_parameters and write it
# to vars_file_path -- the human-readable per-instance audit record.


def write_vars_file(vars_file_path: str, vars_file_template: str, instance_parameters: dict[str, Any]) -> None:
    with open(vars_file_path, "w") as fh:
        fh.write(vars_file_template.format(**instance_parameters))


# Function: resolve_custom_user_scripts()
# Purpose: validate the --custom_user_scripts names against what actually
# exists in custom_user_scripts_dir *before* any rendering happens. A name
# needs at least one of the two files (prelogin and/or postboot) -- a
# module can be postboot-only or prelogin-only. A name with neither is a
# hard failure (almost certainly a typo), not a silent no-op. Returns
# (prelogin_names, postboot_names) -- each the subset of `names`, in the
# order given, that actually has that hook's file -- for template_engine.py
# to render.


def resolve_custom_user_scripts(names: list[str], custom_user_scripts_dir: str, refer_to_docs_and_quit: QuitFn) -> tuple[list[str], list[str]]:
    prelogin_names = []
    postboot_names = []
    for name in names:
        prelogin_path = os.path.join(custom_user_scripts_dir, "custom_user_prelogin_script.j2_" + name)
        postboot_path = os.path.join(custom_user_scripts_dir, "custom_user_postboot_script.j2_" + name)
        has_prelogin = os.path.isfile(prelogin_path)
        has_postboot = os.path.isfile(postboot_path)
        if not has_prelogin and not has_postboot:
            refer_to_docs_and_quit(
                '--custom_user_scripts "' + name + '" matches neither custom_user_prelogin_script.j2_' + name + " nor custom_user_postboot_script.j2_" + name + " in " + custom_user_scripts_dir + "!"
            )
        if has_prelogin:
            prelogin_names.append(name)
        if has_postboot:
            postboot_names.append(name)
    return prelogin_names, postboot_names


# Function: setup_cloudwatch_logging()
# Purpose: create (or reuse) the per-instance CloudWatch Logs group the
# CloudWatch Agent ships logs to, and set its retention policy. Done here
# via boto3, before Terraform ever runs, rather than from within the
# instance's own cloud-init -- so the group exists with the *correct*
# retention policy before the agent starts writing to it, instead of
# racing the agent's own auto-create-with-indefinite-retention behavior.


def setup_cloudwatch_logging(logs_client: CloudWatchLogsClient, log_group_name: str, log_retention_days: int, refer_to_docs_and_quit: QuitFn) -> None:
    try:
        logs_client.create_log_group(logGroupName=log_group_name, tags={"ManagedBy": "Ec2InstanceMaker"})
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            refer_to_docs_and_quit("AWS API error while creating CloudWatch Logs group " + log_group_name + ": " + str(e))
    try:
        logs_client.put_retention_policy(logGroupName=log_group_name, retentionInDays=log_retention_days)
    except ClientError as e:
        refer_to_docs_and_quit("AWS API error while setting retention policy on " + log_group_name + ": " + str(e))


################################################################################
# The functions below extract the remaining logic that was still inline in
# make_instance.py -- see CLAUDE-STATE.md for the phased plan this completes.
################################################################################

# Function: validate_instance_name_and_owner_format()
# Purpose: restrict instance_name/instance_owner to a safe charset -- both
# feed into far more than just AWS resource names/tags (where mixed case
# alone has caused real problems before, S3/DNS-style naming rules
# elsewhere in AWS): instance_name specifically also becomes a Terraform
# resource label (DEFAULT_EC2_TEMPLATE.j2's `resource "aws_instance"
# "{{ instance_name }}"`), a filesystem path component
# (vars_files/<name>.yml, instance_data/<name>/, the kill-instance./
# build-ami.<name>.sh symlinks template_engine.py creates), and an
# unquoted bash variable assignment (kill_instance.j2). Before this
# existed, only uppercase letters were rejected -- a value containing a
# quote, `../`, or a shell metacharacter could break out of any of those
# contexts (HCL string injection, path traversal, or, formerly, unquoted
# shell-command injection now separately closed by quoting those
# contexts too). Restricting the charset at the source closes all of
# them at once, rather than trying to escape correctly for every
# different context downstream.
#
# instance_owner gets a slightly wider allowance (dots/underscores, not
# just hyphens) since it's documented as an ActiveDirectory username
# (--instance_owner's own --help text), and real AD usernames commonly
# use `first.last`/`first_last` conventions.


def validate_instance_name_format(instance_name: str, refer_to_docs_and_quit: QuitFn) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", instance_name):
        refer_to_docs_and_quit("instance_name must start with a lowercase letter and contain only lowercase letters, numbers, and hyphens!")


def validate_instance_name_and_owner_format(instance_name: str, instance_owner: str, refer_to_docs_and_quit: QuitFn) -> None:
    validate_instance_name_format(instance_name, refer_to_docs_and_quit)
    if not re.fullmatch(r"[a-z][a-z0-9._-]*", instance_owner):
        refer_to_docs_and_quit("instance_owner must start with a lowercase letter and contain only lowercase letters, numbers, periods, underscores, and hyphens!")


# Function: validate_ec2_keypair_format()
# Purpose: restrict ec2_keypair to a charset that is safe in every context
# it reaches, for the same reason validate_instance_name_format() exists.
#
# ec2_keypair was missed when instance_name/instance_owner were locked
# down, and it lands in *more* contexts than either of them:
#   - a Python string literal   (access_instance.j2: `ec2_keypair = '...'`),
#     which access_instance.py then executes -- and note there is no
#     Jinja filter for the generated-Python context at all, so escaping
#     at the template was never an option here;
#   - a double-quoted bash string (kill_instance.j2: EC2_KEYPAIR="...",
#     SSH_KEYPAIR_FILE="....pem"), where $(...) and backticks expand;
#   - an HCL string literal      (DEFAULT_EC2_TEMPLATE.j2: key_name = "...");
#   - a filesystem path component (instance_data_dir + ec2_keypair +
#     ".pem"), where "../" would write RSA private key material outside
#     instance_data/.
#
# An adversarial review demonstrated injection in all four. Restricting
# the charset at the source closes every one of them at once, and is the
# only option that also covers the generated-Python sink. AWS itself only
# constrains a key pair name to <=255 ASCII characters, so this is
# deliberately stricter than the API is.


# Function: validate_free_text_field() / validate_email_format()
# Purpose: keep control characters out of the genuinely free-text fields.
#
# instance_owner_email, instance_owner_department and project_id are
# deliberately not charset-restricted the way instance_name/instance_owner
# are -- they are human text and should stay that way. But a *newline* in
# them is not human text, it is a context break, and these values reach a
# lot of contexts:
#
#   - the shipped custom_user_scripts templates interpolate
#     instance_owner_email into a `# Author:` comment with no filter, so a
#     newline escapes the comment. That content is embedded into
#     instance_userdata.j2's cloud-config, still parses as valid YAML, and
#     cloud-init runs it as root. An adversarial review demonstrated this
#     end to end.
#   - the toolkit's own templates do filter them (shquote/tf_shquote/
#     hcl_escape), but none of those three escape a raw newline, so one
#     produces `Error: Invalid multi-line string` and aborts the build.
#   - custom_user_scripts/ is a user-owned drop-in directory. Templates
#     there cannot be forced to apply a filter, so the only place this can
#     be closed for them is at the source.
#
# Rejecting control characters costs nothing legitimate -- no real
# department name, project id or email address contains one -- and it is
# the only fix that covers templates this repo does not control.


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def validate_free_text_field(value: str, field_name: str, refer_to_docs_and_quit: QuitFn) -> None:
    if _CONTROL_CHARACTERS.search(value):
        refer_to_docs_and_quit(
            field_name + " may not contain control characters such as newlines or tabs. "
            "These values are interpolated into generated shell scripts, Terraform config and cloud-init YAML, "
            "where a line break means something quite different from what you probably intended."
        )


def validate_email_format(instance_owner_email: str, refer_to_docs_and_quit: QuitFn) -> None:
    validate_free_text_field(instance_owner_email, "instance_owner_email", refer_to_docs_and_quit)
    # Deliberately permissive -- this is a sanity check, not RFC 5322. It
    # exists so an obvious typo fails here rather than at sns:Subscribe,
    # after a security group and keypair already exist.
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", instance_owner_email):
        refer_to_docs_and_quit('"' + instance_owner_email + '" does not look like an email address!')


# Function: validate_vpc_name_format()
# Purpose: vpc_name becomes a Terraform *identifier*, not a string.
#
# template_engine.py builds `provider = "aws." + vpc_name` and
# DEFAULT_EC2_TEMPLATE.j2 emits it as a bare HCL identifier
# (`provider = aws.<vpc_name>`), where no escaping filter applies or could
# apply -- an identifier position has no quoting to escape into. The same
# value also reaches provider_aws.j2's `alias`. So the only place this can
# be made safe is here, at the source.
#
# That matters more than it looks: vpc_name is not operator input in the
# usual sense. It is read back off the VPC's Name tag, so its real author
# is whoever can call ec2:CreateTags on that VPC. An adversarial review
# demonstrated a crafted value closing the resource block and appending an
# entire extra Terraform resource; it was only prevented from applying
# because the same value separately broke provider_aws.j2's parsing, which
# is an accident rather than a defense.
#
# Terraform identifiers are letters, digits, underscores and hyphens, and
# must not start with a digit -- anything outside that would fail to parse
# anyway, so rejecting it here turns a confusing HCL syntax error deep in
# `terraform apply` into an actionable message before anything is created.


# Function: validate_iam_name_lengths()
# Purpose: refuse an instance_name that would generate an IAM role name
# longer than AWS accepts.
#
# derive_iam_names() builds "<prefix>-role-<instance_serial_number>", and
# instance_serial_number is "<instance_name>-<14-digit timestamp>". So the
# role name is len(prefix) + len(instance_name) + 21, against IAM's 64
# character limit for a role name. A perfectly reasonable
# instance_name of 28 characters overflows it with the default prefix --
# and it fails at create_role in phase 3, after the security group,
# keypair and CloudWatch log group already exist, with an AWS-side error
# that does not obviously point at the name being too long.


_IAM_ROLE_NAME_MAX = 64
_SERIAL_AND_SEPARATORS_LENGTH = 21


# Function: validate_custom_ami_format()
# Purpose: --custom_ami is passed to describe_images as an "image-id"
# *filter value*, and EC2 filter values support "*"/"?" wildcards. So
# --custom_ami 'ami-*' would match every AMI the account owns and
# check_custom_ami() would silently return the newest one -- a build
# quietly using an image nobody selected.


def validate_custom_ami_format(custom_ami: str, refer_to_docs_and_quit: QuitFn) -> None:
    if custom_ami == "UNDEFINED":
        return
    if not re.fullmatch(r"ami-[0-9a-f]{8}([0-9a-f]{9})?", custom_ami):
        refer_to_docs_and_quit('"' + custom_ami + '" is not a valid AMI id! Expected the form ami-0123456789abcdef0 (no wildcards).')


def validate_iam_name_lengths(iam_name_prefix: str, instance_name: str, refer_to_docs_and_quit: QuitFn) -> None:
    budget = _IAM_ROLE_NAME_MAX - _SERIAL_AND_SEPARATORS_LENGTH
    if len(iam_name_prefix) + len(instance_name) > budget:
        refer_to_docs_and_quit(
            "instance_name and iam_name_prefix are too long together: they generate an IAM role name of "
            + str(len(iam_name_prefix) + len(instance_name) + _SERIAL_AND_SEPARATORS_LENGTH)
            + " characters, and AWS allows "
            + str(_IAM_ROLE_NAME_MAX)
            + ".\n\nShorten one of them so that len(instance_name) + len(iam_name_prefix) <= "
            + str(budget)
            + " (currently "
            + str(len(instance_name))
            + " + "
            + str(len(iam_name_prefix))
            + ")."
        )


def validate_vpc_name_format(vpc_name: str, vpc_id: str, refer_to_docs_and_quit: QuitFn) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", vpc_name):
        refer_to_docs_and_quit(
            "The Name tag on "
            + vpc_id
            + ' is "'
            + vpc_name
            + '", which cannot be used as a Terraform provider alias.\n\n'
            + "It must start with a letter or underscore, contain only letters, numbers, underscores and hyphens, "
            + "and be no longer than 64 characters. Retag the VPC and retry."
        )


def validate_ec2_keypair_format(ec2_keypair: str, refer_to_docs_and_quit: QuitFn) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", ec2_keypair):
        refer_to_docs_and_quit("ec2_keypair must start with a letter or number, contain only letters, numbers, periods, underscores, and hyphens, and be no longer than 255 characters!")


# Function: validate_iam_name_prefix_format()
# Purpose: restrict iam_name_prefix to IAM's own name charset.
#
# iam_name_prefix is string-substituted into the staged IAM policy
# document by modify_iam_policy_document() (aux_data.py) via a raw
# str.replace() of the <EC2_IAM_PREFIX> placeholder. With
# ExtendedEc2InstancePolicy.json -- whose IAMRestricted statement scopes
# to "role/<EC2_IAM_PREFIX>-*", "policy/<EC2_IAM_PREFIX>-*" and
# "instance-profile/<EC2_IAM_PREFIX>-*" -- an unvalidated prefix of "*"
# widens those ARNs to very nearly every IAM entity in the account,
# exactly inverting the flag's documented purpose of letting DevOps teams
# *scope* permissions. A prefix containing a double quote instead produces
# invalid JSON and fails at put_role_policy, after the security group,
# keypair and log group already exist.
#
# IAM's documented charset for role/policy/instance-profile names is
# [\w+=,.@-], all of which is inert both in a JSON string and in the
# unquoted shell positions kill_instance.j2 uses. "*" is not in it.


def validate_iam_name_prefix_format(iam_name_prefix: str, refer_to_docs_and_quit: QuitFn) -> None:
    if not re.fullmatch(r"[\w+=,.@-]{1,64}", iam_name_prefix):
        refer_to_docs_and_quit("iam_name_prefix must contain only letters, numbers, and the characters _+=,.@- and be between 1 and 64 characters long!")


# Function: get_terraform_version()
# Purpose: get the installed Terraform version, aborting with a clear
# message if Terraform is missing. Replaces the original
# `subprocess.check_output("terraform -version | head -1 | awk '{print
# $2}'", shell=True, ...)` pipeline -- list-form `terraform -version` with
# the version token parsed in Python needs no shell=True and no dependency
# on head/awk being on PATH too. A missing `terraform` binary now raises a
# catchable FileNotFoundError instead of silently producing empty output
# for the original code's `if not TERRAFORM_VERSION:` check to notice.


def get_terraform_version(refer_to_docs_and_quit: QuitFn, run: Callable[..., Any] | None = None) -> str:
    import subprocess

    if run is None:
        run = subprocess.run
    try:
        result = run(["terraform", "-version"], capture_output=True, text=True)
    except FileNotFoundError:
        refer_to_docs_and_quit("Terraform is missing! Please visit: https://www.terraform.io/downloads")
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    parts = first_line.split()
    if len(parts) < 2:
        refer_to_docs_and_quit("Terraform is missing! Please visit: https://www.terraform.io/downloads")
    return parts[1]


# Function: create_aws_clients()
# Purpose: construct every boto3 client/resource make_instance.py needs in
# one place -- a single seam for tests to mock instead of patching
# boto3.client globally with per-service dispatch logic. Constructing a
# boto3 client/resource performs no network I/O by itself (that only
# happens when a method on it is actually called), so bundling every
# client's construction here, ahead of where each was previously created
# piecemeal through the build flow, changes nothing observable.


@dataclass
class AwsClients:
    ec2_client: EC2Client
    ec2: EC2ServiceResource
    iam: IAMClient
    sns_client: SNSClient
    stsclient: STSClient
    logs_client: CloudWatchLogsClient


def create_aws_clients(region: str, boto3_client: Callable[..., Any] = boto3.client, boto3_resource: Callable[..., Any] = boto3.resource) -> AwsClients:
    return AwsClients(
        ec2_client=boto3_client("ec2", region_name=region),
        ec2=boto3_resource("ec2", region_name=region),
        iam=boto3_client("iam"),
        sns_client=boto3_client("sns", region_name=region),
        stsclient=boto3_client("sts", region_name=region, endpoint_url="https://sts." + region + ".amazonaws.com"),
        logs_client=boto3_client("logs", region_name=region),
    )


# Function: ensure_state_directories()
# Purpose: idempotently create the three top-level local state directories
# this tool writes into.


def ensure_state_directories(instance_data_dir: str) -> None:
    for directory in ("./vars_files", instance_data_dir, "./active_instances"):
        try:
            os.makedirs(directory)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise


# Function: instance_lock()
# Purpose: hold an exclusive, non-blocking OS-level lock (fcntl.flock) on
# ./active_instances/<instance_name>.lock for the duration of a build or
# teardown, so two overlapping operations against the same instance_name
# (make_instance.py run_build() and manage_instance.py
# terminate_via_kill_script(), whether both invoked from the CLI, both
# from mcp_server.py, or one of each) fail fast with a clear message
# instead of racing on instance_data_dir/vars_files/active_instances
# state. This also closes a pre-existing TOCTOU gap in
# abort_if_vars_file_exists() above: that check and the state-directory
# creation that follows it were never atomic with each other, so two
# concurrent builds of the same instance_name could both pass the
# "doesn't exist yet" check before either created it. flock's exclusivity
# is per open file description, not per process, so this works whether
# the two operations are two separate CLI invocations (two processes) or
# two tool calls handled by the same long-running mcp_server.py process.
# POSIX-only (fcntl) -- consistent with this project's stated OSX/Linux
# support; Windows was never supported.


@contextlib.contextmanager
def instance_lock(instance_name: str, refer_to_docs_and_quit: QuitFn) -> Iterator[None]:
    os.makedirs("./active_instances", exist_ok=True)
    lock_path = "./active_instances/" + instance_name + ".lock"
    # 0o644, not the 0o755 a default umask produces for a mode-less
    # os.open() -- a lock file is not an executable.
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                refer_to_docs_and_quit('Another build or teardown is already in progress for "' + instance_name + '" (lock held on ' + lock_path + "). Wait for it to finish and try again.")
            raise
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# Function: abort_if_vars_file_exists()
# Purpose: refuse to proceed if vars_file_path already exists -- an
# existing vars_file means this instance_name was already built, and
# silently building over it would corrupt local state. Prints the exact
# commands an operator needs to clear it and retry. Exits directly via
# sys.exit(1) (matching setup_keypair()'s precedent above) rather than
# refer_to_docs_and_quit, since this bespoke multi-line guidance message
# predates that helper and shouldn't be wrapped in its generic boilerplate.


def abort_if_vars_file_exists(vars_file_path: str, argv: list[str], instance_name: str, instance_data_dir: str) -> None:
    if not os.path.isfile(vars_file_path):
        return
    kill_script = "./kill-instance." + instance_name + ".sh"
    print("")
    print("  WARNING  ".center(80, "*"))
    print(("  Found an existing " + vars_file_path + " ").center(80, "-"))
    print("")
    # This used to unconditionally advise "rm <vars_file>" and rerun, which
    # is the single most expensive piece of advice in the toolkit: a rerun
    # mints a *new* instance_serial_number, so it creates a new security
    # group, keypair, IAM role/policy/profile, SNS topic and log group under
    # new names -- and re-renders kill-instance.<name>.sh over the old one,
    # which referenced the previous attempt's. Those earlier resources then
    # exist in AWS with nothing on disk referencing them: no generated
    # script can find them, and they keep costing money.
    if os.path.exists(kill_script) or os.path.isdir(instance_data_dir):
        print('A previous build of "' + instance_name + '" left resources behind.')
        print("Tear those down first, then retry:")
        print("")
        print("\t" + kill_script)
        print("\t" + " ".join(argv))
        print("")
        print("Do NOT just delete " + vars_file_path + " and rebuild. The rebuild")
        print("generates a new instance_serial_number and overwrites " + kill_script + ",")
        print("which leaves the previous attempt's security group, IAM role, SNS topic")
        print("and CloudWatch log group with nothing tracking them -- still running,")
        print("still billable, and no longer reachable by any generated script.")
    else:
        print("No teardown script was generated for this build, so there is nothing")
        print("to tear down. Remove the stale vars_file and retry:")
        print("")
        print("\trm " + vars_file_path)
        print("\t" + " ".join(argv))
    print("")
    print("Aborting...")
    sys.exit(1)


# Function: write_serial_number_file()
# Purpose: write the initial serial-number tracking file for a new
# instance_name, recording the exact command line used to build it (read
# back later by templates/build_ami.j2's "relaunch with this AMI"
# guidance). Only writes once per instance_name -- a no-op on any rebuild
# attempt that got this far (which normally can't happen, since
# abort_if_vars_file_exists() already refuses a duplicate build first).
#
# Bug fix during extraction: the original inline code guarded this with
# `if not os.path.isfile(instance_serial_number):` -- checking the serial
# *number string* (e.g. "myinstance-053012092025"), never a real file
# path, instead of `instance_serial_number_file`. That condition was
# always true in practice, making the guard a no-op; fixed here to check
# the actual file path, matching the evident original intent. Also
# switched to explicit `with open(...)` context managers instead of
# relying on garbage collection to close the file handles (same class of
# fix already applied this session to the .pem keypair write).


def write_serial_number_file(instance_serial_number_file: str, instance_name: str, instance_serial_datestamp: str, argv: list[str]) -> None:
    # This used to return early when the file already existed, which left a
    # *previous* failed attempt's datestamp and command line in place while
    # the current build used a freshly-minted serial. build_ami.j2 reads
    # this file, so it would have been reporting the wrong build. The
    # duplicate-build guard and instance_lock() already prevent two live
    # builds of the same instance_name, so overwriting is safe here.
    with open(instance_serial_number_file, "w") as fh:
        print(f"{instance_name}.{instance_serial_datestamp}", file=fh)
    with open(instance_serial_number_file, "a") as fh:
        print(" ".join(argv), file=fh)


# Function: resolve_ebs_optimized_support()
# Purpose: downgrade ebs_optimized to "false" with a console warning if the
# selected instance_type doesn't support it, rather than letting Terraform
# fail later with an opaque AWS error. A no-op (returns ebs_optimized
# unchanged) if ebs_optimized was already "false".


def resolve_ebs_optimized_support(ebs_optimized: BoolStr, instance_type: str, ebs_optimized_support: str, instance_name: str) -> BoolStr:
    if ebs_optimized != "true":
        return ebs_optimized
    if ebs_optimized_support == "unsupported":
        print("")
        print("*** WARNING ***")
        print(instance_type + " does not support EBS optimization!")
        print("Disabling ebs_optimization for: " + instance_name)
        return "false"
    print("")
    print("EBS optimization: Enabled")
    return ebs_optimized


# Function: resolve_request_type_pricing()
# Purpose: for ondemand, print the "spot is cheaper" reminder and return
# sentinel UNDEFINED pricing values. For spot, look up and buffer the
# current Spot price. fetch_spot_price_raw/compute_buffered_spot_price are
# dependency-injected (already-tested functions of the same name above).


def resolve_request_type_pricing(
    request_type: Literal["ondemand", "spot"],
    ec2_client: EC2Client,
    instance_type: str,
    is_windows: bool,
    az: str,
    spot_buffer: float,
    debug_mode: BoolStr,
    fetch_spot_price_raw: Callable[[EC2Client, str, bool, str, QuitFn], float],
    compute_buffered_spot_price: Callable[[float, float], float],
    p_val: Callable[[str, str], None],
    refer_to_docs_and_quit: QuitFn,
) -> tuple[str, str] | tuple[float, float]:
    if request_type == "ondemand":
        print("")
        print("Selected: ondemand (NOTE: spot instances are **MUCH** cheaper!)")
        return "UNDEFINED", "UNDEFINED"
    spot_price_raw = fetch_spot_price_raw(ec2_client, instance_type, is_windows, az, refer_to_docs_and_quit)
    spot_price = compute_buffered_spot_price(spot_price_raw, spot_buffer)
    p_val("spot_price_raw", debug_mode)
    p_val("spot_price_buffer", debug_mode)
    p_val("spot_price", debug_mode)
    print("")
    print("Setting spot_price: $" + str(spot_price) + "/hr")
    return spot_price, spot_buffer


# Function: resolve_placement_group_strategy()
# Purpose: validate and enable the EC2 placement group, or reset
# placement_group_strategy to the UNDEFINED sentinel when disabled. Aborts
# if a single instance requests a placement group (Terraform doesn't
# support a one-member "cluster").


def resolve_placement_group_strategy(
    enable_placement_group: BoolStr,
    count: int,
    instance_type: str,
    placement_group_strategy: str,
    instance_type_info: dict[str, Any],
    ec2_placement_group_check: Callable[[str, str, list[str], BoolStr], None],
    refer_to_docs_and_quit: QuitFn,
    debug_mode: BoolStr,
    p_val: Callable[[str, str], None],
) -> str:
    if enable_placement_group != "true":
        return "UNDEFINED"
    if count == 1:
        refer_to_docs_and_quit("Using placement groups requires deploying more than a single instance!")
    ec2_placement_group_check(instance_type, placement_group_strategy, instance_type_info["placement_group_strategies"], debug_mode)
    print("Enabling: EC2 Placement Group")
    print("Strategy: " + placement_group_strategy)
    print("")
    if debug_mode == "true":
        p_val("placement_group_strategy", debug_mode)
    return placement_group_strategy


# Function: create_sns_topic_and_subscribe()
# Purpose: create a unique SNS topic for this instance(s)'s build/teardown
# notifications and subscribe instance_owner_email to it.


def create_sns_topic_and_subscribe(sns_client: SNSClient, instance_serial_number: str, instance_owner_email: str) -> tuple[str, str]:
    sns_topic_name = "Ec2_Instance_SNS_Alerts_" + str(instance_serial_number)
    sns_topic = sns_client.create_topic(Name=sns_topic_name)
    sns_topic_arn = sns_topic["TopicArn"]
    sns_client.subscribe(TopicArn=sns_topic_arn, Protocol="email", Endpoint=instance_owner_email)
    return sns_topic_name, sns_topic_arn


# Function: publish_sns_notification()
# Purpose: publish the build-completion notification to the SNS topic.


def publish_sns_notification(sns_client: SNSClient, sns_topic_arn: str, sns_message_body: str, sns_instance_subject: str) -> None:
    sns_client.publish(TopicArn=sns_topic_arn, Message=sns_message_body, Subject=sns_instance_subject)


# Function: build_windows_password_table()
# Purpose: build the printable Name/IP/Administrator-password table for
# newly-created Windows instance(s). Uses an in-memory CSV (io.StringIO)
# rather than a real temp file on disk -- prettytable.from_csv only needs
# a file-like object, so there's no reason to touch the filesystem, and no
# leftover temp file to clean up if something raises partway through.


def build_windows_password_table(
    instance_data_dir: str,
    ec2_keypair: str,
    fetch_windows_instance_details: Callable[[str], tuple[str, str, str]],
    decrypt_windows_admin_passwords: Callable[[str, str, str], str],
) -> Any:
    import io

    from prettytable import from_csv

    windows_instance_id, windows_instance_name, windows_ip_address = fetch_windows_instance_details(instance_data_dir)
    windows_administrator_password = decrypt_windows_admin_passwords(instance_data_dir, ec2_keypair, windows_instance_id)

    csv_buffer = io.StringIO()
    csv_buffer.write("Instance Name,IP Address,Administrator Password\n")
    for name, ip_address, password in zip(windows_instance_name.split(","), windows_ip_address.split(","), windows_administrator_password.split(","), strict=True):
        csv_buffer.write(name + "," + ip_address + "," + password + "\n")
    csv_buffer.seek(0)
    return from_csv(csv_buffer)
