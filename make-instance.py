#!/usr/bin/env python3
#
################################################################################
# Name:         make-instance.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 3, 2019
# Last Changed: June 14, 2019
# Purpose:      Generic command-line EC2 instance creator
################################################################################

# Load the required Python libraries.

import argparse
import boto3
import botocore
import errno
import json
import os
import shutil
import subprocess
import sys
import time
from botocore.exceptions import ClientError
from math import pi
from nested_lookup import nested_lookup
from prettytable import from_csv

# Import some external lists and functions.
# Source: aux_data.py

from aux_data import add_security_group_rule
from aux_data import check_custom_ami
from aux_data import ctrlC_Abort
from aux_data import ec2_instances_ebs_optimized
from aux_data import ec2_instances_full_list
from aux_data import get_ami_info
from aux_data import illegal_az_msg
from aux_data import p_fail
from aux_data import p_val
from aux_data import print_TextHeader
from aux_data import refer_to_docs_and_quit

# Parse input from the command line.

parser = argparse.ArgumentParser(description='make-instance.py: Command-line interface to build EC2 instances')

# Configure parser arguments for the required variables.

parser.add_argument('--az', '-A', help='AWS Availability Zone (REQUIRED)', required=True)
parser.add_argument('--instance_name', '-N', help='name of the instance (REQUIRED)', required=True)
parser.add_argument('--instance_owner', '-O', help='ActiveDirectory username of the instance instance_owner (REQUIRED)', required=True)
parser.add_argument('--instance_owner_email', '-E', help='Email address of the instance instance_owner (REQUIRED)', required=True)

# Configure arguments for the optional parameters.

parser.add_argument('--ansible_verbosity', '-V', help='Set the Ansible verbosity level (default = none)', required=False, default='')
parser.add_argument('--base_os', '-B', choices=['alinux', 'alinux2', 'centos6', 'centos7', 'ubuntu1404', 'ubuntu1604', 'ubuntu1804', 'windows2019'], help='cluster base operating system (default = alinux2 a.k.a. Amazon Linux 2)', required=False, default='alinux2')
parser.add_argument('--count', '-C', help='number of EC2 instances to create (default = 1)', type=int, required=False, default=1)
parser.add_argument('--custom_ami', help='ami-id of a custom Amazon Machine Image (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--debug_mode', '-D', choices=['true', 'false'], help='Enable debug mode (default = false)', required=False, default='false')
parser.add_argument('--ebs_encryption', choices=['true', 'false'], help='enable EBS encryption (default = false)', required=False, default='false')
parser.add_argument('--ebs_optimized', choices=['true', 'false'],help='use optimized EBS volumes (default = yes)', required=False, default='true')
parser.add_argument('--ebs_root_volume_iops', help='amount of provisioned IOPS for the EBS root volume when ebs_root_volume_type=io1 (default = 0)', required=False, type=int, default=0)
parser.add_argument('--ebs_root_volume_size', help='EBS volume size in GB (Linux default = 8, Windows default = 30)', required=False, type=int, default=8)
parser.add_argument('--ebs_root_volume_type', choices=['gp2', 'io1', 'st1'], help='EBS volume type (default = gp2)', required=False, default='gp2')
parser.add_argument('--hyperthreading', '-H', choices=['true', 'false'], help='enable Intel Hyperthreading (default = true)', required=False, default='true')
parser.add_argument('--iam_json_policy', '-J', help='Use a pre-existing JSON policy document in the /templates subdirectory to set permissions for iam_role (default = GenericEc2InstancePolicy.json', required=False, default='GenericEc2InstancePolicy.json')
parser.add_argument('--iam_role', help='Apply a pre-existing IAM role to the instance', required=False, default='UNDEFINED')
parser.add_argument('--instance_owner_department', choices=['analytics', 'clinical', 'commercial', 'compbio', 'compchem', 'datasci', 'design', 'development', 'hpc', 'imaging', 'manufacturing', 'medical', 'modeling', 'operations', 'proteomics', 'robotics', 'qa', 'research', 'scicomp'], help='Department of the instance_owner (default = hpc)', required=False, default='hpc')
parser.add_argument('--request_type', choices=['ondemand', 'spot'], help='choose between ondemand or spot instances (default = ondemand)', required=False, default='ondemand')
parser.add_argument('--instance_type', '-I', help='EC2 instance type (default = t2.micro)', required=False, default='t2.micro')
parser.add_argument('--prod_level', choices=['dev', 'test', 'stage', 'prod'], help='Operating stage of the jumphost  (default = dev)', required=False, default='dev')
parser.add_argument('--project_id', '-P', help='Project name or ID number (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--security_group', '-S', help='Primary security group for the EC2 instance (default = generic_ec2_sg)', required=False, default='generic_ec2_sg')
parser.add_argument('--spot_buffer', help='pricing buffer to protect from Spot market fluctuations: spot_price = spot_price + spot_price*spot_buffer', type=float, required=False, default=round((1/pi), 8))
parser.add_argument('--turbot_account', '-T', help='Turbot account ID (default = DISABLED)', required=False, default='DISABLED')

