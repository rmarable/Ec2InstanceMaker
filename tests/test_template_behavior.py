"""Behavioral assertions on *rendered* template output.

scripts/lint_templates.py (run via pre-commit) only checks that rendered
output is syntactically valid (shellcheck/py_compile/terraform validate) --
it never asserts anything about which conditional branch actually rendered
or what it does. That's a real gap: a Jinja logic bug (wrong branch, wrong
condition) produces perfectly valid, perfectly wrong shell/Python, and
nothing catches it. These tests fill that gap for the specific branches
that have actually broken before (see CLAUDE-STATE.md).
"""

import copy
import os
import tempfile

import pytest

from template_engine import TEMPLATE_MAP, _build_render_context, render_instance_templates

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_CONTEXT = {
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
    "cloudwatch_log_group": "/ec2instancemaker/dev01",
    "preserve_cloudwatch_logs": "false",
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
}


def render(overrides):
    ctx = copy.deepcopy(BASE_CONTEXT)
    ctx.update(overrides)
    with tempfile.TemporaryDirectory() as scratch_root:
        os.symlink(os.path.join(REPO_ROOT, "templates"), os.path.join(scratch_root, "templates"))
        os.symlink(os.path.join(REPO_ROOT, "custom_user_scripts"), os.path.join(scratch_root, "custom_user_scripts"))
        instance_name = ctx["instance_name"]
        instance_data_dir = os.path.join(scratch_root, "instance_data", instance_name)
        os.makedirs(instance_data_dir)
        render_instance_templates(ctx, scratch_root, instance_data_dir)
        full_context = _build_render_context(ctx, scratch_root, instance_data_dir)
        rendered = {}
        for src, dest_key in TEMPLATE_MAP:
            path = os.path.join(instance_data_dir, full_context[dest_key])
            if os.path.exists(path):
                with open(path) as fh:
                    rendered[src] = fh.read()
        # custom_user_postboot_script.j2_<name> isn't in TEMPLATE_MAP (there
        # can be zero or many per build) -- capture each rendered postboot
        # script by its own filename instead.
        for filename in sorted(os.listdir(instance_data_dir)):
            if filename.startswith("custom_user_postboot_script."):
                with open(os.path.join(instance_data_dir, filename)) as fh:
                    rendered[filename] = fh.read()
        return rendered


@pytest.fixture(autouse=True)
def _cleanup_root_symlinks():
    # render_instance_templates() symlinks kill-instance.<name>.sh /
    # build-ami.<name>.sh into local_workingdir, which we pass as a
    # scratch_root (not the real repo root), so nothing needs cleanup here
    # -- this fixture exists only to make that non-obviousness explicit.
    yield


class TestKillInstancePreservesIamRoleCorrectly:
    """Regression tests for a bug where the rendered kill script's
    "PRESERVE_IAM_ROLE is true, don't delete" branch had a bash condition
    that could never be true, so the "Preserved..." confirmation lines
    were dead code -- and, more importantly, guards against ever
    reintroducing a version of this template that deletes IAM entities it
    was told to preserve.
    """

    def test_preserve_true_does_not_delete_iam_role(self):
        rendered = render({"preserve_iam_role": "true"})
        kill_script = rendered["kill_instance.j2"]
        assert "aws iam delete-role --role-name" not in kill_script
        assert "aws iam delete-role-policy" not in kill_script
        assert "Preserved EC2 IAM instance role" in kill_script

    def test_preserve_false_does_delete_iam_role(self):
        rendered = render({"preserve_iam_role": "false"})
        kill_script = rendered["kill_instance.j2"]
        assert "aws iam delete-role --role-name" in kill_script
        assert "Preserved EC2 IAM instance role" not in kill_script


class TestDefaultEc2TemplateWindowsVsLinux:
    def test_windows_gets_password_data_and_no_ssm_provisioning(self):
        rendered = render(
            {
                "base_os": "windows2022",
                "architecture": "x86_64",
                "ec2_user": "Administrator",
                "is_windows": True,
                "package_manager": None,
                "awscli_preinstalled": None,
            }
        )
        tf = rendered["DEFAULT_EC2_TEMPLATE.j2"]
        assert "get_password_data" in tf
        assert 'provisioner "local-exec"' not in tf

    def test_linux_provisions_via_ssm_not_ssh_and_has_no_password_data(self):
        # Terraform's own build-time provisioning no longer uses SSH at all
        # -- it shells out to ssm_provision.<name>.sh, which uses SSM
        # send-command instead. This is what makes it possible for
        # --ssh_allowed_ips to be scoped tightly (or the port never opened
        # at all) without breaking `terraform apply` itself.
        rendered = render({"base_os": "al2023"})
        tf = rendered["DEFAULT_EC2_TEMPLATE.j2"]
        assert "get_password_data" not in tf
        assert 'provisioner "file"' not in tf
        assert 'provisioner "local-exec"' in tf
        assert "bash ssm_provision.dev01.sh" in tf
        assert "${self.id}" in tf


