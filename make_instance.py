#!/usr/bin/env python3
#
################################################################################
# Name:         make_instance.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 3, 2019
# Last Changed: September 28, 2019
# Purpose:      Generic command-line EC2 instance creator
################################################################################

# Load the required Python libraries.

import argparse
import dataclasses
import functools
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from math import pi
from typing import Literal, NoReturn

import boto3

# Import some external lists and functions.
# Source: aux_data.py
from aux_data import (
    add_inbound_security_group_rule,
    base_os_instance_check,
    check_custom_ami,
    ctrlC_Abort,
    ebs_encryption_check,
    ec2_placement_group_check,
    get_ami_info,
    get_base_os_family,
    get_instance_type_info,
    illegal_az_msg,
    log_retention_days_check,
    modify_iam_policy_document,
    p_fail,
    p_val,
    print_TextHeader,
    refer_to_docs_and_quit,
)
from instance_builder import (
    AwsClients,
    InstanceParameters,
    abort_if_vars_file_exists,
    apply_terraform,
    build_security_group_tags,
    build_sns_message,
    build_windows_password_table,
    compute_buffered_spot_price,
    create_aws_clients,
    create_sns_topic_and_subscribe,
    decrypt_windows_admin_passwords,
    ensure_state_directories,
    fetch_spot_price_raw,
    fetch_windows_instance_details,
    generate_instance_serial_number,
    generate_sns_timestamps,
    get_terraform_version,
    instance_lock,
    publish_sns_notification,
    resolve_ami,
    resolve_custom_user_scripts,
    resolve_ebs_optimized_support,
    resolve_placement_group_strategy,
    resolve_request_type_pricing,
    resolve_security_group,
    resolve_ssh_allowed_ips,
    resolve_vpc_and_subnet,
    setup_cloudwatch_logging,
    setup_iam,
    setup_keypair,
    validate_and_resize_ebs_volumes,
    validate_az_and_region,
    validate_instance_name_and_owner_format,
    write_serial_number_file,
    write_vars_file,
)
from template_engine import render_instance_templates

# Type aliases used throughout this module's signatures -- same duplicated
# convention as instance_builder.py/aux_data.py/manage_instance.py/
# access_instance.py (see the comment there for why they're not shared via
# import).
BoolStr = Literal["true", "false"]
QuitFn = Callable[[str], NoReturn]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="make_instance.py: Command-line interface to build EC2 instances")

    # Configure parser arguments for the required variables.

    parser.add_argument("--az", "-A", help="AWS Availability Zone (REQUIRED)", required=True)
    parser.add_argument("--instance_name", "-N", help="name of the instance(s) (REQUIRED)", required=True)
    parser.add_argument("--instance_owner", "-O", help="ActiveDirectory username of the instance_owner (REQUIRED)", required=True)
    parser.add_argument("--instance_owner_email", "-E", help="Email address of the instance_owner (REQUIRED)", required=True)

    # Parse values for the optional parameters from the commnand linue.

    parser.add_argument(
        "--base_os",
        "-B",
        choices=["al2023", "alinux2", "alma9", "alma10", "rhel9", "rhel10", "rocky9", "rocky10", "ubuntu2404", "ubuntu2604", "windows2019", "windows2022", "windows2025"],
        help="instance base operating system (default = al2023 a.k.a. Amazon Linux 2023)",
        required=False,
        default="al2023",
    )
    parser.add_argument("--count", "-C", help="number of EC2 instances to create (default = 1)", type=int, required=False, default=1)
    parser.add_argument("--custom_ami", help="ami-id of a custom Amazon Machine Image (default = UNDEFINED)", required=False, default="UNDEFINED")
    parser.add_argument(
        "--custom_user_scripts",
        help="comma-separated list of custom_user_scripts/ names to run (default = default); each name needs custom_user_prelogin_script.j2_<name> and/or custom_user_postboot_script.j2_<name> to exist",
        required=False,
        default="default",
    )
    parser.add_argument("--debug_mode", "-D", choices=["true", "false"], help="Enable debug mode (default = false)", required=False, default="false")
    parser.add_argument("--ebs_encryption", choices=["true", "false"], help="enable EBS encryption where possible (default = false)", required=False, default="false")
    parser.add_argument("--ebs_optimized", choices=["true", "false"], help="use optimized EBS volumes (default = yes)", required=False, default="true")
    parser.add_argument("--ebs_root_volume_iops", help="amount of provisioned IOPS for the EBS root volume when ebs_root_volume_type=io1 (default = 0)", required=False, type=int, default=0)
    parser.add_argument("--ebs_root_volume_size", help="EBS volume size in GB (Linux default = 8, Windows default = 30)", required=False, type=int, default=8)
    parser.add_argument("--ebs_root_volume_type", choices=["gp2", "io1", "st1"], help="EBS volume type (default = gp2)", required=False, default="gp2")
    parser.add_argument("--ebs_device_volume_iops", help="amount of provisioned IOPS for the EBS secondary volume when ebs_root_volume_type=io1 (default = 0)", required=False, type=int, default=0)
    parser.add_argument("--ebs_device_volume_size", help="Secondary EBS volume size in GB (Linux default = 8, Windows default = 30)", required=False, type=int, default=8)
    parser.add_argument("--ebs_device_volume_type", choices=["gp2", "io1", "st1"], help="EBS secondary volume type (default = gp2)", required=False, default="gp2")
    parser.add_argument("--ec2_keypair", help="define an EC2 key pair name to provide SSH or Remote Desktop access (default = ec2_keypair_default)", required=False, default="ec2_keypair_default")
    parser.add_argument(
        "--enable_placement_group",
        "--enable_pg",
        choices=["true", "false"],
        help='Place the new instances in an EC2 placement group using the "cluster" strategy (default = false)',
        required=False,
        default="false",
    )
    parser.add_argument("--hyperthreading", "-H", choices=["true", "false"], help="enable Intel Hyperthreading (default = true)", required=False, default="true")
    parser.add_argument(
        "--iam_json_policy",
        "-J",
        choices=["MinimalEc2InstancePolicy.json", "GenericEc2InstancePolicy.json", "ExtendedEc2InstancePolicy.json"],
        help="Use a pre-existing JSON policy document in the /templates subdirectory to set permissions for iam_role (default = GenericEc2InstancePolicy.json",
        required=False,
        default="GenericEc2InstancePolicy.json",
    )
    parser.add_argument("--iam_name_prefix", help="Provide a prefix for the IAM entities associated with the instance (default = Ec2InstanceMaker)", required=False, default="Ec2InstanceMaker")
    parser.add_argument("--iam_role", help="Apply a pre-existing IAM role to the instance(s)", required=False, default="UNDEFINED")
    parser.add_argument(
        "--instance_owner_department",
        help="Department of the instance_owner (default = compbio)",
        required=False,
        default="compbio",
    )
    parser.add_argument("--request_type", choices=["ondemand", "spot"], help="choose between ondemand or spot instances (default = ondemand)", required=False, default="ondemand")
    parser.add_argument(
        "--instance_type",
        "-T",
        help="EC2 instance type (default = t2.micro); CPU architecture (x86_64 or Graviton/ARM64) is auto-detected, no separate flag needed",
        required=False,
        default="t2.micro",
    )
    parser.add_argument("--prod_level", choices=["dev", "test", "stage", "prod"], help="Operating stage of the jumphost  (default = dev)", required=False, default="dev")
    parser.add_argument(
        "--enable_cloudwatch_logs",
        choices=["true", "false"],
        help="Install and configure the CloudWatch Agent on the instance(s) to ship logs to CloudWatch Logs (default = true)",
        required=False,
        default="true",
    )
    parser.add_argument(
        "--log_retention_days",
        type=int,
        help="Number of days to retain CloudWatch Logs for the instance(s) (default = 30)",
        required=False,
        default=30,
    )
    parser.add_argument(
        "--placement_group_strategy", "--pg_strategy", choices=["cluster", "spread"], help="Designate an EC2 placement group strategy (default = cluster)", required=False, default="cluster"
    )
    parser.add_argument("--preserve_ami", choices=["true", "false"], help="Preserve any AMI image built from the instance(s) post-termination (default = true)", required=False, default="true")
    parser.add_argument(
        "--preserve_cloudwatch_logs",
        choices=["true", "false"],
        help="Preserve the CloudWatch Logs group when the instance(s) are terminated (default = false)",
        required=False,
        default="false",
    )
    parser.add_argument("--project_id", "-P", help="Project name or ID number (default = UNDEFINED)", required=False, default="UNDEFINED")
    parser.add_argument("--public_ip", "-p", help="Attach a public IP address to the instance(s) (default = true)", required=False, default="true")
    parser.add_argument("--security_group", "-S", help="Primary security group name for the EC2 instance (default = ec2instancemaker_sg)", required=False, default="ec2instancemaker_sg")
    parser.add_argument(
        "--spot_buffer", help="pricing buffer to protect from Spot market fluctuations: spot_price = spot_price + spot_price*spot_buffer", type=float, required=False, default=round((1 / pi), 8)
    )
    parser.add_argument(
        "--ssh_allowed_ips",
        help="CIDR block allowed to reach the instance's SSH/RDP port (default = the CIDR of the instance's own VPC). Never accepts 0.0.0.0/0.",
        required=False,
        default="UNDEFINED",
    )
    parser.add_argument("--turbot_account", help="Turbot account ID (default = DISABLED)", required=False, default="DISABLED")
    parser.add_argument("--vpc_name", help="Name of the VPC (default = vpc_default)", required=False, default="vpc_default")

    return parser.parse_args(argv)