# Create variables from optional instance_parameters provided via command line.

args = parser.parse_args()
ansible_verbosity = args.ansible_verbosity
az = args.az
base_os = args.base_os
count = args.count
custom_ami = args.custom_ami
debug_mode = args.debug_mode
ebs_encryption = args.ebs_encryption
ebs_optimized = args.ebs_optimized
ebs_root_volume_iops = args.ebs_root_volume_iops
ebs_root_volume_size = args.ebs_root_volume_size
ebs_root_volume_type = args.ebs_root_volume_type
hyperthreading = args.hyperthreading
iam_json_policy = args.iam_json_policy
iam_role = args.iam_role
instance_name = args.instance_name
instance_owner = args.instance_owner
instance_owner_department = args.instance_owner_department
instance_owner_email = args.instance_owner_email
request_type = args.request_type
instance_type = args.instance_type
prod_level = args.prod_level
project_id = args.project_id
region = az[:-1]
spot_buffer = args.spot_buffer
security_group = args.security_group
turbot_account = args.turbot_account

# Validate parameters that have successfully passed the argument parser checks
# and don't require additional error checking.

if ebs_encryption == 'true':
    p_val('ebs_encryption', debug_mode)
p_val('ebs_root_volume_type', debug_mode)
p_val('request_type', debug_mode)
p_val('prod_level', prod_level)

# Raise an error if instance_name or instance_owner contain uppercase letters.

if any(char0.isupper() for char0 in instance_name) or any(char1.isupper() for char1 in instance_owner):
    error_msg='instance_name and instance_owner may not contain uppercase letters!'
    refer_to_docs_and_quit(error_msg)

# Get the version of Terraform being used to build the instance.

terraform_version_string = "terraform -version | head -1 | awk '{print $2}'"
TERRAFORM_VERSION = subprocess.check_output(terraform_version_string, shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)

# Abort if Terraform is not installed.

if not TERRAFORM_VERSION:
    error_msg='Terraform is missing! Please visit: https://www.terraform.io/downloads'
    refer_to_docs_and_quit(error_msg)
else:
    print('')
    p_val('Terraform: version = ' + TERRAFORM_VERSION, debug_mode)

# Get the version of Ansible being used to build the instance.

ansible_version_string = "ansible --version | head -1 | awk '{print $2}' | tr -d '\n'"
ANSIBLE_VERSION = subprocess.check_output(ansible_version_string, shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)

# Abort if Ansible is not installed.

if not ANSIBLE_VERSION:
    error_msg='Ansible is missing! Please visit: https://bit.ly/2KHuyY5'
    refer_to_docs_and_quit(error_msg)

# Set the vars_file_path.

vars_file_path = './vars_files/' + instance_name + '.yml'

# Create a Pythonic marker for the current working directory.

cwd = os.getcwd()

# Create the vars_file directory if it does not already exist.

try:
    os.makedirs('./vars_files')
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Check for the presence of an existing vars_file for this instance.
# If an existing vars_file exists, abort to prevent potential duplications.

if os.path.isfile(vars_file_path):
    error_msg='Ansible is missing! Please visit: https://bit.ly/2KHuyY5'
    print('')
    print('  WARNING  '.center(80, '*'))
    print(('  Found an existing ' + vars_file_path + ' ').center(80,'-'))
    print('')
    print('Please delete this file and retry the build:')
    print('')
    print('$ rm ' + vars_file_path)
    print('$ ' + ' '.join(sys.argv))
    print('')
    print('Aborting...')
    sys.exit(1)
else:
    if debug_mode == 'true':
        print_TextHeader(instance_name, 'Validating', 80)
    else:
        print('Performing parameter validation...')
    p_val('vars_file_path', debug_mode)

# Define a local state directory for this instance.

instance_data_dir = './instance_data/' + instance_name + '/'
try:
    os.makedirs(instance_data_dir)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Generate a unique instance_serial_number to track individual instances and
# groups of "instance families."

DEPLOYMENT_DATE = time.strftime("%B %-d, %Y")
DEPLOYMENT_DATE_TAG = time.strftime("%B-%-d-%Y")
TIMESTAMP = time.strftime("%s")
SERIAL_DIR = './active_instances'
try:
    os.makedirs(SERIAL_DIR)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise
instance_serial_datestamp = time.strftime("%S%M%H%d%m%Y")
instance_serial_number = instance_name + '-' + instance_serial_datestamp
instance_serial_number_file = SERIAL_DIR + '/' + instance_name + '.serial'

if not os.path.isfile(instance_serial_number):
    print('%s.%s' % (instance_name, instance_serial_datestamp), file=open(instance_serial_number_file, 'w'))
    print(' '.join(sys.argv), file=open(instance_serial_number_file, 'a'))
p_val('instance_serial_number', debug_mode)
p_val('instance_serial_number_file', debug_mode)

