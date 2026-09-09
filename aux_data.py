################################################################################
# Name:		aux_data.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	June 3, 2019
# Last Changed:	July 29, 2019
# Purpose:	Data structures and functions to support Ec2InstanceMaker
################################################################################

from collections.abc import Callable
from typing import Any, Literal, NoReturn, cast

from mypy_boto3_ec2.client import EC2Client
from mypy_boto3_ec2.literals import InstanceTypeType
from mypy_boto3_ec2.service_resource import SecurityGroup
from mypy_boto3_iam.client import IAMClient
from mypy_boto3_logs.client import CloudWatchLogsClient
from mypy_boto3_sns.client import SNSClient

# Type aliases used throughout this module's signatures -- duplicated
# (rather than imported) from instance_builder.py's identical aliases,
# since instance_builder.py must not import aux_data.py (see resolve_ami()
# there) and the reverse import would be a new, needless coupling for one
# line of typing:
# - BoolStr: this codebase's pervasive Ansible-style string-boolean
#   convention ("true"/"false" as literal strings, not real bool values).
# boto3/botocore clients and resources are typed via boto3-stubs
# (mypy_boto3_*, a dev-only dependency) rather than a fabricated Any.
BoolStr = Literal["true", "false"]

# Global variable definitions

null_list = [""]

# base_os family classification: the single source of truth for "is this
# Windows," "which package manager does it use," "what's the default SSH/
# RDP user," and "does it ship awscli preinstalled." Before this table
# existed, each of these questions was answered independently -- via
# ad-hoc substring matching on base_os -- in make_instance.py (multiple
# `"windows" in base_os` checks, a 6-branch ec2_user if-chain) AND in
# several Jinja templates (`{% if 'windows' in base_os %}`,
# `{% if 'ubuntu' not in base_os %}`, an OR-chain of family substrings in
# custom_user_script.j2_R), with no shared source of truth keeping them in
# sync. get_base_os_family() below is the one place a new base_os value
# needs to be taught these facts; make_instance.py computes them once and
# threads the results into instance_parameters so templates read them
# too, instead of re-deriving the same substring logic a third time.
BASE_OS_FAMILIES: dict[str, dict[str, Any]] = {
    "al2023": {"is_windows": False, "package_manager": "yum", "ec2_user": "ec2-user", "awscli_preinstalled": True},
    "alinux2": {"is_windows": False, "package_manager": "yum", "ec2_user": "ec2-user", "awscli_preinstalled": True},
    "alma9": {"is_windows": False, "package_manager": "yum", "ec2_user": "ec2-user", "awscli_preinstalled": False},
    "alma10": {"is_windows": False, "package_manager": "yum", "ec2_user": "ec2-user", "awscli_preinstalled": False},
    "rhel9": {"is_windows": False, "package_manager": "yum", "ec2_user": "ec2-user", "awscli_preinstalled": False},
    "rhel10": {"is_windows": False, "package_manager": "yum", "ec2_user": "ec2-user", "awscli_preinstalled": False},
    "rocky9": {"is_windows": False, "package_manager": "yum", "ec2_user": "rocky", "awscli_preinstalled": False},
    "rocky10": {"is_windows": False, "package_manager": "yum", "ec2_user": "rocky", "awscli_preinstalled": False},
    "ubuntu2404": {"is_windows": False, "package_manager": "apt", "ec2_user": "ubuntu", "awscli_preinstalled": False},
    "ubuntu2604": {"is_windows": False, "package_manager": "apt", "ec2_user": "ubuntu", "awscli_preinstalled": False},
    "windows2019": {"is_windows": True, "package_manager": None, "ec2_user": "Administrator", "awscli_preinstalled": None},
    "windows2022": {"is_windows": True, "package_manager": None, "ec2_user": "Administrator", "awscli_preinstalled": None},
    "windows2025": {"is_windows": True, "package_manager": None, "ec2_user": "Administrator", "awscli_preinstalled": None},
}


# Function: get_base_os_family()
# Purpose: look up the family classification for a base_os value


def get_base_os_family(base_os: str) -> dict[str, Any]:
    if base_os not in BASE_OS_FAMILIES:
        error_msg = '"' + base_os + '" is not a recognized base_os!'
        refer_to_docs_and_quit(error_msg)
    return BASE_OS_FAMILIES[base_os]


# Function: add_security_group_rule()
# Purpose: add a rule to a security group