class TestSsmProvisionInstanceIdReference:
    """aws_instance's .id is the real EC2 instance ID; aws_spot_instance_request's
    .id is the *spot request* ID, not the instance -- .spot_instance_id is
    the actual instance. Getting this wrong means ssm_provision.<name>.sh
    gets a spot request ID instead of an instance ID and every SSM call in
    it fails immediately. This regression is exactly why
    output "instance_id_list" already branches the same way (see
    DEFAULT_EC2_TEMPLATE.j2's two output blocks).
    """

    def test_ondemand_passes_self_id(self):
        # request_type == "ondemand" never renders the spot-tagging
        # local-exec block at all, so "spot_instance_id" appearing
        # anywhere here would only be its use in the SSM-provisioning line.
        rendered = render({"request_type": "ondemand"})["DEFAULT_EC2_TEMPLATE.j2"]
        assert "bash ssm_provision.dev01.sh ${self.id}" in rendered
        assert "spot_instance_id" not in rendered

    def test_spot_passes_self_spot_instance_id(self):
        rendered = render({"request_type": "spot", "spot_price": "0.05"})["DEFAULT_EC2_TEMPLATE.j2"]
        assert "bash ssm_provision.dev01.sh ${self.spot_instance_id}" in rendered


class TestSsmProvisionOutputPersistence:
    """A successful SSM command's output used to be discarded entirely --
    only a failure's StandardErrorContent was ever surfaced, and only to
    the console, never saved anywhere. Both are fixed: every run (success
    or failure) appends its full stdout/stderr to a local log file.
    """

    def test_success_path_saves_output_to_log_file(self):
        rendered = render({})["ssm_provision.j2"]
        assert 'LOG_FILE="ssm_provision.dev01.log"' in rendered
        success_block = rendered.split("Success)")[1].split("Failed")[0]
        assert 'save_command_output "$script_path" "$command_id"' in success_block

    def test_failure_path_also_saves_output_and_says_where(self):
        rendered = render({})["ssm_provision.j2"]
        failure_block = rendered.split("Failed | Cancelled | TimedOut)")[1]
        assert 'save_command_output "$script_path" "$command_id"' in failure_block
        assert "Full output saved to: $LOG_FILE" in failure_block

    def test_save_command_output_captures_both_streams(self):
        rendered = render({})["ssm_provision.j2"]
        save_fn = rendered.split("save_command_output() {")[1].split("\n}\n")[0]
        assert "StandardOutputContent" in save_fn
        assert "StandardErrorContent" in save_fn


class TestManagedByTag:
    """manage_instance.py identifies which EC2 instances this toolkit is
    allowed to start/stop/reboot/terminate by filtering on the ManagedBy
    tag -- it has to actually be on every instance this toolkit creates
    (ondemand tags block, volume_tags block, and the spot-tagging
    local-exec command, which is the only place a spot request's *launched
    instance* actually gets tagged) or that safety filter silently finds
    nothing.
    """

    def test_ondemand_tags_and_volume_tags_include_managed_by(self):
        rendered = render({"request_type": "ondemand"})["DEFAULT_EC2_TEMPLATE.j2"]
        assert rendered.count('ManagedBy               = "Ec2InstanceMaker"') == 2

    def test_spot_create_tags_command_includes_managed_by(self):
        rendered = render({"request_type": "spot", "spot_price": "0.05"})["DEFAULT_EC2_TEMPLATE.j2"]
        assert "Key=ManagedBy,Value=Ec2InstanceMaker" in rendered

    def test_spot_family_create_tags_command_includes_managed_by(self):
        rendered = render({"request_type": "spot", "spot_price": "0.05", "count": 3})["DEFAULT_EC2_TEMPLATE.j2"]
        assert "Key=ManagedBy,Value=Ec2InstanceMaker" in rendered


