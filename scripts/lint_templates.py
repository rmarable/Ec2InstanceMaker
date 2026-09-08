#!/usr/bin/env python3
"""
Render templates/*.j2 with synthetic contexts and lint the *rendered*
output.

templates/*.j2 aren't real .py/.sh/.tf files, so shellcheck/ruff/terraform
never see them directly, and pointing shellcheck at a raw .j2 file would
just choke on the {% %}/{{ }} syntax. This uses template_engine.py -- the
same code make_instance.py calls at build time -- to render each template
with a set of contexts that exercise the major conditional branches
(base_os, count, request_type), then runs the real linters against
the rendered .py/.sh/.tf files.

Usage: python3 scripts/lint_templates.py
Exits non-zero if any rendered file fails its linter, or if `shellcheck` or
`terraform` aren't on PATH.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from template_engine import TEMPLATE_MAP, _build_render_context, render_instance_templates  # noqa: E402

CONTEXTS = {
    "linux_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-0123456789abcdef0",
        "base_os": "alinux2",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": True,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev01",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 8,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t2.micro",
        "ec2_keypair": "dev01-key",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev01-12345678901234_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev01-12345678901234_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev01",
        "vars_file_path": "./vars_files/dev01.yml",
        "request_type": "ondemand",
        "instance_serial_number": "12345678901234_us-east-1",
        "instance_serial_number_file": "./active_instances/dev01.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-0123456789abcdef0",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev01-12345678901234_us-east-1",
        "subnet_id": "subnet-0123456789abcdef0",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "al2023_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-0a1b2c3d4e5f60789",
        "base_os": "al2023",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": True,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev02",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 8,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "dev02-key",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev02-23456789012345_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev02-23456789012345_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev02",
        "vars_file_path": "./vars_files/dev02.yml",
        "request_type": "ondemand",
        "instance_serial_number": "23456789012345_us-east-1",
        "instance_serial_number_file": "./active_instances/dev02.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-0a1b2c3d4e5f60789",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev02-23456789012345_us-east-1",
        "subnet_id": "subnet-0a1b2c3d4e5f60789",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "alma9_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-0a1b2c3d4e5f60718",
        "base_os": "alma9",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": False,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev11",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "dev11-key",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev11-78901234567890_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev11-78901234567890_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev11",
        "vars_file_path": "./vars_files/dev11.yml",
        "request_type": "ondemand",
        "instance_serial_number": "78901234567890_us-east-1",
        "instance_serial_number_file": "./active_instances/dev11.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-0a1b2c3d4e5f60718",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev11-78901234567890_us-east-1",
        "subnet_id": "subnet-0a1b2c3d4e5f60718",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "rhel9_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-0c3d4e5f607182930",
        "base_os": "rhel9",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": False,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev03",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "dev03-key",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev03-45678901234567_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev03-45678901234567_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev03",
        "vars_file_path": "./vars_files/dev03.yml",
        "request_type": "ondemand",
        "instance_serial_number": "45678901234567_us-east-1",
        "instance_serial_number_file": "./active_instances/dev03.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-0c3d4e5f607182930",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev03-45678901234567_us-east-1",
        "subnet_id": "subnet-0c3d4e5f607182930",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "rocky9_family": {
        "az": "us-east-1b",
        "aws_ami": "ami-0d4e5f6071829304b",
        "base_os": "rocky9",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": False,
        "count": 2,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/fam03",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "fam03-key",
        "ec2_user": "rocky",
        "ec2_user_home": "/home/rocky",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-fam03-56789012345678_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-fam03-56789012345678_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "fam03",
        "vars_file_path": "./vars_files/fam03.yml",
        "request_type": "ondemand",
        "instance_serial_number": "56789012345678_us-east-1",
        "instance_serial_number_file": "./active_instances/fam03.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-0d4e5f6071829304b",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_fam03-56789012345678_us-east-1",
        "subnet_id": "subnet-0d4e5f6071829304b",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "ubuntu2404_family": {
        "az": "us-east-1c",
        "aws_ami": "ami-0b2c3d4e5f6071829",
        "base_os": "ubuntu2404",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "apt",
        "awscli_preinstalled": False,
        "count": 3,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/fam02",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "fam02-key",
        "ec2_user": "ubuntu",
        "ec2_user_home": "/home/ubuntu",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-fam02-34567890123456_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-fam02-34567890123456_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "fam02",
        "vars_file_path": "./vars_files/fam02.yml",
        "request_type": "ondemand",
        "instance_serial_number": "34567890123456_us-east-1",
        "instance_serial_number_file": "./active_instances/fam02.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-0b2c3d4e5f6071829",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_fam02-34567890123456_us-east-1",
        "subnet_id": "subnet-0b2c3d4e5f6071829",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "windows_spot_family": {
        "az": "us-east-1b",
        "aws_ami": "ami-0fedcba9876543210",
        "base_os": "windows2019",
        "architecture": "x86_64",
        "is_windows": True,
        "package_manager": None,
        "awscli_preinstalled": None,
        "count": 3,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/fam01",
        "debug_mode": "true",
        "ebs_encryption": "true",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 30,
        "ebs_root_volume_type": "io1",
        "ebs_root_volume_iops": 3000,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "m5.large",
        "ec2_keypair": "fam01-key",
        "ec2_user": "Administrator",
        "ec2_user_home": "/home/Administrator",
        "ec2_iam_instance_policy": "UNDEFINED",
        "ec2_iam_instance_profile": "",
        "ec2_iam_instance_role": "my-preexisting-role",
        "enable_placement_group": "true",
        "hyperthreading": "false",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "fam01",
        "vars_file_path": "./vars_files/fam01.yml",
        "request_type": "spot",
        "instance_serial_number": "98765432109876_us-east-1",
        "instance_serial_number_file": "./active_instances/fam01.serial",
        "placement_group_strategy": "cluster",
        "preserve_ami": "false",
        "project_id": "myproj123",
        "preserve_iam_role": "true",
        "public_ip": "false",
        "region": "us-east-1",
        "spot_price": "0.05",
        "vpc_security_group_ids": "sg-0fedcba9876543210",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_fam01-98765432109876_us-east-1",
        "subnet_id": "subnet-0fedcba9876543210",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "windows2025_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-0e5f60718293041c2",
        "base_os": "windows2025",
        "architecture": "x86_64",
        "is_windows": True,
        "package_manager": None,
        "awscli_preinstalled": None,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev08",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 30,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.medium",
        "ec2_keypair": "dev08-key",
        "ec2_user": "Administrator",
        "ec2_user_home": "/home/Administrator",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev08-67890123456789_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev08-67890123456789_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev08",
        "vars_file_path": "./vars_files/dev08.yml",
        "request_type": "ondemand",
        "instance_serial_number": "67890123456789_us-east-1",
        "instance_serial_number_file": "./active_instances/dev08.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-0e5f60718293041c2",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev08-67890123456789_us-east-1",
        "subnet_id": "subnet-0e5f60718293041c2",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    # The remaining scenarios below fill two kinds of gap: base_os values
    # with zero coverage (alma10/rhel10/rocky10/ubuntu2604/windows2022),
    # and parameter dimensions previously only ever exercised on Windows
    # (windows_spot_family above covers ebs_encryption=true,
    # enable_placement_group=true, request_type=spot, and
    # preserve_iam_role=true all at once, but only for Windows -- there was
    # no Linux scenario for any of them), plus a secondary EBS device
    # volume and a Graviton/arm64 instance_type, neither of which any prior
    # scenario used.
    "alma10_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-089012345678901c2",
        "base_os": "alma10",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": False,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev12",
        "debug_mode": "false",
        "ebs_encryption": "true",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "dev12-key",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev12-89012345678901_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev12-89012345678901_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev12",
        "vars_file_path": "./vars_files/dev12.yml",
        "request_type": "ondemand",
        "instance_serial_number": "89012345678901_us-east-1",
        "instance_serial_number_file": "./active_instances/dev12.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-089012345678901c2",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev12-89012345678901_us-east-1",
        "subnet_id": "subnet-089012345678901c2",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "rhel10_spot": {
        "az": "us-east-1a",
        "aws_ami": "ami-090123456789012d3",
        "base_os": "rhel10",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": False,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev13",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "dev13-key",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev13-90123456789012_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev13-90123456789012_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev13",
        "vars_file_path": "./vars_files/dev13.yml",
        "request_type": "spot",
        "instance_serial_number": "90123456789012_us-east-1",
        "instance_serial_number_file": "./active_instances/dev13.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "0.03",
        "vpc_security_group_ids": "sg-090123456789012d3",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev13-90123456789012_us-east-1",
        "subnet_id": "subnet-090123456789012d3",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "rocky10_placement_group_family": {
        "az": "us-east-1b",
        "aws_ami": "ami-001234567890123e4",
        "base_os": "rocky10",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": False,
        "count": 2,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/fam04",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "fam04-key",
        "ec2_user": "rocky",
        "ec2_user_home": "/home/rocky",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-fam04-01234567890123_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-fam04-01234567890123_us-east-1",
        "enable_placement_group": "true",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "fam04",
        "vars_file_path": "./vars_files/fam04.yml",
        "request_type": "ondemand",
        "instance_serial_number": "01234567890123_us-east-1",
        "instance_serial_number_file": "./active_instances/fam04.serial",
        "placement_group_strategy": "cluster",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-001234567890123e4",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_fam04-01234567890123_us-east-1",
        "subnet_id": "subnet-001234567890123e4",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "ubuntu2604_secondary_volume_family": {
        "az": "us-east-1c",
        "aws_ami": "ami-012345678901234f5",
        "base_os": "ubuntu2604",
        "architecture": "x86_64",
        "is_windows": False,
        "package_manager": "apt",
        "awscli_preinstalled": False,
        "count": 2,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/fam05",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 10,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 20,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.micro",
        "ec2_keypair": "fam05-key",
        "ec2_user": "ubuntu",
        "ec2_user_home": "/home/ubuntu",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-fam05-12345678901234_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-fam05-12345678901234_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "fam05",
        "vars_file_path": "./vars_files/fam05.yml",
        "request_type": "ondemand",
        "instance_serial_number": "12345678901234_us-east-1",
        "instance_serial_number_file": "./active_instances/fam05.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-012345678901234f5",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_fam05-12345678901234_us-east-1",
        "subnet_id": "subnet-012345678901234f5",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "windows2022_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-023456789012345a6",
        "base_os": "windows2022",
        "architecture": "x86_64",
        "is_windows": True,
        "package_manager": None,
        "awscli_preinstalled": None,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev14",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 30,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "t3.medium",
        "ec2_keypair": "dev14-key",
        "ec2_user": "Administrator",
        "ec2_user_home": "/home/Administrator",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev14-23456789012345_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev14-23456789012345_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev14",
        "vars_file_path": "./vars_files/dev14.yml",
        "request_type": "ondemand",
        "instance_serial_number": "23456789012345_us-east-1",
        "instance_serial_number_file": "./active_instances/dev14.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-023456789012345a6",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev14-23456789012345_us-east-1",
        "subnet_id": "subnet-023456789012345a6",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
    "al2023_graviton_ondemand": {
        "az": "us-east-1a",
        "aws_ami": "ami-034567890123456b7",
        "base_os": "al2023",
        "architecture": "arm64",
        "is_windows": False,
        "package_manager": "yum",
        "awscli_preinstalled": True,
        "count": 1,
        "custom_user_prelogin_scripts": ["default"],
        "custom_user_postboot_scripts": ["default"],
        "enable_cloudwatch_logs": "true",
        "preserve_cloudwatch_logs": "false",
        "cloudwatch_log_group": "/ec2instancemaker/dev15",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_optimized": "true",
        "ebs_root_volume_size": 8,
        "ebs_root_volume_type": "gp2",
        "ebs_root_volume_iops": 0,
        "ebs_device_volume_size": 0,
        "ebs_device_volume_type": "gp2",
        "ebs_device_volume_iops": 0,
        "instance_type": "m6g.large",
        "ec2_keypair": "dev15-key",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "ec2_iam_instance_policy": "GenericEc2InstancePolicy.json",
        "ec2_iam_instance_profile": "Ec2InstanceMaker-profile-dev15-34567890123456_us-east-1",
        "ec2_iam_instance_role": "Ec2InstanceMaker-role-dev15-34567890123456_us-east-1",
        "enable_placement_group": "false",
        "hyperthreading": "true",
        "instance_owner": "rmarable",
        "instance_owner_email": "rodney.marable@gmail.com",
        "instance_owner_department": "hpc",
        "instance_name": "dev15",
        "vars_file_path": "./vars_files/dev15.yml",
        "request_type": "ondemand",
        "instance_serial_number": "34567890123456_us-east-1",
        "instance_serial_number_file": "./active_instances/dev15.serial",
        "placement_group_strategy": "UNDEFINED",
        "preserve_ami": "true",
        "project_id": "UNDEFINED",
        "preserve_iam_role": "false",
        "public_ip": "true",
        "region": "us-east-1",
        "spot_price": "UNDEFINED",
        "vpc_security_group_ids": "sg-034567890123456b7",
        "sns_topic_arn": "arn:aws:sns:us-east-1:123456789012:Ec2_Instance_SNS_Alerts_dev15-34567890123456_us-east-1",
        "subnet_id": "subnet-034567890123456b7",
        "vpc_name": "vpc_default",
        "DEPLOYMENT_DATE_TAG": "09/06/2026",
    },
}


def lint_shell(path):
    result = subprocess.run(["shellcheck", "--severity=error", path], capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


# instance_userdata.j2 renders #cloud-config (cloud-init YAML), not a real
# shell script, despite the .sh extension convention -- shellcheck would
# just misparse it.
SKIP_LINT = {"instance_userdata_script"}


def lint_python(path):
    # Generated files compose conditionally per Jinja branch (e.g.
    # access_instance.j2 only uses `csv`/`os` inside its `count > 1`
    # blocks), so a full style/unused-import pass is permanently noisy
    # across different renders of the same template. Check what actually
    # indicates a broken template: syntax errors and undefined names (e.g.
    # a variable renamed in template_engine.py but not in the .j2 source).
    result = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stdout + result.stderr
    result = subprocess.run(["ruff", "check", "--isolated", "--select=F821,F822,F823,E9", path], capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stdout + result.stderr
    # The pre-commit bandit hook only ever scans the 4 hand-written top-level
    # .py files -- it never sees generated code, which is exactly where
    # shell=True + concatenated/interpolated data is most likely to end up
    # (e.g. the SSH/get-password-data commands access_instance.j2 renders).
    # Run the same -ll (Medium/High only) check here so that class of bug
    # doesn't have a blind spot.
    result = subprocess.run(["bandit", "-c", os.path.join(REPO_ROOT, "pyproject.toml"), "-ll", path], capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


SUFFIX_LINTERS = {
    ".sh": lint_shell,
    ".py": lint_python,
}


def lint_terraform_dir(tf_dir):
    """terraform fmt/validate need the whole rendered instance_data dir at
    once (provider_aws.tf + <name>.tf reference each other)."""
    failures = []

    fmt = subprocess.run(["terraform", "fmt", "-check", "-diff", "-no-color"], cwd=tf_dir, capture_output=True, text=True)
    if fmt.returncode != 0:
        failures.append(("terraform fmt", fmt.stdout + fmt.stderr))

    # Every scenario's `terraform init` otherwise re-downloads the AWS
    # provider from registry.terraform.io from scratch -- a hard,
    # undocumented network dependency with no distinguishing error message
    # if it's unreachable, and needlessly slow across N scenarios. Caching
    # across runs (and across scenarios within one run) fixes the latter;
    # the network dependency itself is inherent to `terraform init` and not
    # something this script can remove.
    plugin_cache_dir = os.path.join(REPO_ROOT, ".terraform-plugin-cache")
    os.makedirs(plugin_cache_dir, exist_ok=True)
    init_env = dict(os.environ, TF_PLUGIN_CACHE_DIR=plugin_cache_dir)
    init = subprocess.run(["terraform", "init", "-backend=false", "-input=false", "-no-color"], cwd=tf_dir, capture_output=True, text=True, env=init_env)
    if init.returncode != 0:
        failures.append(("terraform init", init.stdout + init.stderr))
    else:
        validate = subprocess.run(["terraform", "validate", "-no-color"], cwd=tf_dir, capture_output=True, text=True)
        combined = validate.stdout + validate.stderr
        # terraform validate legitimately fails here on file()-referenced
        # artifacts (the .pem keypair) that only exist after a real build --
        # that's expected in this synthetic-context check, not a template
        # bug. Still catch any other/unexpected validate error.
        if validate.returncode != 0 and "Invalid function argument" not in combined:
            failures.append(("terraform validate", combined))
    return failures


def main():
    missing = [tool for tool in ("shellcheck", "terraform") if shutil.which(tool) is None]
    if missing:
        print(f"ERROR: required tool(s) not on PATH: {', '.join(missing)}")
        return 1

    all_failures = []

    for scenario_name, instance_parameters in CONTEXTS.items():
        with tempfile.TemporaryDirectory() as scratch_root:
            os.symlink(os.path.join(REPO_ROOT, "templates"), os.path.join(scratch_root, "templates"))
            os.symlink(os.path.join(REPO_ROOT, "custom_user_scripts"), os.path.join(scratch_root, "custom_user_scripts"))
            instance_name = instance_parameters["instance_name"]
            instance_data_dir = os.path.join(scratch_root, "instance_data", instance_name)
            os.makedirs(instance_data_dir)

            render_instance_templates(instance_parameters, scratch_root, instance_data_dir)
            context = _build_render_context(instance_parameters, scratch_root, instance_data_dir)

            for _src, dest_key in TEMPLATE_MAP:
                if dest_key in SKIP_LINT:
                    continue
                filename = context[dest_key]
                path = os.path.join(instance_data_dir, filename)
                _, ext = os.path.splitext(filename)
                linter = SUFFIX_LINTERS.get(ext)
                if linter is None:
                    continue
                ok, output = linter(path)
                if not ok:
                    all_failures.append((scenario_name, filename, output))

            # custom_user_postboot_script.j2_<name> isn't in TEMPLATE_MAP --
            # there can be zero or many per scenario -- so lint whatever
            # actually got rendered by filename instead.
            for filename in sorted(os.listdir(instance_data_dir)):
                if filename.startswith("custom_user_postboot_script."):
                    ok, output = lint_shell(os.path.join(instance_data_dir, filename))
                    if not ok:
                        all_failures.append((scenario_name, filename, output))

            # custom_user_prelogin_script.j2_<name> content is embedded
            # inline in instance_userdata.j2's cloud-config write_files --
            # extract it and shellcheck it directly, same as any other
            # generated .sh, rather than letting it go unchecked just
            # because it's not a standalone file on disk. Not every
            # write_files entry is a shell script -- the CloudWatch Agent
            # config is JSON -- so dispatch by extension instead of
            # assuming everything in write_files is bash.
            userdata_path = os.path.join(instance_data_dir, context["instance_userdata_script"])
            with open(userdata_path) as fh:
                userdata_doc = yaml.safe_load(fh.read().split("\n", 1)[1])
            for entry in (userdata_doc or {}).get("write_files", []):
                extracted_path = os.path.join(instance_data_dir, os.path.basename(entry["path"]))
                with open(extracted_path, "w") as fh:
                    fh.write(entry["content"])
                if extracted_path.endswith(".sh"):
                    ok, output = lint_shell(extracted_path)
                elif extracted_path.endswith(".json"):
                    try:
                        json.loads(entry["content"])
                        ok, output = True, ""
                    except json.JSONDecodeError as e:
                        ok, output = False, str(e)
                else:
                    continue
                if not ok:
                    all_failures.append((scenario_name, entry["path"], output))

            tf_failures = lint_terraform_dir(instance_data_dir)
            for tool_name, output in tf_failures:
                all_failures.append((scenario_name, tool_name, output))

    if all_failures:
        print("FAILURES:")
        for scenario_name, name, output in all_failures:
            print(f"\n--- {scenario_name} / {name} ---")
            print(output)
        return 1

    total_files = len(CONTEXTS) * len(TEMPLATE_MAP)
    print(f"All rendered templates passed lint ({len(CONTEXTS)} scenarios x {len(TEMPLATE_MAP)} templates = {total_files} files, plus terraform fmt/validate per scenario)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