# Function: print_debug_parameters()
# Purpose: print the current values of all defined instance_parameters to
# the console when --debug_mode=true. Reads a single typed
# InstanceParameters instance instead of ~50 separate local variables --
# was inline in main() before instance_parameters became a dataclass (see
# CLAUDE-STATE.md), where a signature this wide would've been unreadable.


def print_debug_parameters(params: InstanceParameters) -> None:
    print_TextHeader(params.instance_name, "Printing", 80)
    print("aws_account_id = " + params.aws_account_id)
    if params.turbot_account != "DISABLED":
        print("turbot_account = " + params.turbot_account)
    print("aws_ami = " + str(params.aws_ami))
    print("az = " + params.az)
    print("base_os = " + params.base_os)
    if params.count > 1:
        print("count = " + str(params.count))
    print("ebs_encryption = " + str(params.ebs_encryption))
    print("ebs_optimized = " + str(params.ebs_optimized))
    print("ebs_root_volume_size = " + str(params.ebs_root_volume_size))
    print("ebs_root_volume_type = " + params.ebs_root_volume_type)
    print("ebs_root_volume_iops = " + str(params.ebs_root_volume_iops))
    print("ebs_device_volume_size = " + str(params.ebs_device_volume_size))
    print("ebs_device_volume_type = " + params.ebs_device_volume_type)
    print("ebs_device_volume_iops = " + str(params.ebs_device_volume_iops))
    print("instance_type = " + params.instance_type)
    print("architecture = " + params.architecture)
    print("ec2_keypair = " + params.ec2_keypair)
    print("ec2_user = " + params.ec2_user)
    print("ec2_user_home = " + params.ec2_user_home)
    if params.enable_placement_group == "true":
        print("enable_placement_group = " + params.enable_placement_group)
        print("placement_group_strategy = " + params.placement_group_strategy)
    print("hyperthreading = " + params.hyperthreading)
    print("instance_name = " + params.instance_name)
    print("instance_owner = " + params.instance_owner)
    print("instance_owner_email = " + params.instance_owner_email)
    print("instance_owner_department = " + params.instance_owner_department)
    print("instance_serial_number = " + params.instance_serial_number)
    print("instance_serial_number_file = " + params.instance_serial_number_file)
    print("request_type = " + params.request_type)
    print("preserve_ami = " + params.preserve_ami)
    print("prod_devel = " + params.prod_level)
    if params.project_id != "UNDEFINED":
        print("project_id = " + params.project_id)
    if params.ec2_iam_instance_profile:
        print("preserve_iam_role = " + params.preserve_iam_role)
        if "UNDEFINED" not in params.ec2_iam_instance_policy:
            print("ec2_iam_instance_policy = " + params.ec2_iam_instance_policy)
        print("ec2_iam_instance_profile = " + params.ec2_iam_instance_profile)
        print("ec2_iam_instance_role = " + params.ec2_iam_instance_role)
    print("public_ip = " + params.public_ip)
    print("region = " + params.region)
    print("security_group_name = " + str(params.security_group_name))
    print("spot_price = " + str(params.spot_price))
    print("subnet_id = " + params.subnet_id)
    print("vars_file_path = " + params.vars_file_path)
    print("vpc_id = " + params.vpc_id)
    print("vpc_name = " + params.vpc_name)
    print("vpc_security_group_ids = " + params.vpc_security_group_ids)
    print("sns_topic_arn = " + params.sns_topic_arn)
    print("DEPLOYMENT_DATE = " + params.DEPLOYMENT_DATE)
    print("TERRAFORM_VERSION = " + params.TERRAFORM_VERSION)


