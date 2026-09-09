#!/usr/bin/env python3
################################################################################
# Name:		verify_mcp_credentials.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	September 9, 2026
# Purpose:	Check that the AWS identity running mcp_server.py is actually
# 		scoped, instead of assuming it is
################################################################################
#
# An IAM policy you have not tested is a policy you are guessing about.
# This asks IAM itself -- via iam:SimulatePrincipalPolicy, which evaluates
# the real policy set attached to the calling identity without performing
# any of the actions -- whether the dangerous things are denied and the
# necessary things are still allowed.
#
# Nothing here creates, modifies, or deletes anything. Every call is a
# simulation.
#
# Exit status is 0 only if every DENIED probe came back denied and every
# ALLOWED probe came back allowed, so this is usable as a pre-flight check.

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Literal

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

DEFAULT_MANAGED_BY_TAG = "Ec2InstanceMaker"
Expectation = Literal["denied", "allowed"]


@dataclass
class Probe:
    name: str
    action: str
    resource: str
    expectation: Expectation
    why: str
    context: dict[str, list[str]] | None = None


def build_probes(account_id: str, region: str, iam_name_prefix: str, managed_by: str) -> list[Probe]:
    """The probes that matter.

    The "denied" ones are the failure modes an adversarial review actually
    found or that a compromised caller would reach for. The "allowed" ones
    exist so that over-tightening the policy fails loudly here rather than
    halfway through a real build, having already created a security group.
    """
    untagged_instance = f"arn:aws:ec2:{region}:{account_id}:instance/i-0123456789abcdef0"
    return [
        Probe(
            "terminate an instance this toolkit did not create",
            "ec2:TerminateInstances",
            untagged_instance,
            "denied",
            "the tag-scoped Deny is the single most important statement in the policy",
            {"ec2:ResourceTag/ManagedBy": ["something-else"]},
        ),
        Probe(
            "stop an instance this toolkit did not create",
            "ec2:StopInstances",
            untagged_instance,
            "denied",
            "same Deny, reached through manage_instance.py/stop_instance",
            {"ec2:ResourceTag/ManagedBy": ["something-else"]},
        ),
        Probe(
            "create an IAM user",
            "iam:CreateUser",
            f"arn:aws:iam::{account_id}:user/attacker",
            "denied",
            "no build path needs this; it is a classic escalation step",
        ),
        Probe(
            "create an access key",
            "iam:CreateAccessKey",
            f"arn:aws:iam::{account_id}:user/*",
            "denied",
            "credential exfiltration",
        ),
        Probe(
            "attach a policy to an arbitrary role",
            "iam:AttachRolePolicy",
            f"arn:aws:iam::{account_id}:role/SomeUnrelatedRole",
            "denied",
            "escalation via a role outside the --iam_name_prefix namespace",
        ),
        Probe(
            "modify this toolkit's own permissions boundary",
            "iam:CreatePolicyVersion",
            f"arn:aws:iam::{account_id}:policy/Ec2InstanceMakerCreatedRoleBoundary",
            "denied",
            "a boundary the caller can rewrite is not a boundary",
        ),
        Probe(
            "delete an arbitrary S3 bucket",
            "s3:DeleteBucket",
            "arn:aws:s3:::some-unrelated-bucket",
            "denied",
            "the instance policy tiers grant this; the caller role must not",
        ),
        Probe(
            "read every object in the account",
            "s3:GetObject",
            "arn:aws:s3:::some-unrelated-bucket/*",
            "denied",
            "blast radius if the calling model is injected",
        ),
        Probe(
            "describe instances",
            "ec2:DescribeInstances",
            "*",
            "allowed",
            "every read-only MCP tool needs this",
        ),
        Probe(
            "create a security group",
            "ec2:CreateSecurityGroup",
            "*",
            "allowed",
            "phase 2 of every build",
        ),
        Probe(
            "create a role inside the prefix namespace",
            "iam:CreateRole",
            f"arn:aws:iam::{account_id}:role/{iam_name_prefix}-role-example",
            "allowed",
            "phase 3 of every build (requires the permissions boundary)",
            {"iam:PermissionsBoundary": [f"arn:aws:iam::{account_id}:policy/Ec2InstanceMakerCreatedRoleBoundary"]},
        ),
        Probe(
            "create a role WITHOUT the permissions boundary",
            "iam:CreateRole",
            f"arn:aws:iam::{account_id}:role/{iam_name_prefix}-role-example",
            "denied",
            "a role created without the boundary could exceed the caller's own rights",
        ),
        Probe(
            "terminate an instance this toolkit did create",
            "ec2:TerminateInstances",
            untagged_instance,
            "allowed",
            "teardown must still work",
            {"ec2:ResourceTag/ManagedBy": [managed_by]},
        ),
    ]


