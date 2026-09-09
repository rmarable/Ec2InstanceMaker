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

import dataclasses
import os
from unittest.mock import MagicMock

import pytest

import aux_data
import make_instance
from instance_builder import AwsClients

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mock_aws_clients():
    sns_client = MagicMock()
    sns_client.create_topic.return_value = {"TopicArn": "arn:aws:sns:us-east-2:123456789012:Ec2_Instance_SNS_Alerts_testint01"}
    stsclient = MagicMock()
    stsclient.get_caller_identity.return_value = {"Account": "123456789012"}
    return AwsClients(ec2_client=MagicMock(), ec2=MagicMock(), iam=MagicMock(), sns_client=sns_client, stsclient=stsclient, logs_client=MagicMock())


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
    monkeypatch.setattr(make_instance, "resolve_security_group", MagicMock(return_value=("ec2instancemaker_sg_testint01-000000010926", "sg-0123456789abcdef0", "false")))
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


class TestMainConcurrencyGuard:
    """run_build() now holds instance_lock() (instance_builder.py) from
    right before abort_if_vars_file_exists() through the end of the
    build -- proves a second concurrent build of the same instance_name
    aborts cleanly instead of racing on instance_data_dir/vars_files
    state, and proves uppercase-name validation still runs with zero
    filesystem side effects (no lock file created) since it happens
    before the lock is ever acquired.
    """

    def test_second_concurrent_build_of_same_name_aborts_without_touching_aws(self, tmp_path, monkeypatch):
        import fcntl

        monkeypatch.chdir(tmp_path)
        _symlink_repo_assets(tmp_path)
        mock_get_instance_type_info = MagicMock()
        monkeypatch.setattr(make_instance, "get_instance_type_info", mock_get_instance_type_info)

        (tmp_path / "active_instances").mkdir()
        lock_path = tmp_path / "active_instances" / "testint01.lock"
        holder_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(SystemExit) as exc_info:
                make_instance.main(_happy_path_argv())
            assert exc_info.value.code == 1
            mock_get_instance_type_info.assert_not_called()
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_uppercase_instance_name_creates_no_lock_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            make_instance.main(_happy_path_argv("TestInt01"))
        assert not (tmp_path / "active_instances").exists()


class TestRollbackOnFailure:
    """--rollback_on_failure=true tears down whatever a failed build created.

    Without it (the default), a build that dies after phase 2 leaves a
    security group, keypair, IAM role/policy/profile, SNS topic and
    CloudWatch log group behind with no automated cleanup at all -- and
    the duplicate-build guard then refuses the retry.
    """

    def _setup(self, tmp_path, monkeypatch, failure):
        monkeypatch.chdir(tmp_path)
        _symlink_repo_assets(tmp_path)
        _patch_aws_and_terraform_boundary(monkeypatch)
        rollback = MagicMock()
        monkeypatch.setattr(make_instance, "rollback_partial_build", rollback)
        monkeypatch.setattr(make_instance, "render_and_apply", MagicMock(side_effect=failure))
        return rollback

    def test_disabled_by_default_no_rollback_attempted(self, tmp_path, monkeypatch):
        rollback = self._setup(tmp_path, monkeypatch, RuntimeError("terraform blew up"))

        with pytest.raises(RuntimeError):
            make_instance.run_build(_happy_path_argv(), aux_data.refer_to_docs_and_quit)

        rollback.assert_not_called()

    def test_enabled_rolls_back_then_reraises(self, tmp_path, monkeypatch):
        rollback = self._setup(tmp_path, monkeypatch, RuntimeError("terraform blew up"))

        with pytest.raises(RuntimeError):
            make_instance.run_build([*_happy_path_argv(), "--rollback_on_failure=true"], aux_data.refer_to_docs_and_quit)

        rollback.assert_called_once()

    def test_rollback_also_runs_on_sys_exit(self, tmp_path, monkeypatch):
        # refer_to_docs_and_quit() raises SystemExit, which is a
        # BaseException, not an Exception -- catching only Exception would
        # miss every validation failure, i.e. the common case.
        rollback = self._setup(tmp_path, monkeypatch, SystemExit(1))

        with pytest.raises(SystemExit):
            make_instance.run_build([*_happy_path_argv(), "--rollback_on_failure=true"], aux_data.refer_to_docs_and_quit)

        rollback.assert_called_once()


class TestRollbackPartialBuild:
    """The rollback itself, as opposed to the wiring tested above."""

    def _settings(self):
        settings = MagicMock()
        settings.instance_name = "testint01"
        settings.enable_cloudwatch_logs = "true"
        return settings

    def test_prefers_the_generated_kill_script_when_one_exists(self, tmp_path, monkeypatch):
        # The kill script also runs `terraform destroy`, so it is the more
        # complete teardown whenever the build got far enough to render it.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "kill-instance.testint01.sh").write_text("#!/bin/bash\n")
        run = MagicMock(return_value=MagicMock(returncode=0))
        monkeypatch.setattr(make_instance.subprocess, "run", run)
        cleanup = MagicMock()
        monkeypatch.setattr(make_instance, "cleanup_partial_build", cleanup)

        make_instance.rollback_partial_build(self._settings(), MagicMock(), MagicMock(), MagicMock(), "./instance_data/testint01/")

        assert run.call_args.args[0] == ["bash", "./kill-instance.testint01.sh"]
        cleanup.assert_not_called()

    def test_falls_back_to_direct_cleanup_before_templates_were_rendered(self, tmp_path, monkeypatch):
        # A failure inside phase 2 or 3 still leaves a security group,
        # keypair, IAM role/policy/profile, SNS topic and log group behind,
        # but no kill script has been generated yet.
        monkeypatch.chdir(tmp_path)
        cleanup = MagicMock(return_value=[])
        monkeypatch.setattr(make_instance, "cleanup_partial_build", cleanup)

        make_instance.rollback_partial_build(self._settings(), MagicMock(), MagicMock(), MagicMock(), "./instance_data/testint01/")

        cleanup.assert_called_once()

    def test_nothing_created_yet_is_a_no_op(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        cleanup = MagicMock()
        monkeypatch.setattr(make_instance, "cleanup_partial_build", cleanup)

        make_instance.rollback_partial_build(self._settings(), MagicMock(), None, None, "./instance_data/testint01/")

        cleanup.assert_not_called()
        assert "nothing to roll back" in capsys.readouterr().out.lower()


class TestBuildReportCarriesNoSecrets:
    """BuildReport is returned verbatim by mcp_server.build_instance via
    dataclasses.asdict(), so anything in it lands in an MCP client's model
    context and in the conversation transcript -- which persist far longer
    than console scrollback. It used to carry the Windows password table,
    i.e. live plaintext local Administrator credentials.
    """

    def test_the_report_has_no_field_that_could_hold_a_password(self):
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(make_instance.BuildReport)}
        assert "windows_password_table" not in field_names
        assert "windows_password_retrieval_command" in field_names

    def test_a_windows_report_carries_the_command_not_the_table(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _symlink_repo_assets(tmp_path)
        _patch_aws_and_terraform_boundary(monkeypatch)
        # build_windows_password_table would otherwise need real AWS output.
        monkeypatch.setattr(make_instance, "build_windows_password_table", MagicMock(return_value="SUPERSECRETPASSWORDTABLE"))

        report = make_instance.run_build([*_happy_path_argv(), "--base_os=windows2022"], aux_data.refer_to_docs_and_quit)

        serialized = repr(dataclasses.asdict(report))
        assert "SUPERSECRETPASSWORDTABLE" not in serialized
        assert report.windows_password_retrieval_command == "./access_instance.py -N testint01"