class TestBuildInstancePackageManagerAndAwscli:
    """Regression tests for the base_os-family consolidation: build_instance.j2
    used to derive yum-vs-apt independently via base_os substring matching;
    it now reads package_manager from the render context instead. AWS CLI
    installation itself no longer happens here at all -- it moved to
    instance_userdata.j2 as a prelogin step (see
    TestInstanceUserdataAwsCliInstall), so build_instance.j2 never installs
    it regardless of awscli_preinstalled.
    """

    def test_al2023_uses_yum(self):
        rendered = render({"base_os": "al2023", "package_manager": "yum", "awscli_preinstalled": True})
        script = rendered["build_instance.j2"]
        assert "sudo yum -y update" in script
        assert "sudo apt-get update" not in script
        assert "awscli-bundle.zip" not in script
        assert "awscli-exe-linux" not in script

    def test_rocky9_uses_yum(self):
        rendered = render({"base_os": "rocky9", "package_manager": "yum", "awscli_preinstalled": False, "ec2_user": "rocky", "ec2_user_home": "/home/rocky"})
        script = rendered["build_instance.j2"]
        assert "sudo yum -y update" in script
        assert "awscli-bundle.zip" not in script
        assert "awscli-exe-linux" not in script

    def test_ubuntu2404_uses_apt(self):
        rendered = render({"base_os": "ubuntu2404", "package_manager": "apt", "awscli_preinstalled": False, "ec2_user": "ubuntu", "ec2_user_home": "/home/ubuntu"})
        script = rendered["build_instance.j2"]
        assert "sudo apt-get update" in script
        assert "sudo yum -y update" not in script
        assert "awscli-bundle.zip" not in script
        assert "awscli-exe-linux" not in script


class TestAccessInstanceHardening:
    """Regression tests for the access_instance.j2 security fixes: no more
    shell=True string-concatenated ssh/get-password-data commands, and the
    plaintext-password CSV temp file is always cleaned up.
    """

    def test_no_shell_true_ssh_command(self):
        # The static "terraform show | grep | awk" pipelines legitimately
        # still use shell=True (no interpolated/untrusted data flows into
        # them) -- only the ssh/get-password-data commands, which used to
        # concatenate operator/AWS-derived data into a shell=True string,
        # needed to move to list-form subprocess calls.
        rendered = render({"base_os": "al2023"})
        access_script = rendered["access_instance.j2"]
        for line in access_script.splitlines():
            if "ssh" in line or "get-password-data" in line:
                assert "shell=True" not in line, line

    def test_connects_via_ssm_session_manager_not_ssh(self):
        # access_instance.j2 no longer opens a direct SSH connection at
        # all -- it targets the instance via SSM Session Manager, so no
        # inbound SSH port needs to be reachable from wherever the
        # operator runs this script.
        rendered = render({"base_os": "al2023"})
        access_script = rendered["access_instance.j2"]
        assert "subprocess.run(['aws', 'ssm', 'start-session'," in access_script
        assert "subprocess.run(['ssh'," not in access_script

    def test_csv_temp_file_always_removed(self):
        for count in (1, 2):
            rendered = render({"base_os": "al2023", "count": count})
            access_script = rendered["access_instance.j2"]
            assert "os.remove(csvTempFile)" in access_script

    def test_csv_temp_file_uses_tempfile_not_predictable_path(self):
        rendered = render({"base_os": "al2023"})
        access_script = rendered["access_instance.j2"]
        assert "tempfile.mkstemp" in access_script
        assert "/tmp/_csvTempFile_" not in access_script  # nosec B108 - asserting the old insecure pattern is ABSENT, not present

    @staticmethod
    def _assert_wrapped_in_try_except_keyboard_interrupt(script_lines, needle):
        # Regression tests for a real bug found via a live session: Ctrl-C
        # while an `aws ssm start-session` call is active used to propagate
        # as an unhandled KeyboardInterrupt, printing a raw Python traceback
        # instead of exiting cleanly (both this script and the aws CLI child
        # process are in the same terminal foreground process group, so
        # Ctrl-C reaches both). Checks a `try:` line precedes the call
        # (allowing for a multi-line subprocess.run(...) call, e.g. the RDP
        # tunnel's) and an `except KeyboardInterrupt:` follows it shortly
        # after, regardless of exact indentation depth.
        call_index = next(i for i, line in enumerate(script_lines) if needle in line)
        assert any(script_lines[i].strip() == "try:" for i in range(max(call_index - 3, 0), call_index))
        assert any(script_lines[i].strip() == "except KeyboardInterrupt:" for i in range(call_index + 1, min(call_index + 10, len(script_lines))))

    def test_single_instance_ssm_session_survives_ctrl_c(self):
        rendered = render({"base_os": "al2023", "count": 1})
        access_script = rendered["access_instance.j2"]
        self._assert_wrapped_in_try_except_keyboard_interrupt(access_script.splitlines(), "subprocess.run(['aws', 'ssm', 'start-session', '--target', ec2_InstanceId")

    def test_family_ssm_session_survives_ctrl_c(self):
        rendered = render({"base_os": "al2023", "count": 3})
        access_script = rendered["access_instance.j2"]
        self._assert_wrapped_in_try_except_keyboard_interrupt(access_script.splitlines(), "subprocess.run(['aws', 'ssm', 'start-session', '--target', ssh_instance_id")


