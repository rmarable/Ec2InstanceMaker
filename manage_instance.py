#!/usr/bin/env python3
#
################################################################################
# Name:         manage_instance.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:	Start, stop, reboot, or terminate EC2 instance(s) built by
# 		Ec2InstanceMaker, identified safely via the ManagedBy tag so
# 		this can never act on an instance this toolkit didn't create.
################################################################################

# Load some required Python libraries

import argparse
import os
import subprocess
import sys

import boto3
import yaml
from botocore.exceptions import ClientError, EndpointConnectionError

# Import some external lists and functions.
# Source: aux_data.py
from aux_data import refer_to_docs_and_quit

MANAGED_BY_TAG_VALUE = "Ec2InstanceMaker"

# Parse input from the command line.

parser = argparse.ArgumentParser(description="manage_instance.py: start, stop, reboot, or terminate Ec2InstanceMaker-built EC2 instances")
parser.add_argument("--instance_name", "-n", help="name of the EC2 instance or family", required=True)
parser.add_argument("--action", "-a", choices=["start", "stop", "reboot", "terminate"], help="action to perform", required=True)
parser.add_argument("--region", "-r", help="AWS region (default: read from ./vars_files/<instance_name>.yml)", required=False, default=None)
parser.add_argument("--auto_confirm", "-c", help="skip the confirmation prompt", action="store_true", required=False, default=False)

args = parser.parse_args()
instance_name = args.instance_name
action = args.action
region = args.region
auto_confirm = args.auto_confirm

# "terminate" delegates entirely to kill-instance.<name>.sh, which does the
# full teardown (security group, IAM role/policy/profile, SNS topic,
# AMI/snapshot, local state files and symlinks) that a bare
# ec2:TerminateInstances call would leave dangling. This only works from
# the repo checkout where the instance was built -- same dispatch model
# access_instance.py already uses.

if action == "terminate":
    kill_script = "kill-instance." + instance_name + ".sh"
    if not os.path.exists(kill_script):
        error_msg = 'kill-instance script "' + kill_script + '" was not found! Run manage_instance.py from the repo checkout where "' + instance_name + '" was built.'
        refer_to_docs_and_quit(error_msg)
    if not auto_confirm:
        print("")
        print('This will fully tear down "' + instance_name + '": the instance(s), security group, IAM role, SNS topic, and local state.')
        confirmation = input('Type "yes" to continue: ')
        if confirmation.strip().lower() != "yes":
            print("Aborting...")
            sys.exit(1)
    sys.exit(subprocess.run(["bash", kill_script]).returncode)

# For start/stop/reboot: resolve the region, then look up the instance(s)
# via the ManagedBy tag.

if region is None:
    vars_file_path = "./vars_files/" + instance_name + ".yml"
    if os.path.exists(vars_file_path):
        with open(vars_file_path) as fh:
            vars_file_content = yaml.safe_load(fh)
        if vars_file_content:
            region = vars_file_content.get("region")
    if region is None:
        error_msg = "--region was not given and could not be read from " + vars_file_path + "! Pass --region explicitly."
        refer_to_docs_and_quit(error_msg)

ec2_client = boto3.client("ec2", region_name=region)

# Match both a single instance (Name == instance_name) and a family
# (Name == instance_name-<index>) in one filter, and require the
# ManagedBy tag so this can never touch an instance this toolkit didn't
# create, even if its Name happens to collide with something else.

filters = [
    {"Name": "tag:Name", "Values": [instance_name, instance_name + "-*"]},
    {"Name": "tag:ManagedBy", "Values": [MANAGED_BY_TAG_VALUE]},
    {"Name": "instance-state-name", "Values": ["pending", "running", "shutting-down", "stopping", "stopped"]},
]
try:
    response = ec2_client.describe_instances(Filters=filters)
except (ClientError, EndpointConnectionError) as e:
    refer_to_docs_and_quit("AWS API error while looking up " + instance_name + " in " + region + ": " + str(e))

instances = [instance for reservation in response["Reservations"] for instance in reservation["Instances"]]
if not instances:
    error_msg = 'No Ec2InstanceMaker-managed instance(s) named "' + instance_name + '" were found in ' + region + "!"
    refer_to_docs_and_quit(error_msg)

instance_ids = [instance["InstanceId"] for instance in instances]

print("")
print("The following instance(s) will be " + action + "ed:")
for instance in instances:
    name_tag = next((tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"), "?")
    print("  " + instance["InstanceId"] + "  " + name_tag + "  (" + instance["State"]["Name"] + ")")
print("")

if not auto_confirm:
    confirmation = input('Type "yes" to continue: ')
    if confirmation.strip().lower() != "yes":
        print("Aborting...")
        sys.exit(1)

action_calls = {
    "start": ec2_client.start_instances,
    "stop": ec2_client.stop_instances,
    "reboot": ec2_client.reboot_instances,
}

try:
    action_calls[action](InstanceIds=instance_ids)
except (ClientError, EndpointConnectionError) as e:
    refer_to_docs_and_quit("AWS API error while trying to " + action + " " + instance_name + ": " + str(e))

print(action.capitalize() + " request sent for: " + ", ".join(instance_ids))
sys.exit(0)