def add_inbound_security_group_rule(sec_grp: SecurityGroup, protocol: str, cidr: str, psource: int, pdest: int) -> None:
    sec_grp.authorize_ingress(IpProtocol=protocol, CidrIp=cidr, FromPort=psource, ToPort=pdest)


# Function: base_os_instance_check()
# Purpose: verify the selected EC2 instance_type is supported by base_os


def base_os_instance_check(base_os: str, instance_type: str, architecture: str, debug_mode: BoolStr) -> None:
    unsupported_instance_prefixes = {
        "al2023": ec2_instances_unsupported_al2023,
        "alinux2": ec2_instances_unsupported_alinux2,
        "alma9": ec2_instances_unsupported_alma9,
        "alma10": ec2_instances_unsupported_alma10,
        "rhel9": ec2_instances_unsupported_rhel9,
        "rhel10": ec2_instances_unsupported_rhel10,
        "rocky9": ec2_instances_unsupported_rocky9,
        "rocky10": ec2_instances_unsupported_rocky10,
        "ubuntu2404": ec2_instances_unsupported_ubuntu2404,
        "ubuntu2604": ec2_instances_unsupported_ubuntu2604,
        "windows2019": ec2_instances_unsupported_windows2019,
        "windows2022": ec2_instances_unsupported_windows2022,
        "windows2025": ec2_instances_unsupported_windows2025,
    }
    if base_os not in unsupported_instance_prefixes:
        error_msg = '"' + base_os + '" is not a recognized base_os!'
        refer_to_docs_and_quit(error_msg)
    if any(prefix and prefix in instance_type for prefix in unsupported_instance_prefixes[base_os]):
        error_msg = base_os + " does not support EC2 instance type " + instance_type + "!"
        refer_to_docs_and_quit(error_msg)
    if "windows" in base_os and architecture == "arm64":
        error_msg = base_os + " does not support AWS Graviton (ARM64) instance types!"
        refer_to_docs_and_quit(error_msg)
    p_val("base_os", debug_mode)


# Function: get_instance_type_info()
# Purpose: validate instance_type against the live AWS API and return its
# supported CPU architecture, EBS optimization/encryption support, and
# placement group strategies -- ground truth from describe_instance_types
# rather than a hand-maintained allowlist.


def get_instance_type_info(ec2client: EC2Client, instance_type: str) -> dict[str, Any] | None:
    from botocore.exceptions import ClientError

    try:
        # boto3-stubs types InstanceTypes as a Literal of every instance
        # type AWS had published as of this stub release. instance_type
        # stays a plain str (see the comment above this function) so a new
        # AWS instance family works without waiting on a stub update --
        # this cast documents "intentionally dynamic input," not "this
        # value is definitely valid"; validity is exactly what this API
        # call itself determines.
        response = ec2client.describe_instance_types(InstanceTypes=[cast(InstanceTypeType, instance_type)])
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidInstanceType":
            return None
        # Anything else (throttling, AccessDenied, bad credentials, etc.) is
        # a real AWS/API problem, not an invalid instance_type -- surface it
        # clearly instead of misreporting it as one.
        error_msg = "AWS API error while validating instance_type " + instance_type + ": " + str(e)
        refer_to_docs_and_quit(error_msg)
    instance_types = response.get("InstanceTypes")
    if not instance_types:
        return None
    details = instance_types[0]
    supported_architectures = details["ProcessorInfo"]["SupportedArchitectures"]
    if "arm64" in supported_architectures:
        architecture = "arm64"
    elif "x86_64" in supported_architectures:
        architecture = "x86_64"
    else:
        # e.g. i386-only legacy types; nothing in this toolkit supports them.
        return None
    return {
        "architecture": architecture,
        "ebs_optimized_support": details["EbsInfo"]["EbsOptimizedSupport"],
        "ebs_encryption_support": details["EbsInfo"]["EncryptionSupport"],
        "placement_group_strategies": details["PlacementGroupInfo"]["SupportedStrategies"],
    }


# Function: check_custom_ami()
# Purpose: verify the existence of a user-provided custom AMI


