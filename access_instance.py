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

# Import some external lists and functions.
# Source: aux_data.py

from aux_data import refer_to_docs_and_quit

# Parse input from the command line.

parser = argparse.ArgumentParser(description='access_instance.py: Provide quick SSH access to EC2 instances')

# Configure arguments for the required variables.

parser.add_argument('--instance_name', '-N', help='name of the EC2 instance', required=True)
parser.add_argument('--menu_index', '-m', type=int, help='menu index of the EC2 instance', required=False, default=0)

args = parser.parse_args()
instance_name = args.instance_name
menu_index = args.menu_index

# Perform error checking for the command line arguments.
# If successful, execute the custom SSH access script for this instance family.

if os.path.exists('instance_data/' + instance_name + '/access_instance.' + instance_name + '.py'):
    if menu_index == 0:
        cmd_string = 'cd instance_data/' + instance_name + '/ &&' + 'python3 access_instance.' + instance_name + '.py'
    else:
        cmd_string = 'cd instance_data/' + instance_name + '/ &&' + 'python3 access_instance.' + instance_name + '.py' + ' --menu_index=' + str(menu_index)
    try:
        subprocess.run(cmd_string, shell=True)
    except KeyboardInterrupt:
        print('')
        print('No selection detected!')
        print('Exiting...')
        sys.exit(1)
else:
    error_msg='instance "' + instance_name + '" does not appear to exist!'
    refer_to_docs_and_quit(error_msg)
    sys.exit(1)
