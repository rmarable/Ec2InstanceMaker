#!/usr/bin/env python3
#
################################################################################
# Name:         make-instance.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 3, 2019
# Last Changed: September 28, 2019
# Purpose:      Generic command-line EC2 instance creator
################################################################################

# Load the required Python libraries.

import argparse
import errno
import os
import subprocess
import sys
import tempfile
from math import pi

import boto3
from prettytable import from_csv

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
    modify_iam_policy_document,
    p_fail,
    p_val,
    print_TextHeader,
    refer_to_docs_and_quit,
)
from instance_builder import (
    apply_terraform,
    build_security_group_tags,
    build_sns_message,
    compute_buffered_spot_price,
    decrypt_windows_admin_passwords,
    fetch_spot_price_raw,
    fetch_windows_instance_details,
    generate_instance_serial_number,
    generate_sns_timestamps,
    resolve_ami,
    resolve_custom_user_scripts,
    resolve_security_group,
    resolve_ssh_allowed_ips,
    resolve_vpc_and_subnet,
    setup_iam,
    setup_keypair,
    validate_and_resize_ebs_volumes,
    validate_az_and_region,
    write_vars_file,
)
from template_engine import render_instance_templates

# Parse input from the command line.

parser = argparse.ArgumentParser(description="make-instance.py: Command-line interface to build EC2 instances")

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
    help="instance base operating system (default = alinux2 a.k.a. Amazon Linux 2)",
    required=False,
    default="alinux2",
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
    help="Use a pre-existing JSON policy document in the /templates subdirectory to set permissions for iam_role (default = GenericEc2InstancePolicy.json",
    required=False,
    default="GenericEc2InstancePolicy.json",
)
parser.add_argument("--iam_name_prefix", help="Provide a prefix for the IAM entities associated with the instance (default = Ec2InstanceMaker)", required=False, default="Ec2InstanceMaker")
parser.add_argument("--iam_role", help="Apply a pre-existing IAM role to the instance(s)", required=False, default="UNDEFINED")
parser.add_argument(
    "--instance_owner_department",
    choices=[
        "analytics",
        "clinical",
        "commercial",
        "compbio",
        "compchem",
        "datasci",
        "design",
        "development",
        "hpc",
        "imaging",
        "manufacturing",
        "medical",
        "modeling",
        "operations",
        "proteomics",
        "robotics",
        "qa",
        "research",
        "scicomp",
    ],
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
    "--placement_group_strategy", "--pg_strategy", choices=["cluster", "spread"], help="Designate an EC2 placement group strategy (default = cluster)", required=False, default="cluster"
)
parser.add_argument("--preserve_ami", choices=["true", "false"], help="Preserve any AMI image built from the instance(s) post-termination (default = true)", required=False, default="true")
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

# Create variables from the optional instance parameter values provided from
# the command line.

args = parser.parse_args()
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
instance_name = args.instance_name
instance_owner = args.instance_owner
instance_owner_department = args.instance_owner_department
instance_owner_email = args.instance_owner_email
request_type = args.request_type
instance_type = args.instance_type
placement_group_strategy = args.placement_group_strategy
prod_level = args.prod_level
project_id = args.project_id
public_ip = args.public_ip
region = az[:-1]
spot_buffer = args.spot_buffer
security_group = args.security_group
ssh_allowed_ips = args.ssh_allowed_ips
turbot_account = args.turbot_account
vpc_name = args.vpc_name

# Validate parameters that have successfully passed the argument parser checks
# and don't require additional error checking.

if ebs_encryption == "true":
    p_val("ebs_encryption", debug_mode)
p_val("ebs_root_volume_type", debug_mode)
p_val("ebs_device_volume_type", debug_mode)
p_val("request_type", debug_mode)
p_val("prod_level", prod_level)

# Raise an error if instance_name or instance_owner contain uppercase letters.

if any(char0.isupper() for char0 in instance_name) or any(char1.isupper() for char1 in instance_owner):
    error_msg = "instance_name and instance_owner may not contain uppercase letters!"
    refer_to_docs_and_quit(error_msg)