def check_custom_ami(ec2client: EC2Client, custom_ami: str, aws_account_id: str, architecture: str) -> str:
    from botocore.exceptions import ClientError

    try:
        ami_information = ec2client.describe_images(
            Owners=[aws_account_id],
            Filters=[
                {"Name": "architecture", "Values": [architecture]},
                {"Name": "image-id", "Values": [custom_ami]},
                {"Name": "state", "Values": ["available"]},
                {"Name": "virtualization-type", "Values": ["hvm"]},
            ],
        )
    except ClientError as e:
        # Same reasoning as get_instance_type_info() above: a real AWS API
        # problem (throttling, AccessDenied) here used to propagate as a
        # raw, unhandled botocore exception instead of a clean operator-
        # facing message.
        refer_to_docs_and_quit("AWS API error while checking custom_ami " + custom_ami + ": " + str(e))
    amis = sorted(ami_information["Images"], key=lambda x: x["CreationDate"], reverse=True)
    try:
        aws_ami = amis[0]["ImageId"]
    except IndexError:
        return "false"
    else:
        return aws_ami


# Function: ctrlC_Abort()
# Purpose: Print an abort header, capture CTRL-C when pressed, and remove all
# of entities created by make_instance.py prior to capturing KeyboardInterrupt:
# orphaned state directories and files; EC2 security groups and keypairs; IAM
# roles, policies, and instance profiles


def ctrlC_Abort(
    sleep_time: int,
    line_length: int,
    vars_file_path: str,
    instance_data_dir: str,
    instance_serial_number_file: str,
    ec2client: EC2Client,
    iam: IAMClient,
    security_group_name: str,
    vpc_security_group_ids: str,
    iam_instance_role: str,
    iam_instance_policy: str,
    iam_instance_profile: str,
    preserve_iam_role: BoolStr,
    ec2_keypair: str,
    sns_client: SNSClient,
    sns_topic_arn: str,
    logs_client: CloudWatchLogsClient,
    cloudwatch_log_group: str,
    enable_cloudwatch_logs: BoolStr,
    preserve_cloudwatch_logs: BoolStr,
    preserve_security_group: BoolStr,
    instance_name: str,
) -> None:
    import os
    import sys
    import time

    from botocore.exceptions import ClientError

    secret_key_file = instance_data_dir + ec2_keypair + ".pem"
    print("")
    print("".center(line_length, "#"))
    center_line = "    Please type CTRL-C within " + str(sleep_time) + " seconds to abort    "
    print(center_line.center(line_length, "#"))
    print("".center(line_length, "#"))
    print("")
    try:
        time.sleep(sleep_time)
    except KeyboardInterrupt:
        # Every deletion below is attempted even if an earlier one fails,
        # and every failure is *reported*. This previously caught
        # ClientError and printed something only when the error code was
        # the "already gone" one -- any other code (AccessDenied,
        # DeleteConflict, Throttling, DependencyViolation) fell off the
        # end of the handler silently, so the abort printed "Aborting..."
        # and exited 1 while the security group, keypair, role, policy and
        # instance profile were all still there. An abort that half-works
        # and says nothing is worse than one that fails loudly.
        cleanup_failures: list[str] = []

        def attempt(action: Callable[[], object], description: str, already_gone_codes: set[str]) -> None:
            try:
                action()
                print("Deleted: " + description)
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code in already_gone_codes:
                    print("Nothing to delete: " + description)
                else:
                    print("*** FAILED to delete " + description + " -- " + error_code)
                    cleanup_failures.append(description)

        for state_file in (instance_serial_number_file, vars_file_path):
            if os.path.exists(state_file):
                os.remove(state_file)
                print("Removed: " + state_file)
        print("")
        if preserve_iam_role == "true":
            print("Preserved EC2 IAM instance policy: " + iam_instance_policy)
            print("Preserved EC2 IAM instance profile: " + iam_instance_profile)
            print("Preserved EC2 IAM instance role: " + iam_instance_role)
        else:
            # Ordering matters: IAM refuses to delete a role that still has
            # an inline policy or is still referenced by an instance profile.
            iam_cleanup_steps: list[tuple[Callable[[], object], str]] = [
                (
                    lambda: iam.remove_role_from_instance_profile(InstanceProfileName=iam_instance_profile, RoleName=iam_instance_role),
                    "IAM role " + iam_instance_role + " from instance profile " + iam_instance_profile,
                ),
                (lambda: iam.delete_instance_profile(InstanceProfileName=iam_instance_profile), "IAM EC2 instance profile " + iam_instance_profile),
                (lambda: iam.delete_role_policy(RoleName=iam_instance_role, PolicyName=iam_instance_policy), "IAM EC2 policy " + iam_instance_policy),
                (lambda: iam.delete_role(RoleName=iam_instance_role), "IAM EC2 role " + iam_instance_role),
            ]
            for action, description in iam_cleanup_steps:
                attempt(action, description, {"NoSuchEntity"})
        print("")
        if preserve_security_group == "true":
            print("Preserved pre-existing EC2 security group: " + security_group_name)
        else:
            attempt(
                lambda: ec2client.delete_security_group(GroupId=vpc_security_group_ids),
                "EC2 security group " + security_group_name,
                {"InvalidGroup.NotFound"},
            )
        attempt(lambda: ec2client.delete_key_pair(KeyName=ec2_keypair), "EC2 keypair " + ec2_keypair, {"InvalidKeyPair.NotFound"})
        if os.path.exists(secret_key_file):
            os.remove(secret_key_file)
            print("Removed: " + secret_key_file)
        # The SNS topic and the CloudWatch Logs group are both created in
        # the phase immediately before this abort window, and neither was
        # cleaned up here at all -- so every aborted build left an SNS
        # topic (plus a pending email subscription confirmation) and a
        # retention-configured log group behind.
        if sns_topic_arn:
            attempt(lambda: sns_client.delete_topic(TopicArn=sns_topic_arn), "SNS topic " + sns_topic_arn, {"NotFound", "ResourceNotFoundException"})
        if enable_cloudwatch_logs == "true":
            if preserve_cloudwatch_logs == "false":
                attempt(
                    lambda: logs_client.delete_log_group(logGroupName=cloudwatch_log_group),
                    "CloudWatch Logs group " + cloudwatch_log_group,
                    {"ResourceNotFoundException"},
                )
            else:
                print("Preserved CloudWatch Logs group: " + cloudwatch_log_group)
        if cleanup_failures:
            print("")
            print("*** WARNING ***")
            print(str(len(cleanup_failures)) + " resource(s) could NOT be deleted:")
            for description in cleanup_failures:
                print("\t" + description)
            print("")
            print("These may still exist in AWS and may still be incurring charges.")
            print("Run ./kill-instance." + instance_name + ".sh to retry, or delete them by hand.")
        print("")
        print("Aborting...")
        sys.exit(1)


