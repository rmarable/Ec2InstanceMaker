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

import errno
import ipaddress
import os
import sys
import time
from datetime import UTC
from datetime import datetime as DateTime

import boto3
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
        now = DateTime.now(UTC)
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
            refer_to_docs_and_quit(
                vpc_id
                + " lacks a valid Name tag! This will break Terraform. Tag it and retry:\n\n"
                + "aws --region "
                + az[:-1]
                + " ec2 create-tags --resources "
                + vpc_id
                + " --tags Key=Name,Value="
                + vpc_id
            )

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
# Purpose: fetch instance_id/instance_name/ip_address for Windows
# instance(s) from Terraform's own structured output
# (`terraform output -json`) -- DEFAULT_EC2_TEMPLATE.j2 defines
# instance_id_list/instance_name_index/instance_ip_addresses as real
# Terraform outputs, so this reads them directly instead of grep/awk-ing
# `terraform show`'s human-readable text. One list-form subprocess call,
# no shell=True, no dependency on grep/awk being on PATH, and no fragile
# text-format parsing that Terraform's human-readable output isn't
# actually guaranteed to preserve across versions the way `-json` is.


def fetch_windows_instance_details(instance_data_dir):
    import json
    import subprocess

    result = subprocess.run(["terraform", "output", "-json"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=instance_data_dir)
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


# Function: setup_cloudwatch_logging()
# Purpose: create (or reuse) the per-instance CloudWatch Logs group the
# CloudWatch Agent ships logs to, and set its retention policy. Done here
# via boto3, before Terraform ever runs, rather than from within the
# instance's own cloud-init -- so the group exists with the *correct*
# retention policy before the agent starts writing to it, instead of
# racing the agent's own auto-create-with-indefinite-retention behavior.


def setup_cloudwatch_logging(logs_client, log_group_name, log_retention_days, refer_to_docs_and_quit):
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

# Function: validate_instance_name_and_owner_casing()
# Purpose: reject instance_name/instance_owner values containing uppercase
# letters -- both feed into AWS resource names and tags where mixed case
# has caused real problems before (S3/DNS-style naming rules elsewhere in
# AWS), so this is caught here rather than surfacing as a confusing
# downstream API error.


def validate_instance_name_and_owner_casing(instance_name, instance_owner, refer_to_docs_and_quit):
    if any(char.isupper() for char in instance_name) or any(char.isupper() for char in instance_owner):
        refer_to_docs_and_quit("instance_name and instance_owner may not contain uppercase letters!")


# Function: get_terraform_version()
# Purpose: get the installed Terraform version, aborting with a clear
# message if Terraform is missing. Replaces the original
# `subprocess.check_output("terraform -version | head -1 | awk '{print
# $2}'", shell=True, ...)` pipeline -- list-form `terraform -version` with
# the version token parsed in Python needs no shell=True and no dependency
# on head/awk being on PATH too. A missing `terraform` binary now raises a
# catchable FileNotFoundError instead of silently producing empty output
# for the original code's `if not TERRAFORM_VERSION:` check to notice.


def get_terraform_version(refer_to_docs_and_quit, run=None):
    import subprocess

    if run is None:
        run = subprocess.run
    try:
        result = run(["terraform", "-version"], capture_output=True, text=True)
    except FileNotFoundError:
        refer_to_docs_and_quit("Terraform is missing! Please visit: https://www.terraform.io/downloads")
        return None
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    parts = first_line.split()
    if len(parts) < 2:
        refer_to_docs_and_quit("Terraform is missing! Please visit: https://www.terraform.io/downloads")
        return None
    return parts[1]


# Function: create_aws_clients()
# Purpose: construct every boto3 client/resource make_instance.py needs in
# one place -- a single seam for tests to mock instead of patching
# boto3.client globally with per-service dispatch logic. Constructing a
# boto3 client/resource performs no network I/O by itself (that only
# happens when a method on it is actually called), so bundling every
# client's construction here, ahead of where each was previously created
# piecemeal through the build flow, changes nothing observable.


class AwsClients:
    def __init__(self, ec2_client, ec2, iam, sns_client, stsclient):
        self.ec2_client = ec2_client
        self.ec2 = ec2
        self.iam = iam
        self.sns_client = sns_client
        self.stsclient = stsclient


def create_aws_clients(region, boto3_client=boto3.client, boto3_resource=boto3.resource):
    return AwsClients(
        ec2_client=boto3_client("ec2", region_name=region),
        ec2=boto3_resource("ec2", region_name=region),
        iam=boto3_client("iam"),
        sns_client=boto3_client("sns", region_name=region),
        stsclient=boto3_client("sts", region_name=region, endpoint_url="https://sts." + region + ".amazonaws.com"),
    )


# Function: ensure_state_directories()
# Purpose: idempotently create the three top-level local state directories
# this tool writes into.


def ensure_state_directories(instance_data_dir):
    for directory in ("./vars_files", instance_data_dir, "./active_instances"):
        try:
            os.makedirs(directory)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise


# Function: abort_if_vars_file_exists()
# Purpose: refuse to proceed if vars_file_path already exists -- an
# existing vars_file means this instance_name was already built, and
# silently building over it would corrupt local state. Prints the exact
# commands an operator needs to clear it and retry. Exits directly via
# sys.exit(1) (matching setup_keypair()'s precedent above) rather than
# refer_to_docs_and_quit, since this bespoke multi-line guidance message
# predates that helper and shouldn't be wrapped in its generic boilerplate.


def abort_if_vars_file_exists(vars_file_path, argv):
    if not os.path.isfile(vars_file_path):
        return
    print("")
    print("  WARNING  ".center(80, "*"))
    print(("  Found an existing " + vars_file_path + " ").center(80, "-"))
    print("")
    print("Please delete this file and retry the build:")
    print("")
    print("rm " + vars_file_path)
    print(" ".join(argv))
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


def write_serial_number_file(instance_serial_number_file, instance_name, instance_serial_datestamp, argv):
    if os.path.isfile(instance_serial_number_file):
        return
    with open(instance_serial_number_file, "w") as fh:
        print(f"{instance_name}.{instance_serial_datestamp}", file=fh)
    with open(instance_serial_number_file, "a") as fh:
        print(" ".join(argv), file=fh)


# Function: resolve_ebs_optimized_support()
# Purpose: downgrade ebs_optimized to "false" with a console warning if the
# selected instance_type doesn't support it, rather than letting Terraform
# fail later with an opaque AWS error. A no-op (returns ebs_optimized
# unchanged) if ebs_optimized was already "false".


def resolve_ebs_optimized_support(ebs_optimized, instance_type, ebs_optimized_support, instance_name):
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


def resolve_request_type_pricing(request_type, ec2_client, instance_type, is_windows, az, spot_buffer, debug_mode, fetch_spot_price_raw, compute_buffered_spot_price, p_val):
    if request_type == "ondemand":
        print("")
        print("Selected: ondemand (NOTE: spot instances are **MUCH** cheaper!)")
        return "UNDEFINED", "UNDEFINED"
    spot_price_raw = fetch_spot_price_raw(ec2_client, instance_type, is_windows, az)
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


def resolve_placement_group_strategy(enable_placement_group, count, instance_type, placement_group_strategy, instance_type_info, ec2_placement_group_check, refer_to_docs_and_quit, debug_mode, p_val):
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


def create_sns_topic_and_subscribe(sns_client, instance_serial_number, instance_owner_email):
    sns_topic_name = "Ec2_Instance_SNS_Alerts_" + str(instance_serial_number)
    sns_topic = sns_client.create_topic(Name=sns_topic_name)
    sns_topic_arn = sns_topic["TopicArn"]
    sns_client.subscribe(TopicArn=sns_topic_arn, Protocol="email", Endpoint=instance_owner_email)
    return sns_topic_name, sns_topic_arn


# Function: publish_sns_notification()
# Purpose: publish the build-completion notification to the SNS topic.


def publish_sns_notification(sns_client, sns_topic_arn, sns_message_body, sns_instance_subject):
    sns_client.publish(TopicArn=sns_topic_arn, Message=sns_message_body, Subject=sns_instance_subject)


# Function: build_windows_password_table()
# Purpose: build the printable Name/IP/Administrator-password table for
# newly-created Windows instance(s). Uses an in-memory CSV (io.StringIO)
# rather than a real temp file on disk -- prettytable.from_csv only needs
# a file-like object, so there's no reason to touch the filesystem, and no
# leftover temp file to clean up if something raises partway through.


def build_windows_password_table(instance_data_dir, ec2_keypair, fetch_windows_instance_details, decrypt_windows_admin_passwords):
    import io

    from prettytable import from_csv

    windows_instance_id, windows_instance_name, windows_ip_address = fetch_windows_instance_details(instance_data_dir)
    windows_administrator_password = decrypt_windows_admin_passwords(instance_data_dir, ec2_keypair, windows_instance_id)

    csv_buffer = io.StringIO()
    csv_buffer.write("Instance Name,IP Address,Adminstrator Password\n")
    for name, ip_address, password in zip(windows_instance_name.split(","), windows_ip_address.split(","), windows_administrator_password.split(","), strict=True):
        csv_buffer.write(name + "," + ip_address + "," + password + "\n")
    csv_buffer.seek(0)
    return from_csv(csv_buffer)