# Check to ensure the selected EC2 instance_type is valid.

if instance_type not in ec2_instances_full_list:
    p_fail(instance_type, 'instance_type', ec2_instances_full_list)
else:
    print('')
    print('Selected EC2 instance type: ' + instance_type)

# Verify that the selected EC2 instance_type is supported by base_os.

if base_os == 'centos6' and ('t3' or 'm5' or 'a1.' or 'c5.' or 'f1.4xlarge' or 'g3s.xlarge' or 'p3' or 'r5' or 'x1e.' or 'z1d.' or 'h1.' or 'i3.metal' or 'i3en.') in instance_type:
    error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
    refer_to_docs_and_quit(error_msg)
elif base_os == 'centos7' and ('m5metal.' or 'a1.' or 'p3dn.24xlarge' or 'r5d.24xlarge' or 'r5d.metal' or 'r5.metal' or 'x1e.' or 'h1.' or 'i3en.') in instance_type:
    error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
    refer_to_docs_and_quit(error_msg)
elif base_os == 'ubuntu1404' and ('t1.' or 't3a.' or 'm5a' or 'm5d.' or 'm5.metal' or 'm1.' or 'a1.' or 'c5n.' or 'c5d.' or 'c1.' or 'f1.4xlarge' or 'p3dn.24xlarge' or 'r5' or 'm2.' or 'z1d.' or 'i3.metal' or 'i3en.') in instance_type:
    error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
    refer_to_docs_and_quit(error_msg)
elif base_os == 'ubuntu1604' and ('t1.' or 't3a.' or 'm5a' or 'm5d.metal' or 'm5.metal' or 'm1.' or 'a1.' or 'c1.' or 'r5ad.' or 'r5d.24xlarge' or 'r5d.metal' or 'r5.metal' or 'm2.' or 'z1d.metal' or 'i3en.') in instance_type:
    error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
    refer_to_docs_and_quit(error_msg)
elif base_os == 'ubuntu1804' and ('t1.' or 't3a.' or 'm5ad' or 'm5d.metal' or 'm5.metal' or 'm1.' or 'a1.' or 'c1.' or 'cc2.8xlarge' or 'r5ad.' or 'r5d.24xlarge' or 'm2.' or 'i3en.') in instance_type:
    error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
    refer_to_docs_and_quit(error_msg)
elif base_os == 'windows2019' and ('a1.' or 'f1.') in instance_type:
    error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
    refer_to_docs_and_quit(error_msg)
else:
    p_val('base_os', debug_mode)
    p_val('instance_type', debug_mode)

# Provide a mechanism to ensure ebs_optimized is appropriately set for the EC2
# instance being deployed.

if ebs_optimized == 'true':
    if instance_type not in ec2_instances_ebs_optimized:
        print('')
        print('*** WARNING ***')
        print(instance_type + ' does not support EBS optimization!')
        print('Disabling ebs_optimization for: ' + instance_name)
        ebs_optimized = 'false'
p_val('ebs_optimized', debug_mode)

# Check to ensure requested EBS volume size is not larger than 16 TB.

if ebs_root_volume_size > 16000:
    error_msg='Maximum allowed EBS volume size is 16 TB (16000 GB)!'
    refer_to_docs_and_quit(error_msg)

# Resize the EBS volume for Windows Server if the selected value is less than
# the AWS recommended minimum (30 GB).

if base_os == 'windows2019':
    if ebs_root_volume_size <= 30:
        ebs_root_volume_size = 30

# If provisioned EBS was selected for ebs_root_volume_type, check to ensure
# the requested amount of IOPS is between 100-16,000.

if ebs_root_volume_type == 'io1':
    if (ebs_root_volume_iops == 0) or (ebs_root_volume_iops > 16000):
        error_msg='ebs_root_volume_iops must be set to a value between 100 and 16,000!'
        refer_to_docs_and_quit(error_msg)

# Define a boto3 client to interact with EC2.

ec2client = boto3.client('ec2', region_name = region)

# Print a friendly reminder that spot is cheaper than ondemand to the console
# if ondemand instances were chosen.  If using spot instances, add spot_price
# to spot_buffer to protect against market fluctuationa:
# spot_price = spot_price * spot_buffer, rounded to 8 decimal places.
# Current AWS spot instance prices: https://aws.amazon.com/ec2/spot/pricing/
# Defalt value of spot_buffer = 1/pi 

if request_type == 'ondemand':
    print("Selecting: ondemand (using spot is *significantly* cheaper!)")
    spot_price = 'UNDEFINED'