# Function: log_retention_days_check()
# Purpose: verify --log_retention_days is a value CloudWatch Logs'
# PutRetentionPolicy actually accepts -- it rejects anything outside this
# fixed set with an API error, so validate client-side for a clearer
# operator-facing message.

CLOUDWATCH_LOGS_RETENTION_DAYS = (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653)


def log_retention_days_check(log_retention_days: int, debug_mode: BoolStr) -> None:
    if log_retention_days not in CLOUDWATCH_LOGS_RETENTION_DAYS:
        p_fail(str(log_retention_days), "log_retention_days", [str(d) for d in CLOUDWATCH_LOGS_RETENTION_DAYS])
    p_val("log_retention_days", debug_mode)


# Function: ebs_encryption_check()
# Purpose: verify the instance_type supports EBS encryption


def ebs_encryption_check(instance_type: str, ebs_encryption_support: str, instance_name: str, debug_mode: BoolStr) -> None:
    if ebs_encryption_support == "supported":
        print("")
        print("Enabling: EBS encryption")
        print("build-ami." + instance_name + ".sh will create encrypted AMIs!")
    else:
        error_msg = instance_type + " does not support EBS encryption!"
        refer_to_docs_and_quit(error_msg)


# Function: ec2_placement_group_check()
# Purpose: verify the instance_type supports the requested EC2 placement
# group strategy


def ec2_placement_group_check(instance_type: str, placement_group_strategy: str, placement_group_strategies: list[str], debug_mode: BoolStr) -> None:
    if placement_group_strategy not in placement_group_strategies:
        error_msg = instance_type + ' does not support the "' + placement_group_strategy + '" EC2 Placement Group strategy!'
        refer_to_docs_and_quit(error_msg)


# Function: get_ami_info()
# Purpose: get the ID of an AWS AMI image


