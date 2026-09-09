################################################################################
# Name:		template_engine.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Purpose:	Render the per-instance Terraform/Ansible/shell templates using
# 		plain Jinja2 instead of shelling out to ansible-playbook.
#
# This replaces create_instance_terraform_templates.yml. It renders the same
# templates/*.j2 files, unmodified, to the same instance_data/<name>/
# destinations, and symlinks kill-instance.<name>.sh / build-ami.<name>.sh
# back into the repo root exactly as the old Ansible playbook did.
#
# One Ansible-only construct is used by templates/*.j2 and is shimmed
# below so the template files themselves did not need to change:
#   - the `bool` Jinja2 filter -- used (chained with the builtin `lower`
#     filter) to normalize "true"/"false" strings for Terraform output
#
# There used to be a second, lookup('pipe', <shell command>), which shelled
# out at render time. It is gone -- see the note in _make_environment().
################################################################################

import os
import shlex
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# (template filename in templates/, key in the derived-vars dict below that
# holds its rendered destination filename) -- matches the with_items list
# that used to live in create_instance_terraform_templates.yml.

TEMPLATE_MAP = [
    ("access_instance.j2", "access_instance_dest"),
    ("build_instance.j2", "build_instance_script"),
    ("instance_userdata.j2", "instance_userdata_script"),
    ("kill_instance.j2", "kill_instance_script"),
    ("provider_aws.j2", "provider_tf_dest"),
    ("DEFAULT_EC2_TEMPLATE.j2", "tf_ec2_instance_dest"),
    ("build_ami.j2", "build_ami_script"),
    ("ssm_provision.j2", "ssm_provision_script"),
]

# custom_user_prelogin_script.j2_<name> / custom_user_postboot_script.j2_<name>
# (in custom_user_scripts_dir, resolved+validated by
# instance_builder.resolve_custom_user_scripts() before this module ever
# sees them) aren't in TEMPLATE_MAP above: there can be zero or many of
# them per build (--custom_user_scripts is a comma-separated list), so
# they don't fit TEMPLATE_MAP's fixed one-template-to-one-dest-key shape.
# render_instance_templates() below renders them separately -- prelogin
# scripts' *content* gets embedded into instance_userdata.j2's cloud-config
# (see render_prelogin_scripts()), postboot scripts each render to their
# own file on disk, same as everything else.


def render_prelogin_scripts(names: list[str], env: Environment, context: dict[str, Any]) -> list[dict[str, str]]:
    """Render each selected custom_user_prelogin_script.j2_<name> and
    return their content for embedding into instance_userdata.j2's
    cloud-config write_files/runcmd -- these aren't written to
    instance_data_dir directly, since cloud-init needs the content inline
    in the user-data it receives, not a path on the operator's machine.
    """
    return [{"name": name, "content": env.get_template("custom_user_prelogin_script.j2_" + name).render(**context)} for name in names]


def render_postboot_scripts(names: list[str], env: Environment, context: dict[str, Any], instance_data_dir: str, instance_name: str) -> list[str]:
    """Render each selected custom_user_postboot_script.j2_<name> to its
    own file in instance_data_dir (same pattern as every other generated
    script) and return the list of destination filenames, for
    DEFAULT_EC2_TEMPLATE.j2 to loop over when pushing/running them via the
    SSH remote-exec provisioner.
    """
    filenames = []
    for name in names:
        filename = "custom_user_postboot_script." + instance_name + "." + name + ".sh"
        dest_path = os.path.join(instance_data_dir, filename)
        rendered = env.get_template("custom_user_postboot_script.j2_" + name).render(**context)
        with open(dest_path, "w") as fh:
            fh.write(rendered)
        os.chmod(dest_path, 0o755)  # nosec B103 - generated scripts must be executable, matches every other generated script
        filenames.append(filename)
    return filenames