################################################################################
# main()'s build flow, phased.
#
# main() used to be one ~650-line linear sequence threading ~50 local
# variables through every step. Every AWS/Terraform-touching function it
# calls already lives in instance_builder.py/aux_data.py, fully typed and
# independently tested (tests/test_instance_builder.py) -- what was left to
# clean up here was main()'s own orchestration: too many independent
# variables in play at once to read as a sequence of named steps.
#
# The 5 phase functions below stay in make_instance.py itself (NOT
# instance_builder.py) -- this is required, not a style choice:
# tests/test_make_instance_integration.py monkeypatches individual
# AWS-touching functions via monkeypatch.setattr(make_instance,
# "resolve_vpc_and_subnet", ...), which only intercepts calls made via
# attribute lookup on this module's own namespace. A phase function calling
# resolve_vpc_and_subnet(...) from inside instance_builder.py would bypass
# that patched attribute entirely and silently stop being covered by the
# existing tests.
#
# BuildSettings bundles every value that's established once from argparse
# (or derived once, early) and never reassigned afterward -- the "who,
# where, what" of one build, threaded unchanged through all 5 phases.
# EbsRequest/BuildOptions bundle smaller, related CLI-input clusters the
# same way. Each phase's own *_Resolution dataclass carries only the new
# values that phase actually resolves via AWS calls (or leaves reassigned,
# e.g. placement_group_strategy) -- passed whole to the next phase that
# needs them, instead of unpacked field-by-field.
################################################################################


@dataclass
class BuildSettings:
    instance_name: str
    instance_serial_number: str
    instance_serial_number_file: str
    instance_data_dir: str
    vars_file_path: str
    region: str
    az: str
    debug_mode: BoolStr
    instance_owner: str
    instance_owner_email: str
    instance_owner_department: str
    instance_type: str
    base_os: str
    count: int
    request_type: Literal["ondemand", "spot"]
    enable_placement_group: BoolStr
    enable_cloudwatch_logs: BoolStr
    log_retention_days: int
    iam_name_prefix: str
    turbot_account: str
    DEPLOYMENT_DATE: str
    DEPLOYMENT_DATE_TAG: str
    TERRAFORM_VERSION: str


@dataclass
class EbsRequest:
    encryption: BoolStr
    optimized: BoolStr
    root_volume_size: int
    root_volume_type: str
    root_volume_iops: int
    device_volume_size: int
    device_volume_type: str
    device_volume_iops: int


@dataclass
class BuildOptions:
    preserve_ami: BoolStr
    preserve_cloudwatch_logs: BoolStr
    hyperthreading: BoolStr
    prod_level: Literal["dev", "test", "stage", "prod"]
    project_id: str
    public_ip: BoolStr


@dataclass
class NetworkAndComputeResolution:
    architecture: str
    is_windows: bool
    package_manager: str | None
    awscli_preinstalled: bool | None
    ec2_user: str
    ebs_optimized: BoolStr
    ebs_root_volume_size: int
    ebs_device_volume_size: int
    spot_price: str | float
    placement_group_strategy: str


@dataclass
class VpcSecurityAndKeypairResolution:
    aws_account_id: str
    vpc_id: str
    vpc_name: str
    subnet_id: str
    ssh_allowed_ips: str
    security_group_name: str
    vpc_security_group_ids: str
    ec2_user_home: str
    aws_ami: str
    ec2_keypair: str


@dataclass
class IamSnsAndLoggingResolution:
    cloudwatch_log_group: str
    ec2_iam_instance_role: str
    ec2_iam_instance_policy: str
    ec2_iam_instance_profile: str
    preserve_iam_role: BoolStr
    sns_topic_arn: str
    sns_datestamp: str
    sns_timestamp: str


# BuildReport is run_build()'s return value -- the same information
# report_and_notify() prints to the console for a CLI operator, structured
# for a programmatic caller (mcp_server.py's build_instance tool) instead.


@dataclass
class BuildReport:
    instance_name: str
    count: int
    is_windows: bool
    access_command: str | None
    windows_password_table: str | None
    kill_script: str
    build_ami_script: str
    sns_topic_arn: str


# Function: resolve_network_and_compute()
# Purpose: phase 1 -- AZ/region validation, instance_type_info, base_os
# checks/family, EBS optimize/encrypt/resize, spot pricing, placement
# group strategy.