# Get the version of Terraform being used to build the instance(s).

terraform_version_string = "terraform -version | head -1 | awk '{print $2}'"
TERRAFORM_VERSION = subprocess.check_output(terraform_version_string, shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)  # nosec B602 - static command, no interpolation

# Abort if Terraform is not installed.

if not TERRAFORM_VERSION:
    error_msg = "Terraform is missing! Please visit: https://www.terraform.io/downloads"
    refer_to_docs_and_quit(error_msg)
else:
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

# Create the vars_file directory if it does not already exist.

try:
    os.makedirs("./vars_files")
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Check for the presence of an existing vars_file for the instance(s).
# If an existing vars_file exists, abort to prevent potential duplications.

if os.path.isfile(vars_file_path):
    print("")
    print("  WARNING  ".center(80, "*"))
    print(("  Found an existing " + vars_file_path + " ").center(80, "-"))
    print("")
    print("Please delete this file and retry the build:")
    print("")
    print("$ rm " + vars_file_path)
    print("$ " + " ".join(sys.argv))
    print("")
    print("Aborting...")
    sys.exit(1)
else:
    if debug_mode == "true":
        print_TextHeader(instance_name, "Validating", 80)
    else:
        print("Performing parameter validation...")
    p_val("vars_file_path", debug_mode)

# Define a local state directory for the instance(s).

instance_data_dir = "./instance_data/" + instance_name + "/"
try:
    os.makedirs(instance_data_dir)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Generate a unique instance_serial_number for the instance(s).

SERIAL_DIR = "./active_instances"
try:
    os.makedirs(SERIAL_DIR)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise
serial_number_info = generate_instance_serial_number(instance_name)
DEPLOYMENT_DATE = serial_number_info["DEPLOYMENT_DATE"]
DEPLOYMENT_DATE_TAG = serial_number_info["DEPLOYMENT_DATE_TAG"]
instance_serial_datestamp = serial_number_info["instance_serial_datestamp"]
instance_serial_number = serial_number_info["instance_serial_number"]
instance_serial_number_file = SERIAL_DIR + "/" + instance_name + ".serial"

if not os.path.isfile(instance_serial_number):
    print(f"{instance_name}.{instance_serial_datestamp}", file=open(instance_serial_number_file, "w"))
    print(" ".join(sys.argv), file=open(instance_serial_number_file, "a"))
p_val("instance_serial_number", debug_mode)
p_val("instance_serial_number_file", debug_mode)

# Create a boto3 client and resource for EC2.

ec2_client = boto3.client("ec2", region_name=region)
ec2 = boto3.resource("ec2", region_name=region)

# Perform error checking on the selected AWS Region and Availability Zone.
# Abort if a non-existent Availability Zone was chosen. This must happen
# before any other AWS API call (e.g. get_instance_type_info() below) that
# doesn't itself handle a bad region/AZ cleanly -- describe_availability_zones
# is the first call to hit a bogus region with a recognizable, catchable
# error (ValueError/EndpointConnectionError), and gives a clean, friendly
# abort instead of a raw traceback from something further down the line.

validate_az_and_region(ec2_client, az, illegal_az_msg)
p_val("region", debug_mode)
p_val("az", debug_mode)

# Check to ensure the selected EC2 instance_type is valid and determine its
# supported CPU architecture, EBS optimization/encryption support, and
# placement group strategies directly from the AWS API. This also implicitly
# determines whether the instance_type is x86_64 or Graviton/ARM64 -- no
# separate --architecture flag is needed, since instance_type already fully
# implies it.

instance_type_info = get_instance_type_info(instance_type, region)
if instance_type_info is None:
    p_fail(instance_type, "instance_type", "missing_element")
architecture = instance_type_info["architecture"]
print("")
print("Selected EC2 instance type: " + instance_type + " (" + architecture + ")")

# Verify that the selected EC2 instance_type is supported by base_os.