class TestAccessInstanceWindowsRdpTunnel:
    """Windows access no longer requires exposing port 3389 to any CIDR --
    it tunnels RDP through an SSM Session Manager port-forwarding session
    instead. This covers the single-instance and family selection paths,
    since the family path (menu-driven target selection) didn't exist
    before this feature.
    """

    WINDOWS_OVERRIDES = {
        "base_os": "windows2022",
        "architecture": "x86_64",
        "ec2_user": "Administrator",
        "is_windows": True,
        "package_manager": None,
        "awscli_preinstalled": None,
    }

    def test_single_instance_tunnels_without_a_menu_prompt(self):
        rendered = render(dict(self.WINDOWS_OVERRIDES, count=1))
        access_script = rendered["access_instance.j2"]
        assert "AWS-StartPortForwardingSession" in access_script
        assert '"portNumber":["3389"]' in access_script
        assert "menuchoice = 1" in access_script
        assert "Select an instance to access using Remote Desktop" not in access_script

    def test_family_prompts_for_a_target_before_tunneling(self):
        rendered = render(dict(self.WINDOWS_OVERRIDES, count=3))
        access_script = rendered["access_instance.j2"]
        assert "Select an instance to access using Remote Desktop" in access_script
        assert "rdp_instance_id = ec2_InstanceId.split(',')[rdp_target_index]" in access_script

    def test_no_inbound_rdp_command_left_over(self):
        # There must be no leftover manual "here is the IP, RDP to it
        # yourself" flow -- the tunnel command is what actually runs.
        rendered = render(dict(self.WINDOWS_OVERRIDES, count=1))
        access_script = rendered["access_instance.j2"]
        assert "'aws', 'ssm', 'start-session'," in access_script
        assert "rdp_local_port" in access_script

    def test_rdp_tunnel_survives_ctrl_c(self):
        # Ctrl-C is the *documented* way to close this tunnel ("Press
        # Ctrl+C to close the tunnel when finished.") -- it must never
        # produce a raw traceback.
        rendered = render(dict(self.WINDOWS_OVERRIDES, count=1))
        access_script = rendered["access_instance.j2"].splitlines()
        TestAccessInstanceHardening._assert_wrapped_in_try_except_keyboard_interrupt(access_script, "'aws', 'ssm', 'start-session',")


class TestInstanceUserdataSsmAgentInstall:
    """RHEL/Rocky's standard AMIs don't preinstall the SSM Agent (unlike
    AL2023/Ubuntu/AlmaLinux/Windows -- verified against AWS's own docs),
    so access_instance.py's SSM Session Manager connection would silently
    never register on those four base_os values without this cloud-init
    install step.
    """

    def test_rhel_and_rocky_get_the_install_step(self):
        for base_os in ("rhel9", "rhel10", "rocky9", "rocky10"):
            rendered = render({"base_os": base_os})["instance_userdata.j2"]
            assert "amazon-ssm-agent.rpm" in rendered
            assert "systemctl enable --now amazon-ssm-agent" in rendered

    def test_other_base_os_values_do_not_get_it(self):
        rendered = render({"base_os": "al2023"})["instance_userdata.j2"]
        assert "amazon-ssm-agent" not in rendered

    def test_arm64_uses_the_arm64_package(self):
        rendered = render({"base_os": "rocky9", "architecture": "arm64"})["instance_userdata.j2"]
        assert "linux_arm64/amazon-ssm-agent.rpm" in rendered
        assert "linux_amd64" not in rendered

    def test_x86_64_uses_the_amd64_package(self):
        rendered = render({"base_os": "rocky9", "architecture": "x86_64"})["instance_userdata.j2"]
        assert "linux_amd64/amazon-ssm-agent.rpm" in rendered