def resolve_network_and_compute(
    settings: BuildSettings,
    aws_clients: AwsClients,
    ebs: EbsRequest,
    spot_buffer: float,
    placement_group_strategy: str,
    refer_to_docs_and_quit: QuitFn,
) -> NetworkAndComputeResolution:
    ec2_client = aws_clients.ec2_client
    debug_mode = settings.debug_mode

    validate_az_and_region(ec2_client, settings.az, illegal_az_msg)
    p_val("region", debug_mode)
    p_val("az", debug_mode)

    instance_type_info = get_instance_type_info(ec2_client, settings.instance_type)
    if instance_type_info is None:
        p_fail(settings.instance_type, "instance_type", "missing_element")
    architecture = instance_type_info["architecture"]
    print("")
    print("Selected EC2 instance type: " + settings.instance_type + " (" + architecture + ")")

    print("")
    print("Selected base operating system: " + settings.base_os)
    base_os_instance_check(settings.base_os, settings.instance_type, architecture, debug_mode)

    base_os_family = get_base_os_family(settings.base_os)
    is_windows = base_os_family["is_windows"]
    package_manager = base_os_family["package_manager"]
    awscli_preinstalled = base_os_family["awscli_preinstalled"]
    ec2_user = base_os_family["ec2_user"]

    ebs_optimized = resolve_ebs_optimized_support(ebs.optimized, settings.instance_type, instance_type_info["ebs_optimized_support"], settings.instance_name)
    p_val("ebs_optimized", debug_mode)

    if ebs.encryption == "true":
        ebs_encryption_check(settings.instance_type, instance_type_info["ebs_encryption_support"], settings.instance_name, debug_mode)

    ebs_root_volume_size, ebs_device_volume_size = validate_and_resize_ebs_volumes(
        ebs.root_volume_size,
        ebs.device_volume_size,
        ebs.root_volume_type,
        ebs.device_volume_type,
        ebs.root_volume_iops,
        ebs.device_volume_iops,
        is_windows,
        refer_to_docs_and_quit,
    )

    # The buffered spot_buffer this returns alongside spot_price was never
    # consumed again after this point in the original code either (it's not
    # an InstanceParameters field) -- discarded here too, not a behavior
    # change.
    spot_price, _ = resolve_request_type_pricing(
        settings.request_type, ec2_client, settings.instance_type, is_windows, settings.az, spot_buffer, debug_mode, fetch_spot_price_raw, compute_buffered_spot_price, p_val
    )
    print("")

    placement_group_strategy = resolve_placement_group_strategy(
        settings.enable_placement_group,
        settings.count,
        settings.instance_type,
        placement_group_strategy,
        instance_type_info,
        ec2_placement_group_check,
        refer_to_docs_and_quit,
        debug_mode,
        p_val,
    )

    return NetworkAndComputeResolution(
        architecture=architecture,
        is_windows=is_windows,
        package_manager=package_manager,
        awscli_preinstalled=awscli_preinstalled,
        ec2_user=ec2_user,
        ebs_optimized=ebs_optimized,
        ebs_root_volume_size=ebs_root_volume_size,
        ebs_device_volume_size=ebs_device_volume_size,
        spot_price=spot_price,
        placement_group_strategy=placement_group_strategy,
    )


# Function: resolve_vpc_security_and_keypair()
# Purpose: phase 2 -- VPC/subnet, ssh_allowed_ips, security group, ec2_user
# home directory, AMI, keypair.


def resolve_vpc_security_and_keypair(
    settings: BuildSettings,
    aws_clients: AwsClients,
    network: NetworkAndComputeResolution,
    vpc_name: str,
    security_group: str,
    ssh_allowed_ips: str,
    custom_ami: str,
    ec2_keypair: str,
    refer_to_docs_and_quit: QuitFn,
) -> VpcSecurityAndKeypairResolution:
    ec2_client = aws_clients.ec2_client
    ec2 = aws_clients.ec2
    debug_mode = settings.debug_mode

    aws_account_id = aws_clients.stsclient.get_caller_identity()["Account"]

    vpc_id, vpc_name, subnet_id = resolve_vpc_and_subnet(ec2_client, vpc_name, settings.az, refer_to_docs_and_quit)
    p_val("vpc_name", debug_mode)
    p_val("subnet_id", debug_mode)

    ssh_allowed_ips = resolve_ssh_allowed_ips(ec2_client, vpc_id, ssh_allowed_ips, refer_to_docs_and_quit)
    p_val("ssh_allowed_ips", debug_mode)

    security_group_name, vpc_security_group_ids = resolve_security_group(
        ec2, settings.region, security_group, settings.instance_serial_number, vpc_id, network.is_windows, ssh_allowed_ips, add_inbound_security_group_rule
    )
    p_val("security_group", debug_mode)
    p_val("vpc_security_group_ids", debug_mode)

    ec2_user_home = "/home/" + network.ec2_user
    p_val("ec2_user", debug_mode)
    p_val("ec2_user_home", debug_mode)

    aws_ami = resolve_ami(
        custom_ami,
        settings.base_os,
        network.architecture,
        aws_account_id,
        functools.partial(get_ami_info, ec2_client),
        functools.partial(check_custom_ami, ec2_client),
        refer_to_docs_and_quit,
    )
    p_val("aws_ami", debug_mode)

    if ec2_keypair == "ec2_keypair_default":
        ec2_keypair = settings.instance_serial_number + "_" + settings.region

    secret_key_file = settings.instance_data_dir + ec2_keypair + ".pem"
    setup_keypair(ec2_client, ec2_keypair, secret_key_file, settings.region, debug_mode, refer_to_docs_and_quit)
    p_val("ec2_keypair", debug_mode)

    return VpcSecurityAndKeypairResolution(
        aws_account_id=aws_account_id,
        vpc_id=vpc_id,
        vpc_name=vpc_name,
        subnet_id=subnet_id,
        ssh_allowed_ips=ssh_allowed_ips,
        security_group_name=security_group_name,
        vpc_security_group_ids=vpc_security_group_ids,
        ec2_user_home=ec2_user_home,
        aws_ami=aws_ami,
        ec2_keypair=ec2_keypair,
    )


# Function: provision_iam_sns_and_logging()
# Purpose: phase 3 -- CloudWatch log group, IAM role/policy/profile setup,
# Turbot environment variables, SNS topic creation/subscribe/timestamps.