print("")
print("Selected base operating system: " + base_os)
base_os_instance_check(base_os, instance_type, architecture, debug_mode)

# Look up base_os's family classification once -- is_windows,
# package_manager, and awscli_preinstalled are threaded through
# instance_parameters below so templates can read them too, instead of
# re-deriving the same substring logic independently in Jinja.

base_os_family = get_base_os_family(base_os)
is_windows = base_os_family["is_windows"]
package_manager = base_os_family["package_manager"]
awscli_preinstalled = base_os_family["awscli_preinstalled"]

# Create a boto3 client to interact with IAM.

iam = boto3.client("iam")

# Create a boto3 client to interact with SNS.

sns_client = boto3.client("sns", region_name=region)

# Provide a mechanism to ensure ebs_optimized is appropriately set for the EC2
# instance(s) being deployed.

if ebs_optimized == "true":
    if instance_type_info["ebs_optimized_support"] == "unsupported":
        print("")
        print("*** WARNING ***")
        print(instance_type + " does not support EBS optimization!")
        print("Disabling ebs_optimization for: " + instance_name)
        ebs_optimized = "false"
    else:
        print("")
        print("EBS optimization: Enabled")
p_val("ebs_optimized", debug_mode)

# Verify that the selected EC2 instance_type supports encrypted EBS volumes.

if ebs_encryption == "true":
    ebs_encryption_check(instance_type, instance_type_info["ebs_encryption_support"], instance_name, debug_mode)

# Enforce the 16 TB EBS size ceiling, bump undersized root/device volumes
# to AWS's recommended 30 GB minimum for Windows Server, and validate
# provisioned-IOPS bounds when ebs_root_volume_type == "io1".

ebs_root_volume_size, ebs_device_volume_size = validate_and_resize_ebs_volumes(
    ebs_root_volume_size,
    ebs_device_volume_size,
    ebs_root_volume_type,
    ebs_root_volume_iops,
    ebs_device_volume_iops,
    is_windows,
    refer_to_docs_and_quit,
)

# Print a friendly reminder that spot is cheaper than ondemand to the console
# if ondemand instances were chosen.  If using spot instances, add spot_price
# to spot_buffer to protect against market fluctuations:
#
# spot_price = spot_price * spot_buffer, rounded off to 8 decimal places.
# Default value of spot_buffer = 1/pi
#
# Current AWS spot instance prices: https://aws.amazon.com/ec2/spot/pricing/

if request_type == "ondemand":
    print("")
    print("Selected: ondemand (NOTE: spot instances are **MUCH** cheaper!)")
    spot_price = "UNDEFINED"
    spot_buffer = "UNDEFINED"
if request_type == "spot":
    spot_price_raw = fetch_spot_price_raw(ec2_client, instance_type, is_windows, az)
    spot_price = compute_buffered_spot_price(spot_price_raw, spot_buffer)
    p_val("spot_price_raw", debug_mode)
    p_val("spot_price_buffer", debug_mode)
    p_val("spot_price", debug_mode)
    print("")
    print("Setting spot_price: $" + str(spot_price) + "/hr")
print("")

# Determine if the instance(s) should be placed in an EC2 placement group.
# Abort if the user attempts to put a single instance in a placement group.
# Partition spread groups are not yet supported by Terraform for some reason.

if enable_placement_group == "true":
    if count == 1:
        error_msg = "Using placement groups requires deploying more than a single instance!"
        refer_to_docs_and_quit(error_msg)
    else:
        ec2_placement_group_check(instance_type, placement_group_strategy, instance_type_info["placement_group_strategies"], debug_mode)
        print("Enabling: EC2 Placement Group")
        print("Strategy: " + placement_group_strategy)
        print("")
        if debug_mode == "true":
            p_val("placement_group_strategy", debug_mode)
else:
    placement_group_strategy = "UNDEFINED"

# Parse the AWS Account ID.

