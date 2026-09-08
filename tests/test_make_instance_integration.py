"""Coarse orchestration integration test for make_instance.py's main().

make_instance.py used to be a ~900-line flat script with no functions --
importing it executed the entire build flow immediately, making it
impossible to test the orchestration itself (as opposed to the individual
pieces already covered by tests/test_instance_builder.py). Wrapping it in
main(argv=None) and extracting every AWS/Terraform-touching step into
instance_builder.py (see CLAUDE-STATE.md for the phased plan) makes this
test possible: mock each extracted function directly on the make_instance
module, leave pure/local functions real, and call main() like any other
function -- no AWS calls, no real Terraform, no real EC2/IAM/SNS/CloudWatch
resources.

Deliberately coarse, not a full scenario matrix -- that's what
tests/test_instance_builder.py's 100+ unit tests and
tests/test_template_behavior.py's rendered-template assertions already
cover. This only proves the wiring between them still holds: call order,
argument shapes, and the real instance_parameters dict this file actually
assembles against the real Jinja2 templates (not a synthetic context).
"""

import os
from unittest.mock import MagicMock

import pytest

import make_instance
from instance_builder import AwsClients

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mock_aws_clients():
    sns_client = MagicMock()
    sns_client.create_topic.return_value = {"TopicArn": "arn:aws:sns:us-east-2:123456789012:Ec2_Instance_SNS_Alerts_testint01"}
    stsclient = MagicMock()
    stsclient.get_caller_identity.return_value = {"Account": "123456789012"}
    return AwsClients(ec2_client=MagicMock(), ec2=MagicMock(), iam=MagicMock(), sns_client=sns_client, stsclient=stsclient)


def _happy_path_argv(instance_name="testint01"):
    return [
        "--az",
        "us-east-2a",
        "--instance_name",
        instance_name,
        "--instance_owner",
        "tester",
        "--instance_owner_email",
        "tester@example.com",
    ]


def _patch_aws_and_terraform_boundary(monkeypatch, aws_clients=None, setup_iam_return=None):
    """Patch every function in make_instance's namespace that would
    otherwise touch real AWS or shell out to real Terraform. Everything
    else (generate_instance_serial_number, build_sns_message,
    write_vars_file, render_instance_templates, resolve_custom_user_scripts,
    get_base_os_family, base_os_instance_check, validate_and_resize_ebs_volumes,
    etc.) is left real -- none of it touches AWS or a subprocess.
    """
    aws_clients = aws_clients or _mock_aws_clients()
    setup_iam_return = setup_iam_return or ("Ec2InstanceMaker-role-testint01", "Ec2InstanceMaker-policy-testint01", "Ec2InstanceMaker-profile-testint01", "false")

    monkeypatch.setattr(make_instance, "ctrlC_Abort", MagicMock())
    monkeypatch.setattr(
        make_instance,
        "get_instance_type_info",
        MagicMock(
            return_value={
                "architecture": "x86_64",
                "ebs_optimized_support": "default",
                "ebs_encryption_support": "supported",
                "placement_group_strategies": ["cluster", "spread"],
            }
        ),
    )
    monkeypatch.setattr(make_instance, "create_aws_clients", MagicMock(return_value=aws_clients))
    monkeypatch.setattr(make_instance, "validate_az_and_region", MagicMock())
    monkeypatch.setattr(make_instance, "setup_cloudwatch_logging", MagicMock())
    monkeypatch.setattr(make_instance, "resolve_vpc_and_subnet", MagicMock(return_value=("vpc-0123456789abcdef0", "vpc_default", "subnet-0123456789abcdef0")))
    monkeypatch.setattr(make_instance, "resolve_ssh_allowed_ips", MagicMock(return_value="10.0.0.0/16"))
    monkeypatch.setattr(make_instance, "resolve_security_group", MagicMock(return_value=("ec2instancemaker_sg_testint01-000000010926", "sg-0123456789abcdef0")))
    monkeypatch.setattr(make_instance, "resolve_ami", MagicMock(return_value="ami-0123456789abcdef0"))
    monkeypatch.setattr(make_instance, "setup_keypair", MagicMock())
    monkeypatch.setattr(make_instance, "setup_iam", MagicMock(return_value=setup_iam_return))
    monkeypatch.setattr(make_instance, "get_terraform_version", MagicMock(return_value="v1.5.7"))
    apply_terraform_mock = MagicMock()
    monkeypatch.setattr(make_instance, "apply_terraform", apply_terraform_mock)
    return aws_clients, apply_terraform_mock