def provision_iam_sns_and_logging(
    settings: BuildSettings,
    aws_clients: AwsClients,
    iam_role: str,
    iam_json_policy: str,
    refer_to_docs_and_quit: QuitFn,
) -> IamSnsAndLoggingResolution:
    debug_mode = settings.debug_mode

    cloudwatch_log_group = "/ec2instancemaker/" + settings.instance_name
    if settings.enable_cloudwatch_logs == "true":
        setup_cloudwatch_logging(aws_clients.logs_client, cloudwatch_log_group, settings.log_retention_days, refer_to_docs_and_quit)

    ec2_iam_instance_role, ec2_iam_instance_policy, ec2_iam_instance_profile, preserve_iam_role = setup_iam(
        aws_clients.iam,
        iam_role,
        settings.iam_name_prefix,
        iam_json_policy,
        settings.instance_data_dir,
        settings.instance_serial_number,
        debug_mode,
        refer_to_docs_and_quit,
        modify_iam_policy_document,
    )
    if debug_mode == "true":
        print("")
        p_val("ec2_iam_instance_role", debug_mode)
        p_val("ec2_iam_instance_profile", debug_mode)

    if settings.turbot_account != "DISABLED":
        turbot_profile = "turbot__" + settings.turbot_account + "__" + settings.instance_owner
        os.environ["AWS_PROFILE"] = turbot_profile
        os.environ["AWS_DEFAULT_REGION"] = settings.region
        boto3.setup_default_session(profile_name=turbot_profile)

    sns_topic_name, sns_topic_arn = create_sns_topic_and_subscribe(aws_clients.sns_client, settings.instance_serial_number, settings.instance_owner_email)
    if debug_mode == "true":
        print("")
        print("Subscribed " + settings.instance_owner_email + " to SNS topic: " + sns_topic_name)
        print("")
    p_val("sns_topic_name", debug_mode)

    sns_datestamp, sns_timestamp = generate_sns_timestamps()

    return IamSnsAndLoggingResolution(
        cloudwatch_log_group=cloudwatch_log_group,
        ec2_iam_instance_role=ec2_iam_instance_role,
        ec2_iam_instance_policy=ec2_iam_instance_policy,
        ec2_iam_instance_profile=ec2_iam_instance_profile,
        preserve_iam_role=preserve_iam_role,
        sns_topic_arn=sns_topic_arn,
        sns_datestamp=sns_datestamp,
        sns_timestamp=sns_timestamp,
    )


# Function: render_and_apply()
# Purpose: phase 4 -- assemble InstanceParameters, print the --debug_mode
# dump, write the vars_file, render the Jinja2 templates, run the
# CTRL-C-abort safety window, apply Terraform, and tag the security group.
# ctrlc_abort_seconds overrides the window's length (default: computed from
# debug_mode, same as always) -- mcp_server.py's build_instance tool passes
# 0, since there's no human at a terminal to type CTRL-C in the first place;
# real safety there comes from the tool's own required confirm=True
# argument instead.