if request_type == 'spot':
    if base_os == 'windows2019':
        prices=ec2client.describe_spot_price_history(InstanceTypes=[instance_type],MaxResults=1,ProductDescriptions=['Windows'],AvailabilityZone=az)
    else:
        prices=ec2client.describe_spot_price_history(InstanceTypes=[instance_type],MaxResults=1,ProductDescriptions=['Linux/UNIX'],AvailabilityZone=az)
    spot_price_raw = float(prices['SpotPriceHistory'][0]['SpotPrice'])
    spot_price = round(spot_price_raw + (spot_buffer * spot_price_raw), 8)
    p_val('spot_price_raw', debug_mode)
    p_val('spot_price_buffer', debug_mode)
    p_val('spot_price', debug_mode)
    print('Selecting: spot instances @ $' + str(spot_price) + '/hr')
print('')

# Parse the AWS Account ID.

stsclient = boto3.client('sts', region_name=region, endpoint_url='https://sts.' + region + '.amazonaws.com')
aws_account_id = stsclient.get_caller_identity()["Account"]

# Perform error checking on the selected AWS Region and Availability Zone.
# Abort if a non-existent Availability Zone was chosen.

try:
    az_information = ec2client.describe_availability_zones()
except (ValueError):
    illegal_az_msg(az)
except (botocore.exceptions.EndpointConnectionError):
    illegal_az_msg(az)
else:
    p_val('region', debug_mode)
    p_val('az', debug_mode)

# Parse the subnet_id, vpc_id, and vpc_name from the selected AWS Region and
# Availability Zone.

subnet_information = ec2client.describe_subnets(
    Filters=[ { 'Name': 'availabilityZone', 'Values': [ az, ] }, ],
)
vpc_information = ec2client.describe_vpcs()

try:
    subnet_id = subnet_information['Subnets'][0]['SubnetId']
except IndexError:
    error_msg='AvailabilityZone ' + az + ' does not contain any valid subnets!'
    refer_to_docs_and_quit(error_msg)
p_val('subnet_id', debug_mode)
for vpc in vpc_information["Vpcs"]:
    vpc_id = vpc["VpcId"]
    p_val('vpc_id', debug_mode)
    try:
        vpc_name = vpc_information['Vpcs'][0]['Tags'][0]['Value']
    except KeyError:
        error_msg=vpc_id + ' (' + az + ') lacks a valid Name tag!'
        refer_to_docs_and_quit(error_msg)
    p_val('vpc_name', debug_mode)

# Create a boto3 resource for EC2.

ec2 = boto3.resource('ec2', region_name = region)

# If the user fails to supply a valid security_group, create a new default 
# EC2 security gorup that only permits inbound SSH (Linux) or RDP (Windows)
# traffic to access the instance.

if security_group == 'generic_ec2_sg':
    security_group = security_group + '_' + base_os
filters = [ { 'Name': 'group-name', 'Values': [ security_group, ] }, ]
sg_id = list(ec2.security_groups.filter(Filters=filters))
if not sg_id:
    security_group_name = security_group
    security_group = ec2.create_security_group(
        GroupName=security_group_name,
        Description='EC2 security group - created by Ec2InstanceMaker',
        VpcId=vpc_id
    )
    if base_os == 'windows2019':
        add_security_group_rule(region, security_group, "tcp", "0.0.0.0/0", 3389, 3389)
    else:
        add_security_group_rule(region, security_group, "tcp", "0.0.0.0/0", 22, 22)
    sg_id = list(ec2.security_groups.filter(Filters=filters))
p_val('security_group', debug_mode)

# Parse and validate the vpc_security_group_ids.

v_sg_id = str(*sg_id).split("'")
vpc_security_group_ids = v_sg_id[1]
p_val('vpc_security_group_ids', debug_mode)

# Configure the ec2_user account and home directory path to match base_os.
# Illegal options have already been screened by the argument parser.

if 'alinux' in base_os:
    ec2_user = 'ec2-user'
if 'centos' in base_os:
    ec2_user = 'centos'
if 'ubuntu' in base_os:
    ec2_user = 'ubuntu'
if 'windows' in base_os:
    ec2_user = 'Administrator'
ec2_user_home = '/home/' + ec2_user
p_val('ec2_user', debug_mode)
p_val('ec2_user_home', debug_mode)

# Parse aws_ami from base_os and region if custom_ami was not provided.
# If custom_ami was supplied, verify its existence.

if custom_ami == 'UNDEFINED':
    aws_ami = get_ami_info(base_os, region)
else:
    aws_ami = check_custom_ami(custom_ami, aws_account_id, region)
    if aws_ami == 'false':
        error_msg = 'AMI image "' + custom_ami + '" is unavailable in this AWS account!'
        refer_to_docs_and_quit(error_msg)
p_val('aws_ami', debug_mode)

# Generate a unique SNS topic name for important EC2 events involving this 
# instance and subscribe instance_owner_email.

sns_client = boto3.client('sns')
sns_topic_name = 'Ec2_Instance_SNS_Alerts_' + str(instance_serial_number)
sns_topic = sns_client.create_topic(Name=sns_topic_name)
sns_topic_arn = sns_topic['TopicArn']
sns_subscription = sns_client.subscribe(
    TopicArn=sns_topic_arn,
    Protocol='email',
    Endpoint=instance_owner_email
    )
