import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


# This suite is entirely mock-based and must never reach the network. That
# was previously a convention rather than a guarantee: nothing enforced it,
# and five tests in TestResolveSecurityGroup were in fact issuing live
# requests to the EC2 instance metadata service (169.254.169.254) because
# they constructed a real boto3 ec2.SecurityGroup resource just to read its
# .id, which made boto3 walk the credential chain.
#
# The danger is not those IMDS probes themselves -- it is that with no
# enforcement, a future test that calls a real client method would silently
# hit whichever AWS account the developer (or a CI instance profile) happens
# to be configured for, and would still look like a passing test. For a
# toolkit whose functions create and delete billable resources, that is a
# failure mode worth making structurally impossible rather than merely
# unlikely.
#
# Blocking at botocore's HTTP layer catches every client and resource in one
# place, regardless of which service or which construction path is used.
@pytest.fixture(autouse=True)
def block_real_aws_network(monkeypatch):
    def _refuse(self, request, *args, **kwargs):
        raise RuntimeError(
            "A test attempted a real network request to "
            f"{getattr(request, 'url', '<unknown>')}. The test suite must not "
            "contact AWS (or the instance metadata service). Mock the boto3 "
            "client, resource, or session instead of constructing a real one."
        )

    monkeypatch.setattr("botocore.httpsession.URLLib3Session.send", _refuse)