def render_and_apply(
    settings: BuildSettings,
    aws_clients: AwsClients,
    network: NetworkAndComputeResolution,
    vpc: VpcSecurityAndKeypairResolution,
    iam_sns: IamSnsAndLoggingResolution,
    options: BuildOptions,
    ebs: EbsRequest,
    cwd: str,
    instance_data_dir_abs: str,
    custom_user_prelogin_scripts: list[str],
    custom_user_postboot_scripts: list[str],
    refer_to_docs_and_quit: QuitFn,
    ctrlc_abort_seconds: int | None = None,
) -> None:
    debug_mode = settings.debug_mode

    instance_parameters = InstanceParameters(
        architecture=network.architecture,
        awscli_preinstalled=network.awscli_preinstalled,
        az=settings.az,
        aws_ami=vpc.aws_ami,
        aws_account_id=vpc.aws_account_id,
        base_os=settings.base_os,
        is_windows=network.is_windows,
        package_manager=network.package_manager,
        count=settings.count,
        custom_user_prelogin_scripts=custom_user_prelogin_scripts,
        custom_user_postboot_scripts=custom_user_postboot_scripts,
        debug_mode=debug_mode,
        ebs_encryption=ebs.encryption,
        ebs_optimized=network.ebs_optimized,
        ebs_root_volume_size=network.ebs_root_volume_size,
        ebs_root_volume_type=ebs.root_volume_type,
        ebs_root_volume_iops=ebs.root_volume_iops,
        ebs_device_volume_size=network.ebs_device_volume_size,
        ebs_device_volume_type=ebs.device_volume_type,
        ebs_device_volume_iops=ebs.device_volume_iops,
        instance_type=settings.instance_type,
        ec2_keypair=vpc.ec2_keypair,
        ec2_user=network.ec2_user,
        ec2_user_home=vpc.ec2_user_home,
        ec2_iam_instance_policy=iam_sns.ec2_iam_instance_policy,
        ec2_iam_instance_profile=iam_sns.ec2_iam_instance_profile,
        ec2_iam_instance_role=iam_sns.ec2_iam_instance_role,
        enable_placement_group=settings.enable_placement_group,
        hyperthreading=options.hyperthreading,
        iam_name_prefix=settings.iam_name_prefix,
        instance_data_dir=instance_data_dir_abs,
        instance_owner=settings.instance_owner,
        instance_owner_email=settings.instance_owner_email,
        instance_owner_department=settings.instance_owner_department,
        instance_name=settings.instance_name,
        request_type=settings.request_type,
        instance_serial_number=settings.instance_serial_number,
        instance_serial_number_file=settings.instance_serial_number_file,
        cloudwatch_log_group=iam_sns.cloudwatch_log_group,
        enable_cloudwatch_logs=settings.enable_cloudwatch_logs,
        log_retention_days=settings.log_retention_days,
        placement_group_strategy=network.placement_group_strategy,
        preserve_ami=options.preserve_ami,
        preserve_cloudwatch_logs=options.preserve_cloudwatch_logs,
        prod_level=options.prod_level,
        project_id=options.project_id,
        preserve_iam_role=iam_sns.preserve_iam_role,
        public_ip=options.public_ip,
        region=settings.region,
        security_group_name=vpc.security_group_name,
        spot_price=network.spot_price,
        ssh_allowed_ips=vpc.ssh_allowed_ips,
        vpc_security_group_ids=vpc.vpc_security_group_ids,
        sns_topic_arn=iam_sns.sns_topic_arn,
        sns_datestamp=iam_sns.sns_datestamp,
        sns_timestamp=iam_sns.sns_timestamp,
        subnet_id=vpc.subnet_id,
        turbot_account=settings.turbot_account,
        vars_file_path=settings.vars_file_path,
        vpc_id=vpc.vpc_id,
        vpc_name=vpc.vpc_name,
        DEPLOYMENT_DATE=settings.DEPLOYMENT_DATE,
        DEPLOYMENT_DATE_TAG=settings.DEPLOYMENT_DATE_TAG,
        TERRAFORM_VERSION=settings.TERRAFORM_VERSION,
    )

    if debug_mode == "true":
        print_debug_parameters(instance_parameters)

    vars_file_main_part = """\
################################################################################
# Name:    	{instance_name}.yml
# Author:  	Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 3, 2019
# Last Changed: July 17, 2019
# Deployed On:  {DEPLOYMENT_DATE}
# Purpose: 	Build template auto-generated by Ec2InstanceMaker
################################################################################

# Build tool information

debug_mode: {debug_mode}
vars_file_path: {vars_file_path}
DEPLOYMENT_DATE: {DEPLOYMENT_DATE}
DEPLOYMENT_DATE_TAG: {DEPLOYMENT_DATE_TAG}

# SNS topic

sns_arn: {sns_topic_arn}

# IAM parameters

ec2_iam_instance_policy: {ec2_iam_instance_policy}
ec2_iam_instance_profile: {ec2_iam_instance_profile}
ec2_iam_instance_role: {ec2_iam_instance_role}
preserve_iam_role: {preserve_iam_role}

# EC2 instance parameters

aws_ami: {aws_ami}
preserve_ami: {preserve_ami}
cloudwatch_log_group: {cloudwatch_log_group}
enable_cloudwatch_logs: {enable_cloudwatch_logs}
log_retention_days: {log_retention_days}
preserve_cloudwatch_logs: {preserve_cloudwatch_logs}
base_os: {base_os}
count: {count}
instance_type: {instance_type}
architecture: {architecture}
ec2_keypair: {ec2_keypair}
ec2_user: {ec2_user}
ec2_user_home: /home/{ec2_user}
ec2_user_src: {ec2_user_home}/src
custom_user_prelogin_scripts: {custom_user_prelogin_scripts}
custom_user_postboot_scripts: {custom_user_postboot_scripts}
hyperthreading: {hyperthreading}
instance_data_dir: {instance_data_dir}
instance_userdata_script: instance_userdata.{instance_name}.sh
instance_name: {instance_name}
instance_owner: {instance_owner}
instance_owner_department: {instance_owner_department}
instance_owner_email: {instance_owner_email}
request_type: {request_type}
instance_serial_number: {instance_serial_number}
instance_serial_number_file: {instance_serial_number_file}
prod_level: {prod_level}
project_id: {project_id}
spot_price: {spot_price}
ssh_keypair_file: {ec2_keypair}.pem
ssh_known_hosts: ~/.ssh/known_hosts

# EBS parameters

ebs_encryption: {ebs_encryption}
ebs_optimized: {ebs_optimized}
ebs_root_volume_size: {ebs_root_volume_size}
ebs_root_volume_type: {ebs_root_volume_type}
ebs_root_volume_iops: {ebs_root_volume_iops}

ebs_device_volume_size: {ebs_device_volume_size}
ebs_device_volume_type: {ebs_device_volume_type}
ebs_device_volume_iops: {ebs_device_volume_iops}

# AWS networking

az: {az}
enable_placement_group: {enable_placement_group}
placement_group_strategy: {placement_group_strategy}
public_ip: {public_ip}
region: {region}
security_group_name: {security_group_name}
ssh_allowed_ips: {ssh_allowed_ips}
subnet_id: {subnet_id}
vpc_id: {vpc_id}
vpc_name: {vpc_name}
vpc_security_group_ids: {vpc_security_group_ids}

# Terraform

terraform_version: {TERRAFORM_VERSION}
provider: aws.{vpc_name}
provider_tf_dest: provider_aws.tf
tf_ec2_instance_dest: {instance_name}.tf

# Generated file names (all written into instance_data_dir above)

access_instance_dest: access_instance.{instance_name}.py
build_instance_script: build_instance.{instance_name}.sh
build_ami_script: build_ami.{instance_name}.sh
kill_instance_script: kill_instance.{instance_name}.sh
"""

    write_vars_file(settings.vars_file_path, vars_file_main_part, dataclasses.asdict(instance_parameters))

    print("")
    print("Saved " + settings.instance_name + " build template: " + settings.vars_file_path)
    print("")

    if settings.count == 1:
        print("Generating templates for instance " + settings.instance_name + "...")
    else:
        print("Generating templates for instance family " + settings.instance_name + "...")

    render_instance_templates(dataclasses.asdict(instance_parameters), cwd, instance_data_dir_abs)

    with open(settings.instance_serial_number_file, "a") as fh:
        print("Rendered instance templates into: " + instance_data_dir_abs, file=fh)

    ctrlC_Abort(
        ctrlc_abort_seconds if ctrlc_abort_seconds is not None else (30 if debug_mode == "true" else 5),
        80,
        settings.vars_file_path,
        settings.instance_data_dir,
        settings.instance_serial_number_file,
        aws_clients.ec2_client,
        aws_clients.iam,
        vpc.security_group_name,
        vpc.vpc_security_group_ids,
        iam_sns.ec2_iam_instance_role,
        iam_sns.ec2_iam_instance_policy,
        iam_sns.ec2_iam_instance_profile,
        iam_sns.preserve_iam_role,
        vpc.ec2_keypair,
    )

    print("Invoking Terraform to build " + settings.instance_name + "...")
    apply_terraform(settings.instance_data_dir, debug_mode, refer_to_docs_and_quit)

    security_group_tags = build_security_group_tags(
        vpc.security_group_name,
        settings.instance_name,
        settings.instance_serial_number,
        settings.instance_owner,
        settings.instance_owner_email,
        settings.instance_owner_department,
        settings.DEPLOYMENT_DATE_TAG,
        options.project_id,
    )
    aws_clients.ec2_client.create_tags(Resources=[vpc.vpc_security_group_ids], Tags=security_group_tags)


# Function: report_and_notify()
# Purpose: phase 5 -- print post-apply console guidance (access/kill/AMI
# commands), decrypt and print the Windows password table if applicable,
# publish the build-completion SNS notification, and return a BuildReport
# summarizing all of it for a programmatic caller. Every path through
# run_build() ends here; main() is the only caller that then calls
# sys.exit(0) itself.