def _bool_filter(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("yes", "true", "t", "1", "on")


def _shquote_filter(value: Any) -> str:
    # For interpolating free-text operator input (instance_owner_email/
    # instance_owner_department/project_id -- none of these are restricted
    # to a safe charset the way instance_name/instance_owner are,
    # deliberately, per instance_owner_department being explicit free text)
    # directly into a *generated shell script* (build_ami.j2, kill_instance.j2),
    # where the rendered text IS the shell source -- plain shlex.quote() is
    # correct here, no second escaping layer needed. For the Terraform
    # local-exec case, see tf_shquote below instead.
    return shlex.quote(str(value))


def _tf_shquote_filter(value: Any) -> str:
    # Same purpose as shquote above, but for interpolating into
    # DEFAULT_EC2_TEMPLATE.j2's local-exec `command` string specifically --
    # that string is itself an HCL double-quoted string literal, not raw
    # shell source, so the shell-quoted result needs a second escaping pass
    # so Terraform's own HCL parser doesn't mangle it before it ever reaches
    # /bin/sh -c:
    #   1. shlex.quote() makes the value safe for the shell to parse as a
    #      single token -- but its own escaping mechanism for an embedded
    #      single quote (') produces a literal double-quote character
    #      ('"'"'), which would otherwise break out of the *outer* HCL
    #      string.
    #   2. Escaping \ and " for HCL means Terraform's own parser un-escapes
    #      them back to the literal shlex.quote() output before ever
    #      constructing the string it passes to the shell -- so the shell
    #      still sees exactly what shlex.quote() intended.
    #   3. Escaping HCL's two template sequences -- "${" (interpolation)
    #      and "%{" (directives) -- via HCL's own "$${"/"%%{" escapes.
    #      This step is NOT optional and is easy to overlook: Terraform
    #      expands both sequences in the `command` string *after* this
    #      template is rendered and *before* the result is handed to
    #      /bin/sh -c. An expansion whose result contains a single quote
    #      therefore escapes the '...' wrapping shlex.quote() put around
    #      the value in step 1, turning operator free text into shell
    #      commands that run on the workstation performing the apply
    #      (proven during an adversarial review: a crafted
    #      --instance_owner_department plus a --project_id of
    #      "${self.tags.InstanceOwnerDepartment}" executed an arbitrary
    #      command during `terraform apply`). shlex.quote() cannot see
    #      this coming -- it quotes for the shell, and the injection
    #      happens one layer above the shell.
    shell_quoted = shlex.quote(str(value))
    escaped = shell_quoted.replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("${", "$${").replace("%{", "%%{")


def _hcl_escape_filter(value: Any) -> str:
    # For interpolating free-text operator input (instance_owner_email/
    # instance_owner_department/project_id -- none of these are
    # restricted to a safe charset the way instance_name/instance_owner
    # are) directly into a *plain HCL double-quoted string literal* (a
    # tag value, not a shell command -- see shquote/tf_shquote above for
    # that case, which these three fields also separately need wherever
    # they're interpolated into a local-exec `command` string). Escapes
    # the two characters HCL's own string-literal grammar treats
    # specially -- backslash and double-quote, which would otherwise let
    # a crafted value close the string early and inject arbitrary
    # HCL/Terraform config (proven live during an adversarial review:
    # an unescaped instance_owner_email broke out of a `tags = {...}`
    # block and added a whole extra resource) -- and HCL's "${"
    # interpolation sequence AND its "%{" directive sequence, both
    # neutralized via HCL's own "$${"/"%%{" escapes so a value can't
    # reach Terraform functions, another resource's attributes, or a
    # template directive either. Both sequences matter: a "%{ for ... }"
    # directive evaluates in a tag value just as readily as "${ ... }".
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("${", "$${").replace("%{", "%%{")


def _make_environment(local_workingdir: str) -> Environment:
    # autoescape is intentionally off: output is Terraform/shell/Python/JSON,
    # not HTML, and HTML-escaping quotes/angle-brackets would corrupt it.
    # The loader searches templates/ (toolkit-owned) and custom_user_scripts/
    # (user-owned custom_user_prelogin_script.j2_*/custom_user_postboot_script.j2_*)
    # -- filenames don't collide between the two, so a single FileSystemLoader
    # with both search paths is enough, no ChoiceLoader needed.
    env = Environment(
        loader=FileSystemLoader([os.path.join(local_workingdir, "templates"), os.path.join(local_workingdir, "custom_user_scripts")]),
        trim_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )  # nosec B701
    # NOTE: there is deliberately no `lookup` global here any more. It used
    # to replicate Ansible's lookup('pipe', ...) via
    # subprocess.check_output(arg, shell=True), and every one of its six
    # call sites passed the same literal `date "+%B %-d, %Y"` to stamp a
    # build date into a comment header. That made arbitrary shell execution
    # reachable at *render* time -- before Terraform runs and before the
    # CTRL-C window -- from any Jinja template on the loader path, and the
    # loader path includes custom_user_scripts/, which is documented as the
    # user-owned drop-in directory. A template someone shared could run
    # commands on the operator's workstation just by being rendered. The
    # date now comes from the DEPLOYMENT_DATE context variable
    # (time.strftime("%B %-d, %Y"), byte-identical output), so nothing in
    # the render path shells out at all.
    env.filters["bool"] = _bool_filter
    env.filters["shquote"] = _shquote_filter
    env.filters["tf_shquote"] = _tf_shquote_filter
    env.filters["hcl_escape"] = _hcl_escape_filter
    return env


def _build_render_context(instance_parameters: dict[str, Any], local_workingdir: str, instance_data_dir: str) -> dict[str, Any]:
    context = dict(instance_parameters)
    instance_name = context["instance_name"]
    context.update(
        {
            "instance_data_dir": instance_data_dir,
            "ec2_user_src": context["ec2_user_home"] + "/src",
            "ssh_keypair_file": context["ec2_keypair"] + ".pem",
            "provider": "aws." + context["vpc_name"],
            "sns_arn": context["sns_topic_arn"],
            "access_instance_dest": "access_instance." + instance_name + ".py",
            "build_instance_script": "build_instance." + instance_name + ".sh",
            "instance_userdata_script": "instance_userdata." + instance_name + ".sh",
            "kill_instance_script": "kill_instance." + instance_name + ".sh",
            "provider_tf_dest": "provider_aws.tf",
            "tf_ec2_instance_dest": instance_name + ".tf",
            "build_ami_script": "build_ami." + instance_name + ".sh",
            "ssm_provision_script": "ssm_provision." + instance_name + ".sh",
        }
    )
    return context


def render_instance_templates(instance_parameters: dict[str, Any], local_workingdir: str, instance_data_dir: str) -> None:
    """Render the EC2 instance template set and symlink the kill/build-ami
    scripts back into the repo root.

    instance_data_dir must already exist and must be an absolute path (the
    generated scripts shell out with this path as their subprocess cwd, so a
    relative path would break once the operator's shell cwd changes).
    """
    context = _build_render_context(instance_parameters, local_workingdir, instance_data_dir)
    env = _make_environment(local_workingdir)

    # Render custom_user_scripts/ selections before the main loop -- both
    # instance_userdata.j2 (prelogin_custom_scripts) and
    # DEFAULT_EC2_TEMPLATE.j2 (postboot_script_filenames) below need these
    # in context.
    context["prelogin_custom_scripts"] = render_prelogin_scripts(context["custom_user_prelogin_scripts"], env, context)
    context["postboot_script_filenames"] = render_postboot_scripts(context["custom_user_postboot_scripts"], env, context, instance_data_dir, context["instance_name"])

    for src, dest_key in TEMPLATE_MAP:
        dest_path = os.path.join(instance_data_dir, context[dest_key])
        rendered = env.get_template(src).render(**context)
        with open(dest_path, "w") as fh:
            fh.write(rendered)
        os.chmod(dest_path, 0o755)  # nosec B103 - generated scripts must be executable; matches the old Ansible template `mode: 0755`

    instance_name = context["instance_name"]
    for script_key, link_prefix in (
        ("kill_instance_script", "kill-instance."),
        ("build_ami_script", "build-ami."),
    ):
        link_target = os.path.join(instance_data_dir, context[script_key])
        link_path = os.path.join(local_workingdir, link_prefix + instance_name + ".sh")
        if os.path.islink(link_path) or os.path.exists(link_path):
            os.remove(link_path)
        os.symlink(link_target, link_path)