stsclient = boto3.client("sts", region_name=region, endpoint_url="https://sts." + region + ".amazonaws.com")
aws_account_id = stsclient.get_caller_identity()["Account"]

# Determine subnet_id, vpc_id, and vpc_name from the selected AWS Region and
# Availability Zone.  Return an error if this value is missing.
#
# Parse the vpc_id using vpc_name.
# If vpc_name was not supplied on the command line, use the default VPC.

vpc_id, vpc_name, subnet_id = resolve_vpc_and_subnet(ec2_client, vpc_name, az, refer_to_docs_and_quit)
p_val("vpc_name", debug_mode)
p_val("subnet_id", debug_mode)

# Resolve the CIDR block allowed to reach the instance's SSH/RDP port.
# Defaults to the instance's own VPC CIDR; 0.0.0.0/0 is refused outright.

ssh_allowed_ips = resolve_ssh_allowed_ips(ec2_client, vpc_id, ssh_allowed_ips, refer_to_docs_and_quit)
p_val("ssh_allowed_ips", debug_mode)

# If the user fails to supply a valid security_group, create a new default
# EC2 security group that permits only inbound SSH (Linux) or RDP (Windows)
# traffic to access the instance(s), scoped to ssh_allowed_ips.

security_group_name, vpc_security_group_ids = resolve_security_group(ec2, region, security_group, instance_serial_number, vpc_id, is_windows, ssh_allowed_ips, add_inbound_security_group_rule)
p_val("security_group", debug_mode)
p_val("vpc_security_group_ids", debug_mode)

# Configure the ec2_user account and home directory path to match base_os.
# Illegal options have already been screened by the argument parser so it's
# okay to move straight to parameter validation.

ec2_user = base_os_family["ec2_user"]
ec2_user_home = "/home/" + ec2_user
p_val("ec2_user", debug_mode)
p_val("ec2_user_home", debug_mode)

# Parse aws_ami from base_os and region if custom_ami was not provided.
# If custom_ami was supplied, verify its existence.

aws_ami = resolve_ami(custom_ami, base_os, region, architecture, aws_account_id, get_ami_info, check_custom_ami, refer_to_docs_and_quit)
p_val("aws_ami", debug_mode)

# Create a new EC2 key pair and secret key file for the instance(s) within the
# deployment region of choice if either entity doesn't already exist.

if ec2_keypair == "ec2_keypair_default":
    ec2_keypair = instance_serial_number + "_" + region

secret_key_file = instance_data_dir + ec2_keypair + ".pem"

setup_keypair(ec2_client, ec2_keypair, secret_key_file, region, debug_mode, refer_to_docs_and_quit)
p_val("ec2_keypair", debug_mode)

# Create and apply an IAM EC2 instance(s) profile from the default template if
# iam_role was not defined by the operator.
#
# If iam_name_prefix was supplied, prepend this string to the IAM role, policy,
# and instance profile associated with the instance(s) and modify the JSON
# policy document with the modify_iam_policy_document function.
#
# All IAM resources are to be terminated along with the instance(s).

ec2_iam_instance_role, ec2_iam_instance_policy, ec2_iam_instance_profile, preserve_iam_role = setup_iam(
    iam, iam_role, iam_name_prefix, iam_json_policy, instance_data_dir, instance_serial_number, debug_mode, refer_to_docs_and_quit, modify_iam_policy_document
)
if debug_mode == "true":
    print("")
    p_val("ec2_iam_instance_role", debug_mode)
    p_val("ec2_iam_instance_profile", debug_mode)

# Set some critical environment variables to support Turbot operability.
# https://turbot.com/about/

if turbot_account != "DISABLED":
    turbot_profile = "turbot__" + turbot_account + "__" + instance_owner
    os.environ["AWS_PROFILE"] = turbot_profile
    os.environ["AWS_DEFAULT_REGION"] = region
    boto3.setup_default_session(profile_name=turbot_profile)

# Generate a unique SNS topic name for important EC2 events involving the
# instance(s) and subscribe instance_owner_email.