def report_and_notify(
    settings: BuildSettings,
    aws_clients: AwsClients,
    network: NetworkAndComputeResolution,
    vpc: VpcSecurityAndKeypairResolution,
    iam_sns: IamSnsAndLoggingResolution,
    refer_to_docs_and_quit: QuitFn,
) -> BuildReport:
    print("")
    print("".center(80, "="))
    print("")

    access_command = None
    if not network.is_windows:
        access_command = "./access_instance.py -N " + settings.instance_name
        if settings.count == 1:
            print("Access the new " + settings.base_os + " instance via SSM Session Manager:")
        else:
            print("Access the " + str(settings.count) + " members of the " + settings.base_os + " instance family via SSM Session Manager:")
        print(access_command)

    windows_instance_table = None
    if network.is_windows:
        windows_instance_table = build_windows_password_table(
            settings.instance_data_dir, vpc.ec2_keypair, functools.partial(fetch_windows_instance_details, refer_to_docs_and_quit=refer_to_docs_and_quit), decrypt_windows_admin_passwords
        )
        if settings.count == 1:
            print("Access the new instance via Windows Remote Desktop with this information:")
        else:
            print("Access the new instance family members with Windows Remote Desktop:")
        print("")
        print(windows_instance_table)
        print("")
        print("Reprint this table:")
        print("./access_instance.py -N " + settings.instance_name)

    print("")
    if settings.count == 1:
        print("Delete the instance:")
    else:
        print("Delete the instance family:")
    kill_script = "./kill-instance." + settings.instance_name + ".sh"
    print(kill_script)

    print("")
    print("Build an AMI from the new instance:")
    build_ami_script = "./build-ami." + settings.instance_name + ".sh"
    print(build_ami_script)

    sns_message_body, sns_instance_subject = build_sns_message(settings.count, settings.instance_name, settings.instance_type, settings.request_type, iam_sns.sns_datestamp, iam_sns.sns_timestamp)
    publish_sns_notification(aws_clients.sns_client, iam_sns.sns_topic_arn, sns_message_body, sns_instance_subject)

    print("")
    print("Exiting...")

    return BuildReport(
        instance_name=settings.instance_name,
        count=settings.count,
        is_windows=network.is_windows,
        access_command=access_command,
        windows_password_table=windows_instance_table,
        kill_script=kill_script,
        build_ami_script=build_ami_script,
        sns_topic_arn=iam_sns.sns_topic_arn,
    )


# Function: run_build()
# Purpose: the actual build orchestration -- parses argv, validates
# parameters, and threads settings through all 5 phases, returning the
# BuildReport report_and_notify() produces. main() (the CLI entry point,
# below) is a thin sys.exit(0)-after wrapper around this; mcp_server.py's
# build_instance tool calls this directly instead, passing its own
# refer_to_docs_and_quit (raises instead of exiting) and
# ctrlc_abort_seconds=0 (no interactive terminal to type CTRL-C into).