# AMI catalog: base_os -> (publisher's AWS account ID, describe_images Name
# filter pattern). One shared lookup in get_ami_info() below does the actual
# API call; this table is the only thing that needs a new entry when a new
# base_os is added.
#
# Marketplace-gating status (verified via live describe-images -- absence
# vs. presence of a ProductCodes entry): Amazon Linux/AL2023, AlmaLinux, and
# RHEL are NOT gated (no AWS Marketplace subscription needed, AMIs launch
# immediately). Rocky Linux IS gated -- the operator must subscribe to
# "Rocky Linux 9/10 (Official)" first or launches fail with OptInRequired;
# see the troubleshooting note in README.md. Ubuntu is not gated, but its
# Owners= pin (Canonical's official account) is required -- without it,
# describe_images searches every AWS account for a Name match, which can
# silently resolve to an unrelated (and possibly Marketplace-gated) AMI
# from a different publisher using a similar naming convention. Windows
# Server has no public arm64 AMI; base_os_instance_check() rejects any
# windows* + arm64 instance_type combo before get_ami_info() is ever
# called, so architecture is guaranteed to be "x86_64" for those three.
#
# Name filter notes: al2023's filter is deliberately un-suffixed (no
# "-x86_64"/"-arm64" token) since the "architecture" API filter alone does
# the narrowing. RHEL's filter is architecture-agnostic already. Rocky's
# Name field uses "aarch64" (not "arm64") on the ARM side, so that token is
# dropped entirely rather than hardcoded per architecture. Ubuntu encodes
# architecture as "amd64"/"arm64" in the path itself (not "x86_64"), so
# that segment is wildcarded; it also publishes under hvm-ssd-gp3 (not the
# older hvm-ssd path used by pre-23.10 releases).
_AMI_CATALOG: dict[str, tuple[str, str]] = {
    "alinux2": ("137112412989", "amzn2-ami-hvm-2.0.*"),  # Amazon
    "al2023": ("137112412989", "al2023-ami-2023.*"),  # Amazon
    "alma9": ("764336703387", "AlmaLinux OS 9*"),  # AlmaLinux OS Foundation
    "alma10": ("764336703387", "AlmaLinux OS 10*"),  # AlmaLinux OS Foundation
    "rhel9": ("309956199498", "RHEL-9*"),  # Red Hat
    "rhel10": ("309956199498", "RHEL-10*"),  # Red Hat
    "rocky9": ("679593333241", "Rocky-9-EC2-Base-9.*"),  # Rocky Linux (CIQ)
    "rocky10": ("679593333241", "Rocky-10-EC2-Base-10.*"),  # Rocky Linux (CIQ)
    "ubuntu2404": ("099720109477", "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-*-server-*"),  # Canonical
    "ubuntu2604": ("099720109477", "ubuntu/images/hvm-ssd-gp3/ubuntu-resolute-26.04-*-server-*"),  # Canonical
    "windows2019": ("801119661308", "Windows_Server-2019-English-Full-Base-*"),
    "windows2022": ("801119661308", "Windows_Server-2022-English-Full-Base-*"),
    "windows2025": ("801119661308", "Windows_Server-2025-English-Full-Base-*"),
}


def get_ami_info(ec2client: EC2Client, base_os: str, architecture: str) -> str:
    if base_os not in _AMI_CATALOG:
        error_msg = '"' + base_os + '" is not a recognized base_os!'
        refer_to_docs_and_quit(error_msg)
    owner, name_pattern = _AMI_CATALOG[base_os]
    from botocore.exceptions import ClientError

    try:
        ami_information = ec2client.describe_images(
            Owners=[owner],
            Filters=[
                {"Name": "name", "Values": [name_pattern]},
                {"Name": "architecture", "Values": [architecture]},
                {"Name": "root-device-type", "Values": ["ebs"]},
                {"Name": "virtualization-type", "Values": ["hvm"]},
            ],
        )
    except ClientError as e:
        # Same reasoning as get_instance_type_info()/check_custom_ami()
        # above: a real AWS API problem here used to propagate as a raw,
        # unhandled botocore exception instead of a clean operator-facing
        # message.
        refer_to_docs_and_quit("AWS API error while looking up an AMI for base_os " + base_os + ": " + str(e))
    amis = sorted(ami_information["Images"], key=lambda x: x["CreationDate"], reverse=True)
    return amis[0]["ImageId"]


# Function: illegal_az_msg()
# Purpose: abort when an invalid AvailabilityZone is provided


def illegal_az_msg(az: str) -> NoReturn:
    import sys

    print("*** ERROR ***")
    print('"' + az + '"' + " is not a valid Availability Zone in the selected AWS Region.")
    print("Aborting...")
    sys.exit(1)