if debug_mode == 'true':
    print('')
    print('Subscribed ' + instance_owner_email + ' to SNS topic: ' + sns_topic_name)
    print('')
p_val('sns_topic_name', debug_mode)

# Create a new EC2 key pair and secret key file for the instance within the
# deployment region of choice if either entity doesn't already exist.

ec2_keypair = instance_serial_number + '_' + region
secret_key_file = instance_data_dir + ec2_keypair + '.pem'

try:
    ec2_keypair_status = ec2client.describe_key_pairs(KeyNames=[ec2_keypair])
    if debug_mode == 'true':
        print('')
    print('Found EC2 keypair: ' + ec2_keypair)
except ClientError as e:
    if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
        new_ec2_keypair = ec2client.create_key_pair(KeyName=ec2_keypair)
        print(new_ec2_keypair['KeyMaterial'], file=open(secret_key_file, 'w'))
        subprocess.run('chmod 0600 ' + secret_key_file, shell=True)
        print('Created EC2 keypair: ' + ec2_keypair)

# If the secret key file is missing but the EC2 keypair still exists, provide
# guidance to the operator on how to resolve this discrepancy.

if not os.path.isfile(secret_key_file):
    print('')
    print('*** ERROR ***')
    print('Missing: ' + secret_key_file)
    print('')
    print('Please resolve this issue and retry, perhaps deleting the original keypair by')
    print('pasting this command into the shell:')
    print('')
    print('$ aws --region ' + region + ' ec2 delete-key-pair --key-name ' + ec2_keypair)
    print('')
    print('Aborting...')
    sys.exit(1)
else:
    if debug_mode == 'true':
        print('')
    p_val('ec2_keypair', debug_mode)

# Create a boto3 client to interact with IAM.

iam = boto3.client('iam')

# Create a generic IAM EC2 instance profile permitting EC2 and S3 operations
# for the instance if iam_role was not defined by the operator.
# All of these resources will be terminated along with the instance.

if iam_role == 'UNDEFINED':
    ec2_iam_instance_role = 'ec2-instance-role-' + instance_serial_number
    ec2_iam_instance_policy = 'ec2-instance-policy-' + instance_serial_number
    ec2_iam_instance_profile = 'ec2-instance-profile-' + instance_serial_number
    instance_json_policy_src = 'templates/' + iam_json_policy
    instance_json_policy_template = instance_data_dir + iam_json_policy
    preserve_iam_role = 'false'
    try:
        check_role = iam.get_role(RoleName=ec2_iam_instance_role)
        if debug_mode == 'true':
            print('')
        print('Found IAM EC2 instance role: ' + ec2_iam_instance_role)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            with open(instance_json_policy_src, 'r') as ec2_instance_role_src:
                filedata = ec2_instance_role_src.read()
                ec2_instance_role_src.close()
            with open(instance_json_policy_template, 'w') as ec2_instance_role_dest:
                ec2_instance_role_dest.write(filedata)
                ec2_instance_role_dest.close()
            instance_ec2_instance_role = iam.create_role(
                RoleName=ec2_iam_instance_role,
                AssumeRolePolicyDocument='{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow", "Principal": { "Service": [ "ec2.amazonaws.com" ] }, "Action": "sts:AssumeRole" } ] }',
                Description='Vanilla EC2 instance role'
                )
            with open(instance_json_policy_template, 'r') as policy_input:
                instance_ec2_policy = iam.put_role_policy(
                    RoleName=ec2_iam_instance_role,
                    PolicyName=ec2_iam_instance_policy,
                    PolicyDocument=policy_input.read()
                    )
            if debug_mode == 'true':
                print('')
            print('Created EC2 instance role: ' + ec2_iam_instance_role)
    try:
        check_profile = iam.get_instance_profile(InstanceProfileName=ec2_iam_instance_profile)
        print('Found IAM EC2 instance profile: ' + ec2_iam_instance_profile)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            instance_ec2_instance_profile = iam.create_instance_profile(InstanceProfileName=ec2_iam_instance_profile)
            print('Created EC2 instance profile: ' + ec2_iam_instance_profile)
            instance_add_ec2_role_to_instance_profile = iam.add_role_to_instance_profile(
                InstanceProfileName=ec2_iam_instance_profile,
                RoleName=ec2_iam_instance_role
                )
            print('Added: ' + ec2_iam_instance_role + ' to ' + ec2_iam_instance_profile)

# If the operator provides an iam_role, use it to construct an IAM EC2
# instance profile role that will be applied to the new EC2 instance.  The
# EC2 instance profile and role will be preserved upon instance termination.
#
# Note: this is part of the if:else construct defined above!

