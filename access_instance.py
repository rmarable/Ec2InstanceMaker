#!/usr/bin/env python3
#
################################################################################
# Name:         access_instance.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 3, 2019
# Last Changed: June 22, 2019
# Purpose:	Top-level script to SSH into Ec2InstanceMaker-built instances
################################################################################

# Load some required Python libraries

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from typing import NoReturn

# Import some external lists and functions.
# Source: aux_data.py
from aux_data import refer_to_docs_and_quit
from instance_builder import validate_instance_name_format

# Type alias used throughout this module's signatures -- same duplicated
# convention as instance_builder.py/aux_data.py/manage_instance.py (see the
# comment there for why they're not shared via import).
QuitFn = Callable[[str], NoReturn]


# Function: build_access_command()
# Purpose: build the argv for the per-instance generated
# access_instance.<name>.py script -- --menu_index is only appended when
# non-default, matching the original inline code's behavior exactly.


def build_access_command(instance_name: str, menu_index: int) -> list[str]:
    cmd = ["python3", "access_instance." + instance_name + ".py"]
    if menu_index != 0:
        cmd.append("--menu_index=" + str(menu_index))
    return cmd


# Function: dispatch_to_instance_access_script()
# Purpose: run the per-instance generated access_instance.<name>.py script
# (template_engine.py rendered it into instance_data/<name>/ from
# templates/access_instance.j2) from that directory, or quit if this
# instance_name was never built here. Returns the subprocess exit code.
#
# Ctrl-C during an active SSM session (not just an unanswered menu prompt)
# lands here too -- both this process and the child script are in the same
# terminal foreground process group and receive the same SIGINT, so this
# fires whether or not a selection was ever made. Keep the message generic
# rather than assuming which case it was.


def dispatch_to_instance_access_script(
    instance_name: str,
    menu_index: int,
    refer_to_docs_and_quit: QuitFn,
    run_access_script: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> int:
    instance_dir = os.path.join("instance_data", instance_name)
    access_script_path = os.path.join(instance_dir, "access_instance." + instance_name + ".py")
    if not os.path.exists(access_script_path):
        refer_to_docs_and_quit('instance "' + instance_name + '" does not appear to exist!')
    cmd = build_access_command(instance_name, menu_index)
    try:
        return run_access_script(cmd, cwd=instance_dir).returncode
    except KeyboardInterrupt:
        print("")
        print("Interrupted.")
        print("Exiting...")
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="access_instance.py: Provide quick access to EC2 instances via SSM Session Manager")
    parser.add_argument("--instance_name", "-N", help="name of the EC2 instance", required=True)
    parser.add_argument("--menu_index", "-m", type=int, help="menu index of the EC2 instance", required=False, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> NoReturn:
    args = parse_args(argv)
    instance_name: str = args.instance_name
    menu_index: int = args.menu_index

    validate_instance_name_format(instance_name, refer_to_docs_and_quit)

    sys.exit(dispatch_to_instance_access_script(instance_name, menu_index, refer_to_docs_and_quit))


if __name__ == "__main__":
    main()