class TestInstanceUserdataAwsCliInstall:
    """AWS CLI is baked in as a toolkit-required prelogin step (same
    reasoning as the SSM Agent above) so it's guaranteed present before any
    user prelogin/postboot script runs, regardless of whether the base_os's
    AMI ships it preinstalled.
    """

    def test_installed_when_not_preinstalled(self):
        rendered = render({"awscli_preinstalled": False, "package_manager": "yum"})["instance_userdata.j2"]
        assert "awscli-exe-linux-x86_64.zip" in rendered
        assert "/tmp/aws/install" in rendered  # nosec B108 - asserting real generated cloud-init content, not a mock path

    def test_skipped_when_preinstalled(self):
        rendered = render({"awscli_preinstalled": True})["instance_userdata.j2"]
        assert "awscli-exe-linux" not in rendered

    def test_arm64_uses_the_aarch64_package(self):
        rendered = render({"awscli_preinstalled": False, "package_manager": "yum", "architecture": "arm64"})["instance_userdata.j2"]
        assert "awscli-exe-linux-aarch64.zip" in rendered
        assert "awscli-exe-linux-x86_64.zip" not in rendered

    def test_apt_based_os_installs_unzip_via_apt_not_yum(self):
        rendered = render({"awscli_preinstalled": False, "package_manager": "apt"})["instance_userdata.j2"]
        assert "apt-get install -y unzip" in rendered
        assert "yum install -y unzip" not in rendered


class TestCustomUserScriptsRendering:
    """custom_user_scripts/ replaced the old templates/custom_user_script.j2
    symlink mechanism: --custom_user_scripts is a comma-separated list, each
    name resolved (by instance_builder.resolve_custom_user_scripts(), tested
    separately) to a prelogin script (embedded into instance_userdata.j2's
    cloud-config) and/or a postboot script (its own file, pushed and run via
    ssm_provision.<name>.sh's SSM send-command calls, same execution point
    build_instance.sh has always run at).
    """

    def test_prelogin_script_content_is_embedded_in_userdata(self):
        rendered = render({})["instance_userdata.j2"]
        assert "write_files:" in rendered
        assert "PRE-LOGIN custom user script" in rendered
        assert "/opt/ec2instancemaker/custom_user_prelogin_script.default.sh" in rendered

    def test_postboot_script_renders_as_its_own_file(self):
        rendered = render({})
        postboot = rendered["custom_user_postboot_script.dev01.default.sh"]
        assert "POST-BOOT custom user script" in postboot

    def test_ssm_provision_runs_the_postboot_script(self):
        rendered = render({})["ssm_provision.j2"]
        assert 'run_via_ssm "custom_user_postboot_script.dev01.default.sh"' in rendered

    def test_no_postboot_scripts_selected_means_ssm_provision_only_runs_build_instance(self):
        rendered = render({"custom_user_postboot_scripts": []})["ssm_provision.j2"]
        assert 'run_via_ssm "custom_user_postboot_script' not in rendered
        # build_instance.sh's own run_via_ssm call must still be there regardless.
        assert 'run_via_ssm "build_instance.dev01.sh"' in rendered

    def test_no_prelogin_scripts_selected_means_no_prelogin_write_files_entry(self):
        # write_files: itself may still appear (e.g. the CloudWatch Agent
        # config, unrelated to custom_user_scripts) -- this only asserts
        # the prelogin-script-specific entry is gone.
        rendered = render({"custom_user_prelogin_scripts": []})["instance_userdata.j2"]
        assert "custom_user_prelogin_script" not in rendered

    def test_no_prelogin_scripts_and_no_cloudwatch_means_no_write_files_at_all(self):
        rendered = render({"custom_user_prelogin_scripts": [], "enable_cloudwatch_logs": "false"})["instance_userdata.j2"]
        assert "write_files:" not in rendered