else:
    ec2_iam_instance_role = iam_role
    ec2_iam_instance_policy = 'UNDEFINED'
    ec2_iam_instance_profile = iam_role + '_instance_profile'
    preserve_iam_role = 'true'
    try:
        check_role = iam.get_role(RoleName=ec2_iam_instance_role)
        if debug_mode == 'true':
            print('')
        print('Found IAM EC2 instance role: ' + ec2_iam_instance_role)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            error_msg='IAM EC2 instance role ' + ec2_iam_instance_role + ' does not exist!'
            refer_to_docs_and_quit(error_msg)
    try:
        check_profile = iam.get_instance_profile(InstanceProfileName=ec2_iam_instance_profile)
        print('Found IAM EC2 instance profile: ' + ec2_iam_instance_profile)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            instance_ec2_instance_profile = iam.create_instance_profile(InstanceProfileName=ec2_iam_instance_profile)
            print('Created EC2 instance profile: ' + ec2_iam_instance_profile)
            instance_add_ec2_role_to_instance_profile = iam.add_role_to_instance_profile(
                InstanceProfileName=ec2_iam_instance_profile,
                RoleName=ec2_iam_instance_role
                )
            print('Added: ' + ec2_iam_instance_role + ' to ' + ec2_iam_instance_profile)
if debug_mode == 'true':
    print('')
    p_val('ec2_iam_instance_role', debug_mode)
    p_val('ec2_iam_instance_profile', debug_mode)

# Set some critical environment variables to support Turbot.
# https://turbot.com/about/

if turbot_account != 'DISABLED':
    turbot_profile = 'turbot__' + turbot_account + '__' + instance_owner
    os.environ['AWS_PROFILE'] = turbot_profile
    os.environ['AWS_DEFAULT_REGION'] = region
    boto3.setup_default_session(profile_name=turbot_profile)

# Define the instance_parameters dictionary for populating the vars_file.

instance_parameters = {
    'az': az,
    'aws_ami': aws_ami,
    'aws_account_id': aws_account_id,
    'base_os': base_os,
    'count': count,
    'ebs_encryption': ebs_encryption,
    'ebs_optimized': ebs_optimized,
    'ebs_root_volume_size': ebs_root_volume_size,
    'ebs_root_volume_type': ebs_root_volume_type,
    'ebs_root_volume_iops': ebs_root_volume_iops,
    'instance_type': instance_type,
    'ec2_keypair': ec2_keypair,
    'ec2_user': ec2_user,
    'ec2_user_home': ec2_user_home,
    'ec2_iam_instance_policy': ec2_iam_instance_policy,
    'ec2_iam_instance_profile': ec2_iam_instance_profile,
    'ec2_iam_instance_role': ec2_iam_instance_role,
    'instance_type': instance_type,
    'hyperthreading': hyperthreading,
    'instance_owner': instance_owner,
    'instance_owner_email': instance_owner_email,
    'instance_owner_department': instance_owner_department,
    'instance_name': instance_name,
    'request_type': request_type,
    'instance_serial_number': instance_serial_number,
    'instance_serial_number_file': instance_serial_number_file,
    'prod_level': prod_level,
    'project_id': project_id,
    'preserve_iam_role': preserve_iam_role,
    'region': region,
    'security_group': security_group,
    'spot_price': spot_price,
    'vpc_security_group_ids': vpc_security_group_ids,
    'sns_topic_arn': sns_topic_arn,
    'subnet_id': subnet_id,
    'turbot_account': turbot_account,
    'vars_file_path': vars_file_path,
    'vpc_id': vpc_id,
    'vpc_name': vpc_name,
    'ANSIBLE_VERSION': ANSIBLE_VERSION,
    'DEPLOYMENT_DATE': DEPLOYMENT_DATE,
    'DEPLOYMENT_DATE_TAG': DEPLOYMENT_DATE_TAG,
    'TERRAFORM_VERSION': TERRAFORM_VERSION
}

# Print the current values of all defined instance_parameters to the console
# when debug_mode is enabled.

