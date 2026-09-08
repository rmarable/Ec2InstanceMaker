################################################################################
# Name:		instance_builder.py
# Purpose:	Pure, independently-testable pieces of make-instance.py's build
# 		flow, extracted incrementally (see CLAUDE-STATE.md for the
# 		rationale and the phased extraction plan this is part of).
#
# make-instance.py is a ~1200-line linear script with no functions and heavy
# implicit state-threading between sections -- there was previously no way
# to unit-test any of its logic without mocking the entire script. This
# module is where extracted pieces land: each function here takes its
# dependencies as explicit arguments (no hidden globals, no reliance on
# execution order) and is covered by tests/test_instance_builder.py.
#
# Not every phase belongs here yet -- only the ones extracted so far. See
# CLAUDE-STATE.md for what's been moved and what's still inline in
# make-instance.py.
################################################################################

import ipaddress
import os
import sys
import time
from datetime import datetime as DateTime

from botocore.exceptions import ClientError

# Function: validate_az_and_region()
# Purpose: abort cleanly if the selected AWS Region/Availability Zone is
# invalid. Must run before any other AWS API call that doesn't itself
# handle a bad region/AZ cleanly -- describe_availability_zones is the
# first call to hit a bogus region with a recognizable, catchable error.


def validate_az_and_region(ec2_client, az, illegal_az_msg):
    import botocore

    try:
        ec2_client.describe_availability_zones()
    except ValueError:
        illegal_az_msg(az)
    except botocore.exceptions.EndpointConnectionError:
        illegal_az_msg(az)


# Function: generate_instance_serial_number()
# Purpose: derive the instance_serial_number and the human-readable
# deployment date/tag strings used throughout the build. `now` defaults to
# the real current time; tests pass a fixed value for determinism.


def generate_instance_serial_number(instance_name, now=None):
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


def generate_sns_timestamps(now=None):
    if now is None:
        now = DateTime.utcnow()
    sns_datestamp = now.strftime("%m") + "-" + now.strftime("%d") + "-" + now.strftime("%Y")
    sns_timestamp = now.strftime("%H") + ":" + now.strftime("%M")
    return sns_datestamp, sns_timestamp


# Function: validate_and_resize_ebs_volumes()
# Purpose: enforce the 16 TB EBS size ceiling, bump undersized root/device
# volumes up to AWS's recommended 30 GB minimum for Windows Server, and
# validate provisioned-IOPS bounds when ebs_root_volume_type == "io1".
# Returns the (possibly Windows-adjusted) root/device volume sizes.


def validate_and_resize_ebs_volumes(
    ebs_root_volume_size,
    ebs_device_volume_size,
    ebs_root_volume_type,
    ebs_root_volume_iops,
    ebs_device_volume_iops,
    is_windows,
    refer_to_docs_and_quit,
):
    if ebs_root_volume_size > 16000:
        refer_to_docs_and_quit("Maximum allowed EBS volume size is 16 TB (16000 GB)!")
    if ebs_device_volume_size > 16000:
        refer_to_docs_and_quit("Maximum allowed secondary EBS device volume size is 16 TB (16000 GB)!")
    if is_windows:
        if ebs_root_volume_size <= 30:
            ebs_root_volume_size = 30
        if ebs_device_volume_size <= 30:
            ebs_device_volume_size = 30
    if ebs_root_volume_type == "io1":
        if (ebs_root_volume_iops == 0) or (ebs_root_volume_iops > 16000):
            refer_to_docs_and_quit("ebs_root_volume_iops must be set to a value between 100 and 16,000!")
        if (ebs_device_volume_iops == 0) or (ebs_device_volume_iops > 16000):
            refer_to_docs_and_quit("ebs_device_volume_iops must be set to a value between 100 and 16,000!")
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


