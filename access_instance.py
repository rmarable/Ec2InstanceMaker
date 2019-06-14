#!/usr/bin/env python3
#
################################################################################
# Name:         access_instance.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 3, 2019
# Last Changed: June 14, 2019
# Purpose:	Top-level script to SSH into Ec2InstanceMaker-built instances
################################################################################

# Load some required Python libraries

import argparse
import os
import subprocess
import sys

# Import some external lists and functions.
# Source: aux_data.py

from aux_data import refer_to_docs_and_quit

# Parse input from the command line.

parser = argparse.ArgumentParser(description='access_instance.py: Provide quick SSH access to EC2 instances')

# Configure arguments for the required variables.

parser.add_argument('--instance_name', '-N', help='name of the EC2 instance', required=True)

args = parser.parse_args()
instance_name = args.instance_name

# Perform error checking for the command line arguments.
# If successful, execute the custom SSH access script for this instance family.

if os.path.exists('instance_data/' + instance_name + '/access_instance.' + instance_name + '.py'):
    cmd_string = 'cd instance_data/' + instance_name + '/ &&' + 'python3 access_instance.' + instance_name + '.py'
    subprocess.run(cmd_string, shell=True)
else:
    error_msg='instance "' + instance_name + '" does not appear to exist!'
    refer_to_docs_and_quit(error_msg)
    sys.exit(1)