# Function: modify_iam_policy_document()
# Purpose: modify the generic JSON policy document to limit the scope of IAM
# modification rights to roles, instance profiles, and policies prepended with
# iam_name_prefix


def modify_iam_policy_document(instance_json_policy_src: str, instance_json_policy_stage: str, iam_name_prefix: str, instance_serial_number: str) -> None:
    with open(instance_json_policy_src) as ec2_iam_role_src:
        role_stage_0 = ec2_iam_role_src.read()
        ec2_iam_role_src.close()
        role_stage_1 = role_stage_0.replace("<EC2_POLICY>", iam_name_prefix + "-policy-" + instance_serial_number)
        role_stage_2 = role_stage_1.replace("<EC2_ROLE>", iam_name_prefix + "-role-" + instance_serial_number)
        role_stage_3 = role_stage_2.replace("<EC2_INSTANCE_PROFILE>", iam_name_prefix + "-profile-" + instance_serial_number)
        # ExtendedEc2InstancePolicy.json's IAMRestricted statement uses this
        # placeholder (instead of the single-instance <EC2_ROLE>-style ones
        # above) to scope its elevated IAM permissions (needed so a spawned
        # instance can create IAM entities for *child* instances) to only
        # this operator's iam_name_prefix namespace, not every IAM entity in
        # the AWS account.
        role_stage_4 = role_stage_3.replace("<EC2_IAM_PREFIX>", iam_name_prefix)
        filedata = role_stage_4
    with open(instance_json_policy_stage, "w") as ec2_iam_role_dest:
        ec2_iam_role_dest.write(filedata)
        ec2_iam_role_dest.close()


# Function: p_fail()
# Purpose: print a failed instance_parameter validation message to stdout


def p_fail(p: str, q: str, r: str | list[str]) -> NoReturn:
    import sys
    import textwrap

    print("")
    print("*** Error ***")
    if r == "missing_element":
        print('"' + p + '"' + " seems to be missing as a valid " + q + ".")
    else:
        print('"' + p + '"' + " is not a valid option for " + q + ".")
        print("Supported values:")
        r = "\t".join(r)
        print("\n".join(textwrap.wrap(r, 78)))
    print("")
    print("Aborting...")
    sys.exit(1)


# Function: p_val()
# Purpose: print a successful instance_parameter validation message to stdout
# debug_mode is typed plain str, not BoolStr, since this specific check
# (unlike every other debug_mode comparison in the codebase) also accepts
# a capitalized "True" -- narrowing it to BoolStr would make that branch
# a type error without actually being asked to fix the inconsistency.


def p_val(p: str, debug_mode: str) -> None:
    if debug_mode == "True" or debug_mode == "true":
        print(p + " successfully validated")
    else:
        pass


# Function print_TextHeader()
# Purpose: print a centered text header to support validation and reviewing
# of instance_parameters.


def print_TextHeader(p: str, action: str, line_length: int) -> None:
    print("")
    print("".center(line_length, "-"))
    T2C = action + " parameter values for " + p
    print(T2C.center(line_length))
    print("".center(line_length, "-"))


# Function: refer_to_docs_and_quit()
# Purpose: print an error message, refer to the Ec2InstanceMaker public
# documentation, and quit with a non-successful error code.


def refer_to_docs_and_quit(error_msg: str) -> NoReturn:
    import sys

    print("*** ERROR ***")
    print(error_msg)
    print("")
    print("Please resolve this error and retry the instance build.")
    print("Aborting...")
    sys.exit(1)


# Unsupported instance types by operating system

ec2_instances_unsupported_al2023 = null_list
ec2_instances_unsupported_alinux2 = null_list
ec2_instances_unsupported_alma9 = null_list
ec2_instances_unsupported_alma10 = null_list
ec2_instances_unsupported_rhel9 = null_list
ec2_instances_unsupported_rhel10 = null_list
ec2_instances_unsupported_rocky9 = null_list
ec2_instances_unsupported_rocky10 = null_list
ec2_instances_unsupported_ubuntu2404 = null_list
ec2_instances_unsupported_ubuntu2604 = null_list
ec2_instances_unsupported_windows2019 = ["a1.", "f1."]
ec2_instances_unsupported_windows2022 = ["a1.", "f1."]
ec2_instances_unsupported_windows2025 = ["a1.", "f1."]