sns_topic_name = "Ec2_Instance_SNS_Alerts_" + str(instance_serial_number)
sns_topic = sns_client.create_topic(Name=sns_topic_name)
sns_topic_arn = sns_topic["TopicArn"]
sns_subscription = sns_client.subscribe(TopicArn=sns_topic_arn, Protocol="email", Endpoint=instance_owner_email)
if debug_mode == "true":
    print("")
    print("Subscribed " + instance_owner_email + " to SNS topic: " + sns_topic_name)
    print("")
p_val("sns_topic_name", debug_mode)

# Generate date and time stamps for the SNS instance message alert.

sns_datestamp, sns_timestamp = generate_sns_timestamps()

# Define the instance_parameters dictionary for populating the vars_file.

instance_parameters = {
    "architecture": architecture,
    "awscli_preinstalled": awscli_preinstalled,
    "az": az,
    "aws_ami": aws_ami,
    "aws_account_id": aws_account_id,
    "base_os": base_os,
    "is_windows": is_windows,
    "package_manager": package_manager,
    "count": count,
    "custom_user_prelogin_scripts": custom_user_prelogin_scripts,
    "custom_user_postboot_scripts": custom_user_postboot_scripts,
    "debug_mode": debug_mode,
    "ebs_encryption": ebs_encryption,
    "ebs_optimized": ebs_optimized,
    "ebs_root_volume_size": ebs_root_volume_size,
    "ebs_root_volume_type": ebs_root_volume_type,
    "ebs_root_volume_iops": ebs_root_volume_iops,
    "ebs_device_volume_size": ebs_device_volume_size,
    "ebs_device_volume_type": ebs_device_volume_type,
    "ebs_device_volume_iops": ebs_device_volume_iops,
    "instance_type": instance_type,
    "ec2_keypair": ec2_keypair,
    "ec2_user": ec2_user,
    "ec2_user_home": ec2_user_home,
    "ec2_iam_instance_policy": ec2_iam_instance_policy,
    "ec2_iam_instance_profile": ec2_iam_instance_profile,
    "ec2_iam_instance_role": ec2_iam_instance_role,
    "enable_placement_group": enable_placement_group,
    "hyperthreading": hyperthreading,
    "iam_name_prefix": iam_name_prefix,
    "instance_data_dir": instance_data_dir_abs,
    "instance_owner": instance_owner,
    "instance_owner_email": instance_owner_email,
    "instance_owner_department": instance_owner_department,
    "instance_name": instance_name,
    "request_type": request_type,
    "instance_serial_number": instance_serial_number,
    "instance_serial_number_file": instance_serial_number_file,
    "placement_group_strategy": placement_group_strategy,
    "preserve_ami": preserve_ami,
    "prod_level": prod_level,
    "project_id": project_id,
    "preserve_iam_role": preserve_iam_role,
    "public_ip": public_ip,
    "region": region,
    "security_group_name": security_group_name,
    "spot_price": spot_price,
    "ssh_allowed_ips": ssh_allowed_ips,
    "vpc_security_group_ids": vpc_security_group_ids,
    "sns_topic_arn": sns_topic_arn,
    "sns_datestamp": sns_datestamp,
    "sns_timestamp": sns_timestamp,
    "subnet_id": subnet_id,
    "turbot_account": turbot_account,
    "vars_file_path": vars_file_path,
    "vpc_id": vpc_id,
    "vpc_name": vpc_name,
    "DEPLOYMENT_DATE": DEPLOYMENT_DATE,
    "DEPLOYMENT_DATE_TAG": DEPLOYMENT_DATE_TAG,
    "TERRAFORM_VERSION": TERRAFORM_VERSION,
}

# Print the current values of all defined instance_parameters to the console
# when debug_mode is enabled.

