################################################################################
# Name:		aux_data.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	June 3, 2019
# Last Changed:	July 29, 2019
# Purpose:	Data structures and functions to support Ec2InstanceMaker
################################################################################

# Global variable definitions

null_list = ['']
supported = 0

# Function: add_security_group_rule()
# Purpose: add a rule to a security group

def add_inbound_security_group_rule(region, sec_grp, protocol, cidr, psource, pdest):
    import boto3
    ec2 = boto3.resource('ec2', region_name = region)
    sec_grp.authorize_ingress(
        IpProtocol=protocol,
        CidrIp=cidr,
        FromPort=psource,
        ToPort=pdest
    )

# Function: base_os_instance_check()
# Purpose: verify the selected EC2 instance_type is supported by base_os

def base_os_instance_check(base_os, instance_type, debug_mode):
    def base_os_status(base_os, instance_type):
        print('')
        error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
        refer_to_docs_and_quit(error_msg)
    if base_os == 'centos6' and ('t3' or 'm5' or 'a1.' or 'c5.' or 'f1.4xlarge' or 'g3s.xlarge' or 'p3' or 'r5' or 'x1e.' or 'z1d.' or 'h1.' or 'i3.metal' or 'i3en.') in instance_type:
        base_os_status(base_os, instance_type)
    elif base_os == 'centos7' and ('m5metal.' or 'a1.' or 'p3dn.24xlarge' or 'r5d.24xlarge' or 'r5d.metal' or 'r5.metal' or 'x1e.' or 'h1.' or 'i3en.') in instance_type:
        base_os_status(base_os, instance_type)
    elif base_os == 'ubuntu1404' and ('t1.' or 't3a.' or 'm5a' or 'm5d.' or 'm5.metal' or 'm1.' or 'a1.' or 'c5n.' or 'c5d.' or 'c1.' or 'f1.4xlarge' or 'p3dn.24xlarge' or 'r5' or 'm2.' or 'z1d.' or 'i3.metal' or 'i3en.') in instance_type:
        base_os_status(base_os, instance_type)
    elif base_os == 'ubuntu1604' and ('t1.' or 't3a.' or 'm5a' or 'm5d.metal' or 'm5.metal' or 'm1.' or 'a1.' or 'c1.' or 'r5ad.' or 'r5d.24xlarge' or 'r5d.metal' or 'r5.metal' or 'm2.' or 'z1d.metal' or 'i3en.') in instance_type:
        base_os_status(base_os, instance_type)
    elif base_os == 'ubuntu1804' and ('t1.' or 't3a.' or 'm5ad' or 'm5d.metal' or 'm5.metal' or 'm1.' or 'a1.' or 'c1.' or 'cc2.8xlarge' or 'r5ad.' or 'r5d.24xlarge' or 'm2.' or 'i3en.') in instance_type:
        base_os_status(base_os, instance_type)
    elif base_os == 'windows2019' and ('a1.' or 'f1.') in instance_type:
        base_os_status(base_os, instance_type)
    else:
        p_val('base_os', debug_mode)

# Function: check_custom_ami()
# Purpose: verify the existence of a user-provided custom AMI

def check_custom_ami(custom_ami, aws_account_id, region):
    import boto3
    import json
    ec2client = boto3.client('ec2', region_name = region)
    ami_information = ec2client.describe_images(
        Owners=[aws_account_id],
        Filters=[
            {'Name': 'architecture', 'Values': ['x86_64']},
            {'Name': 'image-id', 'Values': [custom_ami]},
            {'Name': 'state', 'Values': ['available']},
            {'Name': 'virtualization-type', 'Values': ['hvm']},
        ],
    )
    amis = sorted(ami_information['Images'],
        key=lambda x: x['CreationDate'],
        reverse=True)
    try:
        aws_ami = amis[0]['ImageId']
    except IndexError:
        return('false')
    else:
        return(aws_ami)

# Function: ctrlC_Abort()
# Purpose: Print an abort header, capture CTRL-C when pressed, and remove all
# of entities created by make-instance.py prior to capturing KeyboardInterrupt:
# orphaned state directories and files; EC2 security groups and keypairs; IAM
# roles, policies, and instance profiles