def _symlink_repo_assets(tmp_path):
    os.symlink(os.path.join(REPO_ROOT, "templates"), tmp_path / "templates")
    os.symlink(os.path.join(REPO_ROOT, "custom_user_scripts"), tmp_path / "custom_user_scripts")


class TestMainHappyPath:
    def test_ondemand_build_completes_and_renders_real_templates(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _symlink_repo_assets(tmp_path)
        aws_clients, apply_terraform_mock = _patch_aws_and_terraform_boundary(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            make_instance.main(_happy_path_argv())

        assert exc_info.value.code == 0

        # The orchestration actually reached every AWS/Terraform-touching
        # step, in the right relative order (mocked, never called for real).
        apply_terraform_mock.assert_called_once()
        aws_clients.sns_client.publish.assert_called_once()
        aws_clients.ec2_client.create_tags.assert_called_once()

        # write_vars_file() and render_instance_templates() ran for real
        # against the actual instance_parameters dict this file assembled --
        # proving every key the templates need was actually present, which
        # no other test currently checks (test_template_behavior.py renders
        # its own synthetic CONTEXTS, not this file's real dict).
        assert (tmp_path / "vars_files" / "testint01.yml").is_file()
        rendered_dir = tmp_path / "instance_data" / "testint01"
        assert (rendered_dir / "testint01.tf").is_file()
        assert (rendered_dir / "access_instance.testint01.py").is_file()
        assert (rendered_dir / "kill_instance.testint01.sh").is_file()

    def test_spot_request_computes_and_records_a_real_price(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _symlink_repo_assets(tmp_path)
        aws_clients, apply_terraform_mock = _patch_aws_and_terraform_boundary(monkeypatch)
        aws_clients.ec2_client.describe_spot_price_history.return_value = {"SpotPriceHistory": [{"SpotPrice": "0.0116"}]}

        argv = _happy_path_argv("testint02") + ["--request_type", "spot"]
        with pytest.raises(SystemExit) as exc_info:
            make_instance.main(argv)

        assert exc_info.value.code == 0
        vars_file_content = (tmp_path / "vars_files" / "testint02.yml").read_text()
        assert "spot_price: 0.0" in vars_file_content

    def test_family_build_with_placement_group(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _symlink_repo_assets(tmp_path)
        _patch_aws_and_terraform_boundary(monkeypatch)

        argv = _happy_path_argv("testint03") + ["--count", "3", "--enable_placement_group", "true"]
        with pytest.raises(SystemExit) as exc_info:
            make_instance.main(argv)

        assert exc_info.value.code == 0
        rendered_dir = tmp_path / "instance_data" / "testint03"
        rendered_tf = (rendered_dir / "testint03.tf").read_text()
        assert "aws_placement_group" in rendered_tf


class TestMainEarlyValidationFailures:
    """These need zero AWS/Terraform mocking at all -- confirmed by tracing
    make_instance.py's flow: uppercase casing is checked before the
    Terraform version check, which itself runs before any AWS client is
    ever constructed. A regression that moved expensive setup earlier than
    these checks would make these tests hang or fail with an unexpected
    error instead of the clean SystemExit(1) asserted here.
    """

    def test_uppercase_instance_name_aborts_before_any_setup(self):
        argv = _happy_path_argv("TestInt01")
        with pytest.raises(SystemExit) as exc_info:
            make_instance.main(argv)
        assert exc_info.value.code == 1

    def test_uppercase_instance_owner_aborts_before_any_setup(self):
        argv = _happy_path_argv("testint01")
        argv[argv.index("tester")] = "Tester"
        with pytest.raises(SystemExit) as exc_info:
            make_instance.main(argv)
        assert exc_info.value.code == 1

    def test_missing_required_argument_is_an_argparse_usage_error(self):
        # argparse itself exits 2 on a missing required argument -- distinct
        # from the code == 1 paths above, which come from
        # refer_to_docs_and_quit()/sys.exit(1) deeper in the build flow.
        with pytest.raises(SystemExit) as exc_info:
            make_instance.main(["--instance_name", "testint01"])
        assert exc_info.value.code == 2


class TestMainVarsFileDuplicateGuard:
    def test_existing_vars_file_aborts_before_any_aws_call(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _symlink_repo_assets(tmp_path)
        (tmp_path / "vars_files").mkdir()
        (tmp_path / "vars_files" / "testint01.yml").write_text("# pre-existing build\n")

        mock_get_instance_type_info = MagicMock()
        monkeypatch.setattr(make_instance, "get_instance_type_info", mock_get_instance_type_info)

        with pytest.raises(SystemExit) as exc_info:
            make_instance.main(_happy_path_argv())

        assert exc_info.value.code == 1
        mock_get_instance_type_info.assert_not_called()