if debug_mode == 'true':
    print_TextHeader(instance_name, 'Printing', 80)
    print('aws_account_id = ' + aws_account_id)
    if turbot_account != 'disabled':
        print('turbot_account = ' + turbot_account)
    print('aws_ami = ' + str(aws_ami))
    print('az = ' + az)
    print('base_os = ' + base_os)
    if count > 1:
        print('count = ' + count)
    print('base_os = ' + base_os)
    print('ebs_encryption = ' + str(ebs_encryption))
    print('ebs_optimized = ' + str(ebs_optimized))
    print('ebs_root_volume_size = ' + str(ebs_root_volume_size))
    print('ebs_root_volume_type = ' + ebs_root_volume_type)
    print('ebs_root_volume_iops = ' + str(ebs_root_volume_iops))
    print('instance_type = ' + instance_type)
    print('ec2_keypair = ' + ec2_keypair)
    print('ec2_user = ' + ec2_user)
    print('ec2_user_home = ' + ec2_user_home)
    print('hyperthreading = ' + hyperthreading)
    print('instance_name = ' + instance_name)
    print('instance_owner = ' + instance_owner)
    print('instance_owner_email = ' + instance_owner_email)
    print('instance_owner_department = ' + instance_owner_department)
    print('request_type = ' + request_type)
    print('instance_serial_number = ' + instance_serial_number)
    print('instance_serial_number_file = ' + instance_serial_number_file)
    print('prod_devel = ' + prod_level)
    if project_id != 'UNDEFINED':
        print('project_id = ' + project_id)
    print('region = ' + region)
    print('security_group = ' + str(security_group))
    if ec2_iam_instance_profile:
        print('preserve_iam_role = ' + preserve_iam_role)
        if 'UNDEFINED' not in ec2_iam_instance_policy:
            print('ec2_iam_instance_policy = ' + ec2_iam_instance_policy)
        print('ec2_iam_instance_profile = ' + ec2_iam_instance_profile)
        print('ec2_iam_instance_role = ' + ec2_iam_instance_role)
    print('spot_price = ' + spot_price)
    print('vpc_security_group_ids = ' + vpc_security_group_ids)
    print('subnet_id = ' + subnet_id)
    print('sns_topic_arn = ' + sns_topic_arn)
    print('vars_file_path = ' + vars_file_path)
    print('vpc_id = ' + vpc_id)
    print('vpc_name = ' + vpc_name)
    print('ANSIBLE_VERSION = ' + ANSIBLE_VERSION)
    print('DEPLOYMENT_DATE = ' + DEPLOYMENT_DATE)
    print('TERRAFORM_VERSION = ' + TERRAFORM_VERSION)

# Generate the vars_file for this instance.

vars_file_main_part = '''\
################################################################################
# Name:    	{instance_name}.yml
# Author:  	Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 3, 2019
# Last Changed: June 9, 2019
# Deployed On:  {DEPLOYMENT_DATE}
# Purpose: 	Build template auto-generated by Ec2InstanceMaker
################################################################################

# Build tool information

ansible_version: {ANSIBLE_VERSION}
remove_instance_data_dir: true
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
base_os: {base_os}
count: {count}
instance_type: {instance_type}
ec2_keypair: {ec2_keypair}
ec2_user: {ec2_user}
ec2_user_home: /home/{ec2_user}
ec2_user_src: "{{{{ ec2_user_home }}}}/src"
hyperthreading: {hyperthreading}
instance_data_dir: "{{{{ local_workingdir }}}}/instance_data/{{{{ instance_name }}}}"
instance_userdata_src: "{{{{ local_workingdir }}}}/templates/instance_userdata.j2"
instance_userdata_script: instance_userdata.{{{{ instance_name }}}}.sh
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
ssh_keypair_file: "{{{{ ec2_keypair }}}}.pem"
ssh_known_hosts: ~/.ssh/known_hosts

# EBS parameters
# Note: Terraform does not currently support encryption of root devices at
# instance creation.  Please use a custom_ami with an already-encrypted EBS
# root volume instead.

ebs_encryption: {ebs_encryption}
ebs_optimized: {ebs_optimized}
ebs_root_volume_size: {ebs_root_volume_size}
ebs_root_volume_type: {ebs_root_volume_type}
ebs_root_volume_iops: {ebs_root_volume_iops}

# AWS networking

az: {az}
region: {region}
security_group: {security_group}
subnet_id: {subnet_id}
vpc_id: {vpc_id}
vpc_name: {vpc_name}
vpc_security_group_ids: {vpc_security_group_ids}

# Terraform

terraform_version: {TERRAFORM_VERSION}
provider: aws.{{{{ vpc_name }}}}
provider_tf_src: "{{{{ local_workingdir }}}}/templates/provider_aws.j2"
provider_tf_dest: provider_aws.tf
tf_ec2_instance_src: "{{{{ local_workingdir }}}}/templates/DEFAULT_EC2_TEMPLATE.j2"
tf_ec2_instance_dest: "{{{{ instance_name }}}}.tf"

# Template paths

access_instance_src: "{{{{ local_workingdir }}}}/templates/access_instance.j2"
access_instance_dest: access_instance.{{{{ instance_name }}}}.py
build_instance_src: "{{{{ local_workingdir }}}}/templates/build_instance.j2"
build_instance_script: build_instance.{{{{ instance_name }}}}.sh
kill_instance_src: "{{{{ local_workingdir }}}}/templates/kill_instance.j2"
kill_instance_script: kill_instance.{{{{ instance_name }}}}.sh
remove_instance_data_dir_src: "{{{{ local_workingdir }}}}/templates/remove_instance_data_dir.j2"
remove_instance_data_dir_dest: remove_instance_data_dir.{{{{ instance_name }}}}.sh
stage_dir_parent: /tmp/_stagedir_Rmarable_InstanceMaker
stage_dir: "{{{{ stage_dir_parent }}}}/{{{{ instance_name }}}}"

'''

# Write the instance vars_file to disk.

print(vars_file_main_part.format(**instance_parameters), file = open(vars_file_path, 'w'))

print('')
print('Saved ' + instance_name + ' build template: ' + vars_file_path)
print('')

# Generate the EC2 instance and security group templates using Ansible.