def ctrlC_Abort(sleep_time, line_length, vars_file_path, instance_data_dir, instance_serial_number_file, instance_serial_number, region, security_group_name, vpc_security_group_ids):
    import boto3
    import os
    import sys
    import time
    from botocore.exceptions import ClientError
    ec2client = boto3.client('ec2', region_name = region)
    iam = boto3.client('iam')
    ec2_keypair = instance_serial_number + '_' + region
    secret_key_file = instance_data_dir + ec2_keypair + '.pem'
    iam_instance_policy = 'ec2-instance-policy-' + str(instance_serial_number)
    iam_instance_profile = 'ec2-instance-profile-' + str(instance_serial_number)
    iam_instance_role = 'ec2-instance-role-' + str(instance_serial_number)
    print('')
    print(''.center(line_length, '#'))
    center_line = '    Please type CTRL-C within ' + str(sleep_time) + ' seconds to abort    '
    print(center_line.center(line_length, '#'))
    print(''.center(line_length, '#'))
    print('')
    try:
        time.sleep(sleep_time)
    except KeyboardInterrupt:
        if (vars_file_path == 1) and (cluster_serial_number_file == 1):
            print('')
            print('No orphaned files or directories were found.')
            print('')
        else:
            os.remove(instance_serial_number_file)
            os.remove(vars_file_path)
            print('')
            print('Removed: ' + instance_serial_number_file)
            print('Removed: ' + vars_file_path)
            print('')
        if (instance_serial_number == 1):
            print('')
            print('No IAM role or policy exists for this instance.')
            print('')
        else:
            try:
                iam.remove_role_from_instance_profile(InstanceProfileName=iam_instance_profile, RoleName=iam_instance_role)
                print('Removed: ' + iam_instance_profile + ' from ' + iam_instance_role)
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    print('No IAM EC2 instance profile exists to remove from the instance role!')
            try:
                iam.delete_instance_profile(InstanceProfileName=iam_instance_profile)
                print('Deleted: ' + iam_instance_profile)
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    print('No IAM EC2 instance profile exists for this instance!')
            try:
                iam.delete_role_policy(RoleName=iam_instance_role, PolicyName=iam_instance_policy)
                print('Deleted: ' + iam_instance_policy)
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    print('No IAM role policy exists for this instance!')
            try:
                iam.delete_role(RoleName=iam_instance_role)
                print('Deleted: ' + iam_instance_role)
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    print('No IAM role exists for this instance!')
            print('')
        try:
            ec2_sg_status = ec2client.delete_security_group(GroupId=vpc_security_group_ids)
            print('Deleted EC2 security group: ' + security_group_name)
            print('')
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroup.NotFound':
                print('No EC2 security group exists for this instance.')
                print('')
        try:
            ec2_keypair_status = ec2client.describe_key_pairs(KeyNames=[ec2_keypair])
            rm_ec2_keypair = ec2client.delete_key_pair(KeyName=ec2_keypair)
            os.remove(secret_key_file)
            print('Deleted EC2 keypair: ' + ec2_keypair)
            print('Deleted EC2 secret key file: ' + secret_key_file)
            print('')
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
                print('No EC2 keypair exists for this instance.')
        print('Aborting...')
        sys.exit(1)

# Function: ebs_encryption_check()
# Purpose: verify the instance_type supports EBS encryption

def ebs_encryption_check(instance_type, instance_name, debug_mode):
    global supported
    for item in ec2_instances_ebs_encryption:
        if item in instance_type:
            supported = True
    if supported:
        print('')
        print('Enabling: EBS encryption')
        print('build-ami.' + instance_name + '.sh will create encrypted AMIs!')
    else:
        error_msg = instance_type + ' does not support EBS encryption!'
        refer_to_docs_and_quit(error_msg)

# Function: ec2_placement_group_check()
# Purpose: verify the instance_type supports EC2 placement groups

def ec2_placement_group_check(instance_type, debug_mode):
    global supported
    for item in ec2_instances_placement_groups:
        if item in instance_type:
            supported = True
    if supported:
        pass
    else:
        error_msg = instance_type + ' does not support EC2 Placement Groups!'
        refer_to_docs_and_quit(error_msg)

# Function: get_ami_info()
# Purpose: get the ID of an AWS AMI image
# Source: http://cavaliercoder.com/blog/finding-the-latest-centos-ami.html