def resolve_vpc_and_subnet(ec2_client, vpc_name, az, refer_to_docs_and_quit):
    if vpc_name == "vpc_default":
        vpc_information = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    else:
        vpc_information = ec2_client.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [vpc_name]}])

    for vpc in vpc_information["Vpcs"]:
        vpc_id = vpc["VpcId"]
        try:
            vpc_name = vpc_information["Vpcs"][0]["Tags"][0]["Value"]
        except KeyError:
            refer_to_docs_and_quit(vpc_id + " lacks a valid Name tag! This will break Terraform.")

    try:
        subnet_information = ec2_client.describe_subnets(
            Filters=[
                {"Name": "availabilityZone", "Values": [az]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ],
        )
    except NameError:
        refer_to_docs_and_quit('"' + vpc_name + '" is an undefined VPC!')
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
# the point of this function existing. Anything else must at least parse
# as a real CIDR block; AWS's own error for a malformed CidrIp is not
# operator-friendly.


def resolve_ssh_allowed_ips(ec2_client, vpc_id, ssh_allowed_ips, refer_to_docs_and_quit):
    if ssh_allowed_ips == "UNDEFINED":
        vpc_info = ec2_client.describe_vpcs(VpcIds=[vpc_id])
        return vpc_info["Vpcs"][0]["CidrBlock"]
    if ssh_allowed_ips == "0.0.0.0/0":
        refer_to_docs_and_quit("--ssh_allowed_ips may not be 0.0.0.0/0! This would expose the instance's SSH/RDP port to the entire internet.")
    try:
        ipaddress.ip_network(ssh_allowed_ips, strict=False)
    except ValueError:
        refer_to_docs_and_quit('"' + ssh_allowed_ips + '" is not a valid CIDR block!')
    return ssh_allowed_ips


# Function: resolve_security_group()
# Purpose: reuse the named EC2 security group if it already exists,
# otherwise create it with the appropriate inbound rule for the base_os
# (RDP/3389 for Windows, SSH/22 otherwise), scoped to ssh_allowed_ips
# (see resolve_ssh_allowed_ips() above -- never 0.0.0.0/0). Returns the
# resolved security_group_name (never the boto3 resource object -- the
# original inline code reassigned a `security_group` local from a string
# to a resource object mid-flow, which this extraction deliberately avoids
# propagating) and the parsed vpc_security_group_ids string.


def resolve_security_group(ec2, region, security_group_name, instance_serial_number, vpc_id, is_windows, ssh_allowed_ips, add_inbound_security_group_rule):
    if security_group_name == "ec2instancemaker_sg":
        security_group_name = security_group_name + "_" + instance_serial_number
    filters = [{"Name": "group-name", "Values": [security_group_name]}]
    sg_id = list(ec2.security_groups.filter(Filters=filters))
    if not sg_id:
        security_group = ec2.create_security_group(GroupName=security_group_name, Description="EC2 security group - created by Ec2InstanceMaker", VpcId=vpc_id)
        if is_windows:
            add_inbound_security_group_rule(region, security_group, "tcp", ssh_allowed_ips, 3389, 3389)
        else:
            add_inbound_security_group_rule(region, security_group, "tcp", ssh_allowed_ips, 22, 22)
        sg_id = list(ec2.security_groups.filter(Filters=filters))
    v_sg_id = str(*sg_id).split("'")
    vpc_security_group_ids = v_sg_id[1]
    return security_group_name, vpc_security_group_ids


# Function: setup_keypair()
# Purpose: reuse the named EC2 keypair if it already exists in this region,
# otherwise create it and write its private key material to
# secret_key_file with 0600 permissions. Aborts (via sys.exit, matching
# the original inline code's operator-facing guidance message, not
# refer_to_docs_and_quit) if the keypair exists in AWS but the local
# secret_key_file is missing -- that mismatch needs a human decision
# (delete and recreate the AWS-side keypair, or find the missing file),
# not a generic error.


def setup_keypair(ec2_client, ec2_keypair, secret_key_file, region, debug_mode, refer_to_docs_and_quit):
    try:
        ec2_client.describe_key_pairs(KeyNames=[ec2_keypair])
        if debug_mode == "true":
            print("")
        print("Found EC2 keypair: " + ec2_keypair)
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidKeyPair.NotFound":
            new_ec2_keypair = ec2_client.create_key_pair(KeyName=ec2_keypair)
            with open(secret_key_file, "w") as fh:
                print(new_ec2_keypair["KeyMaterial"], file=fh)
            os.chmod(secret_key_file, 0o600)
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
        print("$ aws --region " + region + " ec2 delete-key-pair --key-name " + ec2_keypair)
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


def resolve_ami(custom_ami, base_os, region, architecture, aws_account_id, get_ami_info, check_custom_ami, refer_to_docs_and_quit):
    if custom_ami == "UNDEFINED":
        return get_ami_info(base_os, region, architecture)
    aws_ami = check_custom_ami(custom_ami, aws_account_id, region, architecture)
    if aws_ami == "false":
        refer_to_docs_and_quit('AMI image "' + custom_ami + '" is unavailable in this AWS account!')
    return aws_ami


# Function: fetch_spot_price_raw()
# Purpose: look up the most recent EC2 Spot price for instance_type/az.


def fetch_spot_price_raw(ec2_client, instance_type, is_windows, az):
    product_description = "Windows" if is_windows else "Linux/UNIX"
    prices = ec2_client.describe_spot_price_history(InstanceTypes=[instance_type], MaxResults=1, ProductDescriptions=[product_description], AvailabilityZone=az)
    return float(prices["SpotPriceHistory"][0]["SpotPrice"])


# Function: compute_buffered_spot_price()
# Purpose: apply spot_buffer to a raw historical Spot price, rounded to 8
# decimal places, to protect against Spot market fluctuations:
# spot_price = spot_price_raw + (spot_buffer * spot_price_raw)


def compute_buffered_spot_price(spot_price_raw, spot_buffer):
    return round(spot_price_raw + (spot_buffer * spot_price_raw), 8)


# Function: build_sns_message()
# Purpose: construct the SNS notification body/subject announcing instance
# (or instance family) creation.


def build_sns_message(count, instance_name, instance_type, request_type, sns_datestamp, sns_timestamp):
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


def apply_terraform(instance_data_dir, debug_mode):
    import subprocess

    tf_env = {**os.environ, "TF_LOG": "DEBUG"} if debug_mode == "true" else None
    subprocess.run(["terraform", "init", "-input=false"], cwd=instance_data_dir, env=tf_env)
    subprocess.run(["terraform", "plan", "-out", "terraform_environment"], cwd=instance_data_dir, env=tf_env)
    subprocess.run(["terraform", "apply", "terraform_environment"], cwd=instance_data_dir, env=tf_env)


# Function: build_security_group_tags()
# Purpose: construct the EC2 tag set applied to the security group after
# the Terraform apply completes.


def build_security_group_tags(security_group_name, instance_name, instance_serial_number, instance_owner, instance_owner_email, instance_owner_department, deployment_date_tag, project_id):
    tags = [
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
# Purpose: parse instance_id/instance_name/ip_address for Windows
# instance(s) out of `terraform show` output. The three `terraform show |
# grep | awk` pipelines are static commands with no interpolated data
# (the nosec markers below match the identical pattern already used
# elsewhere in this codebase), so shell=True is not a security concern
# here.


def fetch_windows_instance_details(instance_data_dir):
    import subprocess

    instance_id_tf = subprocess.run("terraform show | grep instance_id | awk '{print $3}'", stdout=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL, cwd=instance_data_dir)  # nosec B602 - static command, no interpolation
    instance_id = instance_id_tf.stdout.decode("utf-8").replace('"', "").strip()
    instance_name_tf = subprocess.run("terraform show | grep instance_name_index | awk '{print $3}'", stdout=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL, cwd=instance_data_dir)  # nosec B602 - static command, no interpolation
    instance_name = instance_name_tf.stdout.decode("utf-8").replace('"', "").strip()
    ip_addr_tf = subprocess.run("terraform show | grep instance_ip_addresses | awk '{print $3}'", stdout=subprocess.PIPE, shell=True, stderr=subprocess.DEVNULL, cwd=instance_data_dir)  # nosec B602 - static command, no interpolation
    ip_address = ip_addr_tf.stdout.decode("utf-8").replace('"', "").strip()
    return instance_id, instance_name, ip_address


# Function: decrypt_windows_admin_passwords()
# Purpose: decrypt the Windows Administrator password for each instance ID
# in instance_ids_csv (a comma-separated string, matching Terraform's
# multi-instance output format), using the EC2 keypair's private key.
# Returns a comma-joined string in the same order as instance_ids_csv, so
# it zips positionally against instance names/IPs parsed separately.


def decrypt_windows_admin_passwords(instance_data_dir, ec2_keypair, instance_ids_csv):
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
        password = jq("..|.PasswordData?").transform(text=password_tf.stdout.decode("utf-8"), text_output=True)
        password = password.replace('"', "").strip()
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


def derive_iam_names(iam_name_prefix, instance_serial_number):
    return (
        iam_name_prefix + "-role-" + instance_serial_number,
        iam_name_prefix + "-policy-" + instance_serial_number,
        iam_name_prefix + "-profile-" + instance_serial_number,
    )


# Function: ensure_iam_role_created()
# Purpose: create the IAM role + policy from the staged JSON policy
# document if it doesn't already exist (the iam_role == "UNDEFINED" path).


def ensure_iam_role_created(iam, role_name, policy_name, instance_json_policy_stage, instance_json_policy_template, debug_mode, refer_to_docs_and_quit):
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


def ensure_iam_role_exists(iam, role_name, debug_mode, refer_to_docs_and_quit):
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


def ensure_iam_instance_profile(iam, profile_name, role_name, debug_mode, refer_to_docs_and_quit):
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


def setup_iam(iam, iam_role, iam_name_prefix, iam_json_policy, instance_data_dir, instance_serial_number, debug_mode, refer_to_docs_and_quit, modify_iam_policy_document):
    if iam_role == "UNDEFINED":
        role_name, policy_name, profile_name = derive_iam_names(iam_name_prefix, instance_serial_number)
        instance_json_policy_src = "templates/" + iam_json_policy
        instance_json_policy_stage = instance_data_dir + "stage-" + iam_json_policy
        instance_json_policy_template = instance_data_dir + iam_json_policy
        preserve_iam_role = "false"
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


# Function: write_vars_file()
# Purpose: render vars_file_template with instance_parameters and write it
# to vars_file_path -- the human-readable per-instance audit record.


def write_vars_file(vars_file_path, vars_file_template, instance_parameters):
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


def resolve_custom_user_scripts(names, custom_user_scripts_dir, refer_to_docs_and_quit):
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