class TestCloudWatchAgentInstall:
    """The CloudWatch Agent's install method genuinely differs by OS family
    (verified against AWS's live docs, not recalled) -- AL2023/AmazonLinux2
    have it in their own yum repo, RHEL/Rocky/AlmaLinux need the "redhat"
    S3-hosted rpm, Ubuntu needs the "ubuntu" S3-hosted deb. Getting the
    wrong one silently means no logs ship on 8 of the 13 supported base_os
    values.
    """

    def test_al2023_installs_via_yum_repo(self):
        rendered = render({"base_os": "al2023"})["instance_userdata.j2"]
        assert "yum install -y amazon-cloudwatch-agent" in rendered
        assert "amazoncloudwatch-agent.s3.amazonaws.com" not in rendered

    def test_alinux2_installs_via_yum_repo(self):
        rendered = render({"base_os": "alinux2"})["instance_userdata.j2"]
        assert "yum install -y amazon-cloudwatch-agent" in rendered
        assert "amazoncloudwatch-agent.s3.amazonaws.com" not in rendered

    def test_rhel_installs_via_redhat_rpm(self):
        rendered = render({"base_os": "rhel9", "package_manager": "yum", "architecture": "x86_64"})["instance_userdata.j2"]
        assert "amazoncloudwatch-agent.s3.amazonaws.com/redhat/amd64/latest/amazon-cloudwatch-agent.rpm" in rendered
        assert "rpm -U /tmp/amazon-cloudwatch-agent.rpm" in rendered
        assert "yum install -y amazon-cloudwatch-agent" not in rendered

    def test_rhel_arm64_uses_arm64_rpm(self):
        rendered = render({"base_os": "rocky9", "package_manager": "yum", "architecture": "arm64"})["instance_userdata.j2"]
        assert "amazoncloudwatch-agent.s3.amazonaws.com/redhat/arm64/latest/amazon-cloudwatch-agent.rpm" in rendered

    def test_ubuntu_installs_via_ubuntu_deb(self):
        rendered = render({"base_os": "ubuntu2404", "package_manager": "apt", "architecture": "x86_64"})["instance_userdata.j2"]
        assert "amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb" in rendered
        assert "dpkg -i -E -G /tmp/amazon-cloudwatch-agent.deb" in rendered

    def test_agent_config_applied_after_install(self):
        rendered = render({"base_os": "al2023"})["instance_userdata.j2"]
        install_idx = rendered.index("yum install -y amazon-cloudwatch-agent")
        apply_idx = rendered.index("amazon-cloudwatch-agent-ctl -a fetch-config")
        assert install_idx < apply_idx

    def test_disabled_means_no_cloudwatch_content_at_all(self):
        rendered = render({"enable_cloudwatch_logs": "false"})["instance_userdata.j2"]
        assert "amazon-cloudwatch-agent" not in rendered
        assert "cloudwatch-agent-ctl" not in rendered

    def test_config_uses_the_right_log_group(self):
        rendered = render({"cloudwatch_log_group": "/ec2instancemaker/dev01"})["instance_userdata.j2"]
        assert '"log_group_name": "/ec2instancemaker/dev01"' in rendered

    def test_config_uses_messages_on_yum_and_syslog_on_apt(self):
        yum_rendered = render({"package_manager": "yum"})["instance_userdata.j2"]
        assert "/var/log/messages" in yum_rendered
        assert "/var/log/syslog" not in yum_rendered

        apt_rendered = render({"package_manager": "apt", "base_os": "ubuntu2404", "ec2_user": "ubuntu", "ec2_user_home": "/home/ubuntu"})["instance_userdata.j2"]
        assert "/var/log/syslog" in apt_rendered
        assert "/var/log/messages" not in apt_rendered


class TestCloudWatchLogsTeardown:
    """kill-instance.<name>.sh's CloudWatch Logs group handling: delete by
    default, preserve only when --preserve_cloudwatch_logs=true, and don't
    even mention it if logging was never enabled for this instance.
    """

    def test_deletes_log_group_by_default(self):
        rendered = render({})["kill_instance.j2"]
        assert "aws --region us-east-1 logs delete-log-group --log-group-name $CLOUDWATCH_LOG_GROUP" in rendered
        assert "Preserved CloudWatch Logs group" not in rendered

    def test_preserves_log_group_when_requested(self):
        rendered = render({"preserve_cloudwatch_logs": "true"})["kill_instance.j2"]
        assert "Preserved CloudWatch Logs group" in rendered
        assert "delete-log-group" not in rendered

    def test_disabled_logging_means_no_teardown_logic_at_all(self):
        rendered = render({"enable_cloudwatch_logs": "false"})["kill_instance.j2"]
        assert "CloudWatch Logs group" not in rendered