def _context_entries(context: dict[str, list[str]] | None) -> list[dict[str, Any]]:
    if not context:
        return []
    return [{"ContextKeyName": key, "ContextKeyValues": values, "ContextKeyType": "string"} for key, values in context.items()]


def simulate(iam_client: Any, principal_arn: str, probe: Probe) -> str:
    response = iam_client.simulate_principal_policy(
        PolicySourceArn=principal_arn,
        ActionNames=[probe.action],
        ResourceArns=[probe.resource],
        ContextEntries=_context_entries(probe.context),
    )
    return str(response["EvaluationResults"][0]["EvalDecision"])


def resolve_principal_arn(caller_arn: str) -> str:
    """iam:SimulatePrincipalPolicy wants a user or role ARN.

    An assumed-role identity reports as
    arn:aws:sts::<acct>:assumed-role/<RoleName>/<SessionName>, which the API
    rejects, so convert it back to the underlying role ARN.
    """
    if ":assumed-role/" in caller_arn:
        account_id = caller_arn.split(":")[4]
        role_name = caller_arn.split(":assumed-role/")[1].split("/")[0]
        return f"arn:aws:iam::{account_id}:role/{role_name}"
    return caller_arn


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify that the AWS identity running mcp_server.py is scoped as intended. Read-only: every check is an IAM policy simulation, not a real call.")
    parser.add_argument("--region", "-r", default="us-east-1", help="region to build the probe ARNs in (default = us-east-1)")
    parser.add_argument("--iam_name_prefix", default="Ec2InstanceMaker", help="the --iam_name_prefix the toolkit builds with (default = Ec2InstanceMaker)")
    parser.add_argument("--managed_by_tag", default=DEFAULT_MANAGED_BY_TAG, help=f"value of the ManagedBy tag (default = {DEFAULT_MANAGED_BY_TAG})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        caller = boto3.client("sts").get_caller_identity()
    except (ClientError, NoCredentialsError) as e:
        print(f"Could not determine the calling identity: {e}")
        return 2
    principal_arn = resolve_principal_arn(str(caller["Arn"]))
    account_id = str(caller["Account"])
    print("")
    print(f"Identity : {caller['Arn']}")
    print(f"Simulated: {principal_arn}")
    print(f"Account  : {account_id}   Region: {args.region}")
    print("")

    iam_client = boto3.client("iam")
    probes = build_probes(account_id, args.region, args.iam_name_prefix, args.managed_by_tag)
    failures = []
    for probe in probes:
        try:
            decision = simulate(iam_client, principal_arn, probe)
        except ClientError as e:
            print(f"  ERROR  {probe.name}: {e}")
            failures.append(probe)
            continue
        satisfied = decision == "allowed" if probe.expectation == "allowed" else decision != "allowed"
        status = "  ok   " if satisfied else "  FAIL "
        print(f"{status} {probe.name}")
        print(f"         want {probe.expectation}, got {decision}")
        if not satisfied:
            print(f"         why it matters: {probe.why}")
            failures.append(probe)
    print("")
    if failures:
        print(f"{len(failures)} of {len(probes)} checks FAILED -- this identity is not scoped as intended.")
        print("Do not enable the mutating MCP tools against it until these are resolved.")
        return 1
    print(f"All {len(probes)} checks passed.")
    print("")
    print("Note what this does and does not prove. It confirms the policy denies")
    print("what it should and allows what the toolkit needs. It cannot bound the")
    print("NUMBER of instances a build launches -- IAM has no condition key for")
    print("RunInstances count. Use an EC2 vCPU service quota for that.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
