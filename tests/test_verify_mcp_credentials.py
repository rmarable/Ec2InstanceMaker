"""Tests for verify_mcp_credentials.py and the IAM policy documents in iam/.

The policy documents cannot be validated for real without an AWS account --
that asymmetry is called out in README.md -- so what is testable here is
their structure and the specific properties they are supposed to have: that
the guardrail Denies exist, that they are Denies rather than absent Allows,
and that the placeholders are the ones the setup instructions tell an
operator to substitute.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

import verify_mcp_credentials

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IAM_DIR = os.path.join(REPO_ROOT, "iam")


def _policy(name):
    with open(os.path.join(IAM_DIR, name)) as fh:
        return json.load(fh)


def _statements(policy, effect=None):
    return [s for s in policy["Statement"] if effect is None or s["Effect"] == effect]


def _actions(statement):
    action = statement.get("Action", statement.get("NotAction", []))
    return [action] if isinstance(action, str) else action


class TestMcpServerPolicyGuardrails:
    def setup_method(self):
        self.policy = _policy("McpServerPolicy.json")

    def test_is_a_valid_policy_document(self):
        assert self.policy["Version"] == "2012-10-17"
        assert self.policy["Statement"]

    def test_untagged_instances_are_denied_not_merely_unmentioned(self):
        # An explicit Deny is what makes this hold even if a future Allow
        # gets broader. Relying on the absence of an Allow would not.
        deny = next(s for s in _statements(self.policy, "Deny") if s["Sid"] == "DenyStateChangesOnResourcesThisToolkitDidNotCreate")
        assert "ec2:TerminateInstances" in _actions(deny)
        assert deny["Condition"]["StringNotEquals"]["ec2:ResourceTag/ManagedBy"] == "Ec2InstanceMaker"

    def test_role_creation_requires_the_permissions_boundary(self):
        deny = next(s for s in _statements(self.policy, "Deny") if s["Sid"] == "DenyCreatingRolesWithoutThePermissionsBoundary")
        assert "iam:CreateRole" in _actions(deny)
        assert "iam:PermissionsBoundary" in deny["Condition"]["StringNotEquals"]

    def test_the_boundary_and_this_role_cannot_be_tampered_with(self):
        # A boundary the caller can rewrite is not a boundary.
        deny = next(s for s in _statements(self.policy, "Deny") if s["Sid"] == "DenyTamperingWithTheBoundaryOrThisRole")
        for action in ("iam:CreatePolicyVersion", "iam:DeleteRolePermissionsBoundary", "iam:PutRolePolicy"):
            assert action in _actions(deny)

    def test_identity_creation_is_denied_outright(self):
        deny = next(s for s in _statements(self.policy, "Deny") if s["Sid"] == "DenyIdentityCreationOutright")
        for action in ("iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile"):
            assert action in _actions(deny)

    def test_region_and_instance_type_guardrails_exist(self):
        sids = {s["Sid"] for s in _statements(self.policy, "Deny")}
        assert "DenyAnythingOutsideTheAllowedRegions" in sids
        assert "DenyInstanceTypesOutsideTheAllowlist" in sids

    def test_iam_writes_are_scoped_to_the_prefix_namespace(self):
        allow = next(s for s in _statements(self.policy, "Allow") if s["Sid"] == "IAMScopedToThePrefixNamespace")
        assert all("<EC2_IAM_PREFIX>-*" in arn for arn in allow["Resource"])
        assert "*" not in allow["Resource"]

    def test_passrole_is_scoped_and_service_restricted(self):
        allow = next(s for s in _statements(self.policy, "Allow") if s["Sid"] == "PassOnlyRolesInThePrefixNamespace")
        assert "<EC2_IAM_PREFIX>-*" in allow["Resource"]
        assert allow["Condition"]["StringEquals"]["iam:PassedToService"] == "ec2.amazonaws.com"

    def test_no_allow_statement_grants_star_action(self):
        for statement in _statements(self.policy, "Allow"):
            assert "*" not in _actions(statement), statement["Sid"]

    @pytest.mark.parametrize("placeholder", ["<AWS_ACCOUNT_ID>", "<ALLOWED_REGIONS>", "<ALLOWED_INSTANCE_TYPES>", "<EC2_IAM_PREFIX>"])
    def test_documented_placeholders_are_present(self, placeholder):
        # README tells operators to substitute exactly these.
        assert placeholder in json.dumps(self.policy)


class TestCreatedRoleBoundary:
    def setup_method(self):
        self.policy = _policy("CreatedRoleBoundary.json")

    def test_denies_the_iam_writes_that_enable_escalation(self):
        deny = _statements(self.policy, "Deny")[0]
        # ExtendedEc2InstancePolicy.json grants these to the instance role;
        # the boundary is what stops them mattering.
        for action in ("iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:CreateRole", "iam:PassRole"):
            assert action in _actions(deny)

    def test_still_permits_what_an_instance_legitimately_needs(self):
        allow = _statements(self.policy, "Allow")[0]
        for action in ("ssmmessages:*", "logs:PutLogEvents", "ec2:Describe*"):
            assert action in _actions(allow)


class TestTrustPolicy:
    def test_requires_mfa(self):
        policy = _policy("McpServerTrustPolicy.json")
        statement = policy["Statement"][0]
        assert statement["Action"] == "sts:AssumeRole"
        assert statement["Condition"]["Bool"]["aws:MultiFactorAuthPresent"] == "true"


class TestResolvePrincipalArn:
    def test_assumed_role_arn_is_converted_to_the_role_arn(self):
        # iam:SimulatePrincipalPolicy rejects an sts assumed-role ARN.
        assert (
            verify_mcp_credentials.resolve_principal_arn("arn:aws:sts::123456789012:assumed-role/Ec2InstanceMakerMcpServer/session-name") == "arn:aws:iam::123456789012:role/Ec2InstanceMakerMcpServer"
        )

    def test_user_arn_is_passed_through(self):
        assert verify_mcp_credentials.resolve_principal_arn("arn:aws:iam::123456789012:user/rmarable") == "arn:aws:iam::123456789012:user/rmarable"


class TestProbes:
    def _probes(self):
        return verify_mcp_credentials.build_probes("123456789012", "us-east-1", "Ec2InstanceMaker", "Ec2InstanceMaker")

    def test_covers_both_directions(self):
        expectations = {p.expectation for p in self._probes()}
        assert expectations == {"denied", "allowed"}

    def test_every_probe_explains_why_it_matters(self):
        assert all(p.why for p in self._probes())

    def test_teardown_of_a_managed_instance_must_still_be_allowed(self):
        # Over-tightening should fail here, not halfway through a real
        # teardown with resources already half-deleted.
        probe = next(p for p in self._probes() if p.name == "terminate an instance this toolkit did create")
        assert probe.expectation == "allowed"
        assert probe.context == {"ec2:ResourceTag/ManagedBy": ["Ec2InstanceMaker"]}

    def test_simulation_is_read_only(self):
        iam_client = MagicMock()
        iam_client.simulate_principal_policy.return_value = {"EvaluationResults": [{"EvalDecision": "explicitDeny"}]}
        probe = self._probes()[0]

        decision = verify_mcp_credentials.simulate(iam_client, "arn:aws:iam::123456789012:role/X", probe)

        assert decision == "explicitDeny"
        iam_client.simulate_principal_policy.assert_called_once()
        # Nothing but the simulate call may ever be made.
        assert [c[0] for c in iam_client.method_calls] == ["simulate_principal_policy"]

    def test_context_entries_are_shaped_for_the_api(self):
        entries = verify_mcp_credentials._context_entries({"ec2:ResourceTag/ManagedBy": ["x"]})
        assert entries == [{"ContextKeyName": "ec2:ResourceTag/ManagedBy", "ContextKeyValues": ["x"], "ContextKeyType": "string"}]