def run_build(argv: list[str] | None, refer_to_docs_and_quit: QuitFn, ctrlc_abort_seconds: int | None = None) -> BuildReport:
    # Create variables from the optional instance parameter values provided
    # from the command line. Recording argv (falling back to sys.argv only
    # when the caller didn't provide one, e.g. the real `if __name__ ==
    # "__main__":` entry point below) keeps the command line written into
    # instance_serial_number_file/the duplicate-build guidance message
    # consistent with what was actually parsed -- important for tests that
    # call main() directly with a synthetic argv, which must never leak the
    # test runner's own sys.argv into recorded state.

    recorded_argv = list(argv) if argv is not None else sys.argv
    args = parse_args(argv)
    instance_name = args.instance_name

    az = args.az
    base_os = args.base_os
    count = args.count
    preserve_ami = args.preserve_ami
    custom_ami = args.custom_ami
    custom_user_scripts = [name.strip() for name in args.custom_user_scripts.split(",") if name.strip()]
    debug_mode = args.debug_mode
    ebs_encryption = args.ebs_encryption
    ebs_optimized = args.ebs_optimized
    ebs_root_volume_iops = args.ebs_root_volume_iops
    ebs_root_volume_size = args.ebs_root_volume_size
    ebs_root_volume_type = args.ebs_root_volume_type
    ebs_device_volume_iops = args.ebs_device_volume_iops
    ebs_device_volume_size = args.ebs_device_volume_size
    ebs_device_volume_type = args.ebs_device_volume_type
    ec2_keypair = args.ec2_keypair
    enable_placement_group = args.enable_placement_group
    hyperthreading = args.hyperthreading
    iam_json_policy = args.iam_json_policy
    iam_name_prefix = args.iam_name_prefix
    iam_role = args.iam_role
    instance_owner = args.instance_owner
    instance_owner_department = args.instance_owner_department
    instance_owner_email = args.instance_owner_email
    request_type = args.request_type
    instance_type = args.instance_type
    enable_cloudwatch_logs = args.enable_cloudwatch_logs
    log_retention_days = args.log_retention_days
    placement_group_strategy = args.placement_group_strategy
    prod_level = args.prod_level
    preserve_cloudwatch_logs = args.preserve_cloudwatch_logs
    project_id = args.project_id
    public_ip = args.public_ip
    region = az[:-1]
    spot_buffer = args.spot_buffer
    security_group = args.security_group
    ssh_allowed_ips = args.ssh_allowed_ips
    turbot_account = args.turbot_account
    vpc_name = args.vpc_name

    # Validate parameters that have successfully passed the argument parser
    # checks and don't require additional error checking.

    if ebs_encryption == "true":
        p_val("ebs_encryption", debug_mode)
    p_val("ebs_root_volume_type", debug_mode)
    p_val("ebs_device_volume_type", debug_mode)
    p_val("request_type", debug_mode)
    p_val("prod_level", debug_mode)
    log_retention_days_check(log_retention_days, debug_mode)

    # Raise an error if instance_name or instance_owner contain uppercase
    # letters.

    validate_instance_name_and_owner_format(instance_name, instance_owner, refer_to_docs_and_quit)

    # Get the version of Terraform being used to build the instance(s), and
    # abort if Terraform is not installed.

    TERRAFORM_VERSION = get_terraform_version(refer_to_docs_and_quit)
    print("")
    p_val("Terraform: version = " + TERRAFORM_VERSION, debug_mode)

    # Set the vars_file_path.

    vars_file_path = "./vars_files/" + instance_name + ".yml"

    # Create a Pythonic marker for the current working directory.

    cwd = os.getcwd()
    instance_data_dir_abs = cwd + "/instance_data/" + instance_name

    # Validate --custom_user_scripts against what actually exists in
    # custom_user_scripts/ before doing anything else with AWS -- a typo'd
    # name should fail fast, not after a VPC/security-group/IAM build has
    # already started.

    custom_user_scripts_dir = cwd + "/custom_user_scripts"
    custom_user_prelogin_scripts, custom_user_postboot_scripts = resolve_custom_user_scripts(custom_user_scripts, custom_user_scripts_dir, refer_to_docs_and_quit)

    # Check for the presence of an existing vars_file for the instance(s)
    # before creating any state directories. If an existing vars_file
    # exists, abort to prevent potential duplications.

    # Held for the rest of the build: closes a TOCTOU gap in the
    # duplicate-build guard right below (two concurrent builds of the
    # same instance_name could otherwise both pass the "vars_file
    # doesn't exist yet" check before either created it) and prevents a
    # concurrent destroy_instance/terminate_via_kill_script from racing
    # against this build's own instance_data_dir/vars_file state.
    with instance_lock(instance_name, refer_to_docs_and_quit):
        abort_if_vars_file_exists(vars_file_path, recorded_argv)
        if debug_mode == "true":
            print_TextHeader(instance_name, "Validating", 80)
        else:
            print("Performing parameter validation...")
        p_val("vars_file_path", debug_mode)

        # Create the vars_file/instance_data/active_instances state directories
        # if they don't already exist, and generate a unique
        # instance_serial_number for the instance(s).

        instance_data_dir = "./instance_data/" + instance_name + "/"
        ensure_state_directories(instance_data_dir)

        serial_number_info = generate_instance_serial_number(instance_name)
        DEPLOYMENT_DATE = serial_number_info["DEPLOYMENT_DATE"]
        DEPLOYMENT_DATE_TAG = serial_number_info["DEPLOYMENT_DATE_TAG"]
        instance_serial_datestamp = serial_number_info["instance_serial_datestamp"]
        instance_serial_number = serial_number_info["instance_serial_number"]
        instance_serial_number_file = "./active_instances/" + instance_name + ".serial"

        write_serial_number_file(instance_serial_number_file, instance_name, instance_serial_datestamp, recorded_argv)
        p_val("instance_serial_number", debug_mode)
        p_val("instance_serial_number_file", debug_mode)

        # Create the AWS clients needed for the rest of the build.

        aws_clients = create_aws_clients(region, boto3.client, boto3.resource)

        # settings bundles every value established once above (or straight from
        # argparse) that's never reassigned again -- threaded unchanged through
        # every phase below instead of unpacked into a dozen individual
        # parameters per phase call.

        settings = BuildSettings(
            instance_name=instance_name,
            instance_serial_number=instance_serial_number,
            instance_serial_number_file=instance_serial_number_file,
            instance_data_dir=instance_data_dir,
            vars_file_path=vars_file_path,
            region=region,
            az=az,
            debug_mode=debug_mode,
            instance_owner=instance_owner,
            instance_owner_email=instance_owner_email,
            instance_owner_department=instance_owner_department,
            instance_type=instance_type,
            base_os=base_os,
            count=count,
            request_type=request_type,
            enable_placement_group=enable_placement_group,
            enable_cloudwatch_logs=enable_cloudwatch_logs,
            log_retention_days=log_retention_days,
            iam_name_prefix=iam_name_prefix,
            turbot_account=turbot_account,
            DEPLOYMENT_DATE=DEPLOYMENT_DATE,
            DEPLOYMENT_DATE_TAG=DEPLOYMENT_DATE_TAG,
            TERRAFORM_VERSION=TERRAFORM_VERSION,
        )

        ebs = EbsRequest(
            encryption=ebs_encryption,
            optimized=ebs_optimized,
            root_volume_size=ebs_root_volume_size,
            root_volume_type=ebs_root_volume_type,
            root_volume_iops=ebs_root_volume_iops,
            device_volume_size=ebs_device_volume_size,
            device_volume_type=ebs_device_volume_type,
            device_volume_iops=ebs_device_volume_iops,
        )

        # Phase 1: AZ/region validation, instance_type_info, base_os
        # checks/family, EBS optimize/encrypt/resize, spot pricing, placement
        # group strategy. Must happen before any other AWS API call that
        # doesn't itself handle a bad region/AZ cleanly -- describe_availability_zones
        # is the first call to hit a bogus region with a recognizable,
        # catchable error (ValueError/EndpointConnectionError), and gives a
        # clean, friendly abort instead of a raw traceback from something
        # further down the line.

        network = resolve_network_and_compute(settings, aws_clients, ebs, spot_buffer, placement_group_strategy, refer_to_docs_and_quit)

        # Phase 2: VPC/subnet, ssh_allowed_ips, security group, ec2_user home
        # directory, AMI, keypair.

        vpc = resolve_vpc_security_and_keypair(settings, aws_clients, network, vpc_name, security_group, ssh_allowed_ips, custom_ami, ec2_keypair, refer_to_docs_and_quit)

        # Phase 3: CloudWatch log group, IAM role/policy/profile setup, Turbot
        # environment variables, SNS topic creation/subscribe/timestamps.

        iam_sns = provision_iam_sns_and_logging(settings, aws_clients, iam_role, iam_json_policy, refer_to_docs_and_quit)

        options = BuildOptions(
            preserve_ami=preserve_ami,
            preserve_cloudwatch_logs=preserve_cloudwatch_logs,
            hyperthreading=hyperthreading,
            prod_level=prod_level,
            project_id=project_id,
            public_ip=public_ip,
        )

        # Phase 4: assemble InstanceParameters, print the --debug_mode dump,
        # write the vars_file, render the Jinja2 templates, run the CTRL-C-abort
        # safety window, apply Terraform, and tag the security group.

        render_and_apply(
            settings,
            aws_clients,
            network,
            vpc,
            iam_sns,
            options,
            ebs,
            cwd,
            instance_data_dir_abs,
            custom_user_prelogin_scripts,
            custom_user_postboot_scripts,
            refer_to_docs_and_quit,
            ctrlc_abort_seconds,
        )

        # Phase 5: post-apply console guidance, Windows password table if
        # applicable, and the build-completion SNS notification. Terminal --
        # every path through run_build() ends here.

        return report_and_notify(settings, aws_clients, network, vpc, iam_sns, refer_to_docs_and_quit)


def main(argv: list[str] | None = None) -> NoReturn:
    run_build(argv, refer_to_docs_and_quit)
    sys.exit(0)


if __name__ == "__main__":
    main()