if debug_mode == "true":
    print_TextHeader(instance_name, "Printing", 80)
    print("aws_account_id = " + aws_account_id)
    if turbot_account != "disabled":
        print("turbot_account = " + turbot_account)
    print("aws_ami = " + str(aws_ami))
    print("az = " + az)
    print("base_os = " + base_os)
    if count > 1:
        print("count = " + count)
    print("base_os = " + base_os)
    print("ebs_encryption = " + str(ebs_encryption))
    print("ebs_optimized = " + str(ebs_optimized))
    print("ebs_root_volume_size = " + str(ebs_root_volume_size))
    print("ebs_root_volume_type = " + ebs_root_volume_type)
    print("ebs_root_volume_iops = " + str(ebs_root_volume_iops))
    print("ebs_device_volume_size = " + str(ebs_device_volume_size))
    print("ebs_device_volume_type = " + ebs_device_volume_type)
    print("ebs_device_volume_iops = " + str(ebs_device_volume_iops))
    print("instance_type = " + instance_type)
    print("architecture = " + architecture)
    print("ec2_keypair = " + ec2_keypair)
    print("ec2_user = " + ec2_user)
    print("ec2_user_home = " + ec2_user_home)
    if enable_placement_group == "true":
        print("enable_placement_group = " + enable_placement_group)
        print("placement_group_strategy = " + placement_group_strategy)
    print("hyperthreading = " + hyperthreading)
    print("instance_name = " + instance_name)
    print("instance_owner = " + instance_owner)
    print("instance_owner_email = " + instance_owner_email)
    print("instance_owner_department = " + instance_owner_department)
    print("instance_serial_number = " + instance_serial_number)
    print("instance_serial_number_file = " + instance_serial_number_file)
    print("request_type = " + request_type)
    print("preserve_ami = " + preserve_ami)
    print("prod_devel = " + prod_level)
    if project_id != "UNDEFINED":
        print("project_id = " + project_id)
    if ec2_iam_instance_profile:
        print("preserve_iam_role = " + preserve_iam_role)
        if "UNDEFINED" not in ec2_iam_instance_policy:
            print("ec2_iam_instance_policy = " + ec2_iam_instance_policy)
        print("ec2_iam_instance_profile = " + ec2_iam_instance_profile)
        print("ec2_iam_instance_role = " + ec2_iam_instance_role)
    print("public_ip = " + public_ip)
    print("region = " + region)
    print("security_group_name = " + str(security_group_name))
    print("spot_price = " + spot_price)
    print("subnet_id = " + subnet_id)
    print("vars_file_path = " + vars_file_path)
    print("vpc_id = " + vpc_id)
    print("vpc_name = " + vpc_name)
    print("vpc_security_group_ids = " + vpc_security_group_ids)
    print("sns_topic_arn = " + sns_topic_arn)
    print("DEPLOYMENT_DATE = " + DEPLOYMENT_DATE)
    print("TERRAFORM_VERSION = " + TERRAFORM_VERSION)

# Generate the vars_file for this instance.

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

# Write the instance(s) vars_file to disk.

write_vars_file(vars_file_path, vars_file_main_part, instance_parameters)

print("")
print("Saved " + instance_name + " build template: " + vars_file_path)
print("")

# Generate the EC2 instance creation templates directly with Jinja2.

if count == 1:
    print("Generating templates for instance " + instance_name + "...")
else:
    print("Generating templates for instance family " + instance_name + "...")

render_instance_templates(instance_parameters, cwd, instance_data_dir_abs)

print("Rendered instance templates into: " + instance_data_dir_abs, file=open(instance_serial_number_file, "a"))

# Abort if CTRL-C is typed within 5 seconds to ensure all state files and
# directories, EC2 keypairs and secret key files, IAM roles and profiles,
# and EC2 security groups that might exist are properly deleted.

if debug_mode == "false":
    ctrlC_Abort(
        5,
        80,
        vars_file_path,
        instance_data_dir,
        instance_serial_number_file,
        instance_serial_number,
        region,
        security_group_name,
        vpc_security_group_ids,
        ec2_iam_instance_role,
        ec2_iam_instance_policy,
        ec2_iam_instance_profile,
        preserve_iam_role,
    )