print('Generating templates for instance...')

cmd_string = 'ansible-playbook --extra-vars \"instance_name=' + instance_name + ' instance_serial_number=' + instance_serial_number + ' turbot_account=' + turbot_account + '"' + ' create_instance_terraform_templates.yml ' + ansible_verbosity

print(cmd_string, file=open(instance_serial_number_file, "a"))

subprocess.run(cmd_string, shell=True)

# Create the new EC2 instance and security group with Terraform.
# Abort if CTRL-C is typed within 5 seconds.

if debug_mode == 'false':
    ctrlC_Abort(5, 80, vars_file_path, instance_data_dir, instance_serial_number_file, instance_serial_number, region)
else:
    ctrlC_Abort(30, 80, vars_file_path, instance_data_dir, instance_serial_number_file, instance_serial_number, region)

print('Invoking Terraform to build ' + instance_name + '...')

subprocess.run('terraform init -input=false', shell=True, cwd=instance_data_dir)
subprocess.run('terraform plan -out terraform_environment', shell=True, cwd=instance_data_dir)
subprocess.run('terraform apply \"terraform_environment\"', shell=True, cwd=instance_data_dir)

# Print a pretty spacing bar to improve user readability.
# Replace with '=' bar from ParallelClusterMaker.

print('')
print(''.center(78, '='))
print('')

# Print the instance SSH access command to the console if base_os is Linux.

if 'windows2019' not in base_os:
    if count == 1:
        print('Access the new Linux instance via SSH:')
        print('$ ./access_instance.py -N ' + instance_name)
    else:
        print('Access members of the new Linux instance family via SSH:')
        print('$ ./access_instance.py -N ' + instance_name)

# If base_os is Windows:
#   - parse instance_id and ip_address information from the Terraform output
#   - decrypt the Administrator password
#   - dump everything to a CSV temp file
#   - render and print a "pretty" table to the console for user readability
#   - provide instance access guidance using Windows Remote Desktop
#
# This prevents the decrypted Administrator password from being visible in the
# Terraform state file without having to use Vault.

if base_os == 'windows2019':
    csvTempFile = '/tmp/_csvTempFile_' + instance_serial_number + '.csv'
    windows_instance_id_tf = subprocess.run('terraform show | grep instance_id | awk \'{print $3}\'',  stdout=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL, cwd=instance_data_dir)
    windows_InstanceId = windows_instance_id_tf.stdout.decode('utf-8').replace('\"', '').strip()
    windows_instance_name_tf = subprocess.run('terraform show | grep instance_name_index | awk \'{print $3}\'',  stdout=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL, cwd=instance_data_dir)
    windows_InstanceName = windows_instance_name_tf.stdout.decode('utf-8').replace('\"', '').strip()
    winItemAdminPw_tf = []
    for winItemId_tf in str(windows_InstanceId).split(","):
        winItemPw_tf = subprocess.run('aws ec2 get-password-data --instance-id ' + winItemId_tf + str(' --priv-launch-key ') + ec2_keypair + '.pem | jq \'..|.PasswordData?\'', stdout=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL, cwd=instance_data_dir)
        adminpw_tf = winItemPw_tf.stdout.decode('utf-8').replace('\"', '').strip()
        winItemAdminPw_tf.append(adminpw_tf)
    windows_AdministratorPassword = ','.join(winItemAdminPw_tf)
    windows_ip_addr_tf = subprocess.run('terraform show | grep instance_ip_addresses | awk \'{print $3}\'', stdout=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL, cwd=instance_data_dir)
    windows_IpAddress = windows_ip_addr_tf.stdout.decode('utf-8').replace('\"', '').strip()
    csv_file = open(csvTempFile, 'w')
    csv_file.write('Instance Name,IP Address,Adminstrator Password\n')
    csv_file.close()
    csv_file = open(csvTempFile, 'a')
    for x, y, z in zip(windows_InstanceName.split(','), windows_IpAddress.split(','), windows_AdministratorPassword.split(',')):
        element = x + ',' + y + ',' + z + '\n'
        csv_file.write(element)
    csv_file.close()
    csv_file = open(csvTempFile, 'r')
    windows_InstanceTable = from_csv(csv_file)
    csv_file.close()
    if count == 1:
        print('Access the new instance via Windows Remote Desktop with this information:')
    else:
        print('Access the new instance family members with Windows Remote Desktop:')
    print('')
    print(windows_InstanceTable)
    print('')
    print('Reprint this table:')
    print('$ ./access_instance.py -N ' + instance_name)

# Print the kill-instance command to the console.

print('')
if count == 1:
    print('Delete the instance:')
else:
    print('Delete the instance family:')
print('$ ./kill-instance.' + instance_name + '.sh')

# Cleanup and exit.

if base_os == 'windows2019':
    os.remove(csvTempFile)
    if debug_mode == 'true':
        print('Deleted: ' + csvTempFile)
    print('')
else:
    print('')
print('Exiting...')
sys.exit(0)