def get_ami_info(base_os, region):
    import boto3
    import json
    ec2client = boto3.client('ec2', region_name = region)
    if base_os == 'alinux2':
        ami_information = ec2client.describe_images(
            Owners=['137112412989'], # Amazon
            Filters=[
              {'Name': 'name', 'Values': ['amzn2-ami-hvm-2.0.*']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
            key=lambda x: x['CreationDate'],
            reverse=True)
        aws_ami = amis[0]['ImageId']
    if base_os == 'alinux':
        ami_information = ec2client.describe_images(
            Owners=['137112412989'], # Amazon
            Filters=[
              {'Name': 'name', 'Values': ['amzn-ami-hvm-*']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
            key=lambda x: x['CreationDate'],
            reverse=True)
        aws_ami = amis[0]['ImageId']
    if base_os == 'ubuntu1804':
        ami_information = ec2client.describe_images(
            Filters=[
              {'Name': 'name', 'Values': ['ubuntu/images/hvm-ssd/ubuntu-bionic-18.04-amd64-server-*']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
            key=lambda x: x['CreationDate'],
            reverse=True)
        aws_ami = amis[0]['ImageId']
    if base_os == 'ubuntu1604':
        ami_information = ec2client.describe_images(
            Filters=[
              {'Name': 'name', 'Values': ['ubuntu/images/hvm-ssd/ubuntu-xenial-16.04-amd64-server-*']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
            key=lambda x: x['CreationDate'],
            reverse=True)
        aws_ami = amis[0]['ImageId']
    if base_os == 'ubuntu1404':
        ami_information = ec2client.describe_images(
            Filters=[
              {'Name': 'name', 'Values': ['ubuntu/images-testing/hvm-ssd/ubuntu-trusty-daily-amd64-server-*']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
            key=lambda x: x['CreationDate'],
            reverse=True)
        aws_ami = amis[0]['ImageId']
    if base_os == 'centos6':
        ami_information = ec2client.describe_images(
            Owners=['679593333241'], # CentOS
            Filters=[
              {'Name': 'name', 'Values': ['CentOS Linux 6 x86_64 HVM EBS *']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
                      key=lambda x: x['CreationDate'],
                      reverse=True)
        aws_ami = amis[0]['ImageId']
    if base_os == 'centos7':
        ami_information = ec2client.describe_images(
            Owners=['679593333241'], # CentOS
            Filters=[
              {'Name': 'name', 'Values': ['CentOS Linux 7 x86_64 HVM EBS *']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
              key=lambda x: x['CreationDate'],
              reverse=True)
        aws_ami = amis[0]['ImageId']
    if base_os == 'windows2019':
        ami_information = ec2client.describe_images(
            Owners=['801119661308'], # Windows Server 2019
            Filters=[
              {'Name': 'name', 'Values': ['Windows_Server-2019-English-Full-Base-*']},
              {'Name': 'architecture', 'Values': ['x86_64']},
              {'Name': 'root-device-type', 'Values': ['ebs']},
              {'Name': 'virtualization-type', 'Values': ['hvm']},
            ],
        )
        amis = sorted(ami_information['Images'],
              key=lambda x: x['CreationDate'],
              reverse=True)
        aws_ami = amis[0]['ImageId']
    return(aws_ami)

# Function: menuCount()
# Purpose: iterate through a list from item_value=low to item_value=high

def menuCount(low, high):
    counter = 0
    def tmp():
        nonlocal counter
        item_value = low + counter
        if item_value < high:
            counter += 1
            return item_value
        return None
    return iter(tmp, None)

# Function: illegal_az_msg()
# Purpose: abort when an invalid AvailabilityZone is provided

def illegal_az_msg(az):
    import sys
    print('*** ERROR ***')
    print('"' + az + '"' + ' is not a valid Availability Zone in the selected AWS Region.')
    print('Aborting...')
    sys.exit(1)

# Function: modify_iam_policy_document()
# Purpose: modify the generic JSON policy document to limit the scope of IAM
# modification rights to roles, instance profiles, and policies prepended with
# iam_name_prefix 

def modify_iam_policy_document(instance_json_policy_src, instance_json_policy_stage, iam_name_prefix, instance_serial_number):
    with open(instance_json_policy_src, 'r') as ec2_iam_role_src:
        role_stage_0 = ec2_iam_role_src.read()
        ec2_iam_role_src.close()
        role_stage_1 = role_stage_0.replace('<EC2_POLICY>', iam_name_prefix + '-policy-' + instance_serial_number)
        role_stage_2 = role_stage_1.replace('<EC2_ROLE>', iam_name_prefix + '-role-' + instance_serial_number)
        role_stage_3 = role_stage_2.replace('<EC2_INSTANCE_PROFILE>', iam_name_prefix + '-profile-' + instance_serial_number)
        filedata = role_stage_3
    with open(instance_json_policy_stage, 'w') as ec2_iam_role_dest:
        ec2_iam_role_dest.write(filedata)
        ec2_iam_role_dest.close()

# Function: p_fail()
# Purpose: print a failed instance_parameter validation message to stdout

def p_fail(p, q, r):
    import sys
    import textwrap
    print('')
    print("*** Error ***")
    if r == 'missing_element':
        print('"' + p + '"' + ' seems to be missing as a valid ' + q + '.')
    else:
        print('"' + p + '"' + ' is not a valid option for ' + q + '.')
        print("Supported values:")
        r = '\t'.join(r)
        print('\n'.join(textwrap.wrap(r, 78)))
    print('')
    print("Aborting...")
    sys.exit(1)

# Function: p_val()
# Purpose: print a successful instance_parameter validation message to stdout

def p_val(p, debug_mode):
    if debug_mode == 'True' or debug_mode == 'true':
        print(p + " successfully validated")
    else:
        pass

# Function print_TextHeader()
# Purpose: print a centered text header to support validation and reviewing
# of instance_parameters.

def print_TextHeader(p, action, line_length):
    print('')
    print(''.center(line_length, '-'))
    T2C = action + ' parameter values for ' + p
    print(T2C.center(line_length))
    print(''.center(line_length, '-'))

# Function: refer_to_docs_and_quit()
# Purpose: print an error message, refer to the AWS ParallelCluster public
# documentation, and quit with a non-successful error code.

def refer_to_docs_and_quit(error_msg):
    import sys
    print('*** ERROR ***')
    print(error_msg)
    print('')
    print('Please resolve this error and retry the instance build.')
    print('Aborting...')
    sys.exit(1)

# Function: time_waiter(duration, interval):
# Purpose: given a duration, print '.'  to the console every interval seconds.

def time_waiter(duration, interval):
    import sys
    import time
    step = 1
    for i in range(0, duration, step):
        print('.', end=''),
        time.sleep(interval)
        sys.stdout.flush() 

# There doesn't seem to be an easy way to dynamically list available EC2
# instances # based on functionality or availability on a per-region (or AZ)
# basis, so instead define static lists from the EC2 public documentation
# that can be referenced programatically.

# EBS Optimized Instances

ec2_instances_ebs_optimized = ['c4.large', 'c4.xlarge', 'c4.2xlarge', 'c4.4xlarge', 'c4.8xlarge', 'c5.large', 'c5.xlarge', 'c5.2xlarge', 'c5.4xlarge', 'c5.9xlarge', 'c5.18xlarge', 'c5d.large', 'c5d.xlarge', 'c5d.2xlarge', 'c5d.4xlarge', 'c5d.9xlarge', 'c5d.18xlarge', 'd2.xlarge', 'd2.2xlarge', 'd2.4xlarge', 'd2.8xlarge', 'f1.2xlarge', 'f1.16xlarge', 'g3.4xlarge', 'g3.8xlarge', 'g3.16xlarge', 'h1.2xlarge', 'h1.4xlarge', 'h1.8xlarge', 'h1.16xlarge', 'i3.large', 'i3.xlarge', 'i3.2xlarge', 'i3.4xlarge', 'i3.8xlarge', 'i3.16xlarge', 'i3.metal', 'm4.large', 'm4.xlarge', 'm4.2xlarge', 'm4.4xlarge', 'm4.10xlarge', 'm4.16xlarge', 'm5.large', 'm5.xlarge', 'm5.2xlarge', 'm5.4xlarge', 'm5.8xlarge', 'm5.12xlarge', 'm5.16xlarge', 'm5.24xlarge', 'm5d.large', 'm5d.xlarge', 'm5d.2xlarge', 'm5d.4xlarge', 'm5d.12xlarge', 'm5d.24xlarge', 'p2.xlarge', 'p2.8xlarge', 'p2.16xlarge', 'p3.2xlarge', 'p3.8xlarge', 'p3.16xlarge', 'r4.large', 'r4.xlarge', 'r4.2xlarge', 'r4.4xlarge', 'r4.8xlarge', 'r4.16xlarge', 'r5.large', 'r5.xlarge', 'r5.2xlarge', 'r5.4xlarge', 'r5.8xlarge', 'r5.12xlarge', 'r5.16xlarge', 'r5.24xlarge', 'r5.metal', 'r5d.large', 'r5d.xlarge', 'r5d.2xlarge', 'r5d.4xlarge', 'r5d.8xlarge', 'r5d.12xlarge', 'r5d.16xlarge', 'r5d.24xlarge', 'r5d.metal', 'r5a.large', 'r5a.xlarge', 'r5a.2xlarge', 'r5a.4xlarge', 'r5d.8xlarge', 'r5a.12xlarge', 'r5d.16xlarge', 'r5a.24xlarge', 'r5ad.large', 'r5ad.xlarge', 'r5ad.2xlarge', 'r5ad.4xlarge', 'r5ad.12xlarge', 'r5ad.24xlarge', 'x1.16xlarge', 'x1.32xlarge', 'x1e.xlarge', 'x1e.2xlarge', 'x1e.4xlarge', 'x1e.8xlarge', 'x1e.16xlarge', 'x1e.32xlarge']

# General Purpose Instances

ec2_instances_general_purpose = ['a1.medium', 'a1.large', 'a1.xlarge', 'a1.2xlarge', 'a1.4xlarge', 't2.nano', 't2.micro', 't2.small', 't2.medium', 't2.large', 't2.xlarge', 't2.2xlarge', 't3.nano', 't3.micro', 't3.small', 't3.medium', 't3.large', 't3.xlarge', 't3.2xlarge', 't3a.nano', 't3a.micro', 't3a.small', 't3a.medium', 't3a.large', 't3a.xlarge', 't3a.2xlarge', 'm4.large', 'm4.xlarge', 'm4.2xlarge', 'm4.4xlarge', 'm4.10xlarge', 'm4.16xlarge', 'm5.large', 'm5.xlarge', 'm5.2xlarge', 'm5.4xlarge', 'm5.8xlarge', 'm5.12xlarge', 'm5.16xlarge', 'm5.24xlarge', 'm5d.large', 'm5d.xlarge', 'm5d.2xlarge', 'm5d.4xlarge', 'm5d.12xlarge', 'm5d.24xlarge', 'm5a.large', 'm5a.xlarge', 'm5a.2xlarge', 'm5a.4xlarge', 'm5a.8xlarge', 'm5a.12xlarge', 'm5a.16xlarge', 'm5a.24xlarge', 'm5ad.large', 'm5ad.xlarge', 'm5ad.2xlarge', 'm5ad.4xlarge', 'm5ad.8xlarge', 'm5ad.12xlarge', 'm5ad.16xlarge', 'm5ad.24xlarge']

# Compute Optimized Instances

ec2_instances_compute_optimized = ['c4.large', 'c4.xlarge', 'c4.2xlarge', 'c4.4xlarge', 'c4.8xlarge', 'c5.large', 'c5.xlarge', 'c5.2xlarge', 'c5.4xlarge', 'c5.9xlarge', 'c5.18xlarge', 'c5d.xlarge', 'c5d.2xlarge', 'c5d.4xlarge', 'c5d.9xlarge', 'c5d.18xlarge', 'c5n.18xlarge']

# Memory Optimized Instances

ec2_instances_memory_optimized = ['r4.large', 'r4.xlarge', 'r4.2xlarge', 'r4.4xlarge', 'r4.8xlarge', 'r4.16xlarge', 'r5.large', 'r5.xlarge', 'r5.2xlarge', 'r5.4xlarge', 'r5.8xlarge', 'r5.12xlarge', 'r5.16xlarge', 'r5.24xlarge', 'r5.metal', 'r5d.large', 'r5d.xlarge', 'r5d.2xlarge', 'r5d.4xlarge', 'r5d.8xlarge', 'r5d.12xlarge', 'r5d.16xlarge', 'r5d.24xlarge', 'r5d.metal', 'r5a.large', 'r5a.xlarge', 'r5a.2xlarge', 'r5a.4xlarge', 'r5d.8xlarge', 'r5a.12xlarge', 'r5d.16xlarge', 'r5a.24xlarge', 'r5ad.large', 'r5ad.xlarge', 'r5ad.2xlarge', 'r5ad.4xlarge', 'r5ad.12xlarge', 'r5ad.24xlarge', 'x1.16xlarge', 'x1.32xlarge', 'x1e.xlarge', 'x1e.2xlarge', 'x1e.4xlarge', 'x1e.8xlarge', 'x1e.16xlarge', 'x1e.32xlarge', 'z1d.large', 'z1d.xlarge', 'z1d.2xlarge', 'z1d.3xlarge', 'z1d.6xlarge', 'z1d.12xlarge', 'z1d.metal']

# High Memory Instances

ec2_instances_high_memory = ['u-6tb1.metal', 'u-9tb1.metal', 'u-12tb1.metal']

# Storage Optimized Instances

ec2_instances_storage_optimized = ['d2.xlarge', 'd2.2xlarge', 'd2.4xlarge', 'd2.8xlarge', 'h1.2xlarge', 'h1.4xlarge', 'h1.8xlarge', 'h1.16xlarge', 'i3.large', 'i3.xlarge', 'i3.2xlarge', 'i3.4xlarge', 'i3.8xlarge', 'i3.16xlarge', 'i3.metal']

# Accelerated Computing Instances

ec2_instances_accelerated_computing = ['f1.2xlarge', 'f1.4x.large', 'f1.16xlarge', 'g3s.xlarge', 'g3.4xlarge', 'g3.8xlarge', 'g3.16xlarge', 'p2.xlarge', 'p2.8xlarge', 'p2.16xlarge', 'p3.2xlarge', 'p3.8xlarge', 'p3.16xlarge', 'p3dn.24xlarge']

# Full list of supported instance types

ec2_instances_full_list = ec2_instances_general_purpose + ec2_instances_compute_optimized + ec2_instances_memory_optimized + ec2_instances_high_memory + ec2_instances_storage_optimized + ec2_instances_accelerated_computing

# Instances that support EC2 Placement Groups

ec2_instances_placement_groups = ['a1.', 'm4.', 'm5.', 'c3.', 'c4.', 'c5.', 'cr1.8xlarge', 'r3.', 'r4.', 'r5.', 'x1.', 'x1e.', 'z1d.', 'd2.', 'h1.', 'hs1.8xlarge', 'i2.', 'i3.', 'i3en.', 'f1.', 'g2.', 'g3.', 'p2.', 'p3']

# Instances that support EBS encryption

ec2_instances_ebs_encryption = ['a1.', 't2.', 't3', 'm3.', 'm4.', 'm5.', 'm5a.', 'm5ad.', 'm5d.', 'c3.', 'c4.', 'c5.', 'c5.metal', 'c5d.', 'c5n.', 'cr1.8xlarge', 'r3.', 'r4.', 'r5', 'x1.', 'x1e.', 'z1d.', 'd2.', 'h1.2xlarge', 'hs1.4xlarge', 'i2.', 'i3', 'f1.', 'g2.', 'g3.', 'p2.', 'p3', 'i3.metal', 'm5.metal', 'm5d.metal', 'r5.metal', 'r5d.metal', 'u-6tb1.metal', 'u-9tb1.metal', 'u-12tb1.metal', 'z1d.metal']

# Unsupported instance types by operating system

ec2_instances_unsupported_alinux = null_list
ec2_instances_unsupported_alinux2 = null_list
ec2_instances_unsupported_centos6 = ['t3.', 'm5.', 'a1.', 'c5.', 'f1.4xlarge', 'g3s.xlarge', 'p3.', 'r5.', 'x1e.', 'z1d.', 'h1.', 'i3.metal', 'i3en.']
ec2_instances_unsupported_centos7 = ['m5metal.', 'a1.', 'p3dn.24xlarge', 'r5d.24xlarge', 'r5d.metal', 'r5.metal', 'x1e.', 'h1.', 'i3en.']
ec2_instances_unsupported_ubuntu1404 = ['t1.', 't3a.', 'm5a.', 'm5d.', 'm5.metal', 'm1.', 'a1.', 'c5n.', 'c5d.', 'c1.', 'f1.4xlarge', 'p3dn.24xlarge', 'r5.', 'm2.', 'z1d.', 'i3.metal', 'i3en.']
ec2_instances_unsupported_ubuntu1604 = ['t1.', 't3a.', 'm5a.', 'm5d.metal', 'm5.metal', 'm1.', 'a1.', 'c1.', 'r5.ad.', 'r5.d.24xlarge', 'r5.d.metal', 'r5..metal', 'm2.', 'z1d.metal', 'i3en.']
ec2_instances_unsupported_ubuntu1804 = ['t1.', 't3a.', 'm5a.d', 'm5d.metal', 'm5.metal', 'm1.', 'a1.', 'c1.', 'cc2.8xlarge', 'r5.ad.', 'r5.d.24xlarge', 'm2.', 'i3en.']
ec2_instances_unsupported_windows2019 = ['a1.', 'f1.']