else:
    ctrlC_Abort(
        30,
        80,
        vars_file_path,
        instance_data_dir,
        instance_serial_number_file,
        instance_serial_number,
        region,
        security_group_name,
        vpc_security_group_ids,
        ec2_iam_instance_role,
        ec2_iam_instance_policy,
        ec2_iam_instance_profile,
        preserve_iam_role,
    )

# Create the new EC2 instance(s) with Terraform.

print("Invoking Terraform to build " + instance_name + "...")
apply_terraform(instance_data_dir, debug_mode)

# Apply the common tag set to the EC2 security group.

SecurityGroupTags = build_security_group_tags(
    security_group_name, instance_name, instance_serial_number, instance_owner, instance_owner_email, instance_owner_department, DEPLOYMENT_DATE_TAG, project_id
)
ec2_client.create_tags(Resources=[vpc_security_group_ids], Tags=SecurityGroupTags)

# Print a pretty spacing bar to improve user readability.

print("")
print("".center(80, "="))
print("")

# Print the instance SSH access command to the console if base_os is Linux.

if not is_windows:
    if count == 1:
        print("Access the new " + base_os + " instance via SSH:")
        print("$ ./access_instance.py -N " + instance_name)
    else:
        print("Access the " + str(count) + " members of the instance" + base_os + " instance family via SSH:")
        print("$ ./access_instance.py -N " + instance_name)

# If base_os is Windows:
#   - parse instance_id and ip_address information from the Terraform output
#   - decrypt the Administrator password
#   - dump everything to a CSV temp file
#   - render and print a "pretty" table to the console for user readability
#   - provide instance access guidance using Windows Remote Desktop
#
# This prevents the decrypted Administrator password from being visible in the
# Terraform state file without having to use Vault.

if is_windows:
    csv_fd, csvTempFile = tempfile.mkstemp(prefix="_csvTempFile_" + instance_serial_number + "_", suffix=".csv")
    os.close(csv_fd)
    windows_InstanceId, windows_InstanceName, windows_IpAddress = fetch_windows_instance_details(instance_data_dir)
    windows_AdministratorPassword = decrypt_windows_admin_passwords(instance_data_dir, ec2_keypair, windows_InstanceId)
    csv_file = open(csvTempFile, "w")
    csv_file.write("Instance Name,IP Address,Adminstrator Password\n")
    csv_file.close()
    csv_file = open(csvTempFile, "a")
    for x, y, z in zip(windows_InstanceName.split(","), windows_IpAddress.split(","), windows_AdministratorPassword.split(","), strict=True):
        element = x + "," + y + "," + z + "\n"
        csv_file.write(element)
    csv_file.close()
    csv_file = open(csvTempFile)
    windows_InstanceTable = from_csv(csv_file)
    csv_file.close()
    if count == 1:
        print("Access the new instance via Windows Remote Desktop with this information:")
    else:
        print("Access the new instance family members with Windows Remote Desktop:")
    print("")
    print(windows_InstanceTable)
    print("")
    print("Reprint this table:")
    print("$ ./access_instance.py -N " + instance_name)

# Print the kill-instance command to the console.

print("")
if count == 1:
    print("Delete the instance:")
else:
    print("Delete the instance family:")
print("$ ./kill-instance." + instance_name + ".sh")

# Print the AMI build command to the console.

print("")
print("Build an AMI from the new instance:")
print("$ ./build-ami." + instance_name + ".sh")

# Generate the SNS message body.

sns_message_body, sns_instance_subject = build_sns_message(count, instance_name, instance_type, request_type, sns_datestamp, sns_timestamp)

# Publish an SNS notification announcing creation of the instance(s).

sns_client.publish(TopicArn=sns_topic_arn, Message=sns_message_body, Subject=sns_instance_subject)

# Cleanup and exit.

if is_windows:
    os.remove(csvTempFile)
    if debug_mode == "true":
        print("Deleted: " + csvTempFile)
    print("")
else:
    print("")
print("Exiting...")
sys.exit(0)
