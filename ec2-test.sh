################################################################################
# Name:         ec2-test.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 14, 2019
# Last Changed: June 15, 2019
# Purpose:      Test Ec2InstanceMaker on an Ec2InstanceMaker-created instance
################################################################################

#!/bin/bash

SCRATCH_DIR=~/src/_Ec2InstanceMaker
TERRAFORM_VERSION=0.12.2

mkdir -p $SCRATCH_DIR
cd $SCRATCH_DIR
wget https://releases.hashicorp.com/terraform/$TERRAFORM_VERSION/terraform_${TERRAFORM_VERSION}_linux_amd64.zip
unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip
sudo cp terraform /usr/local/bin
sudo yum install -y python3
git clone https://rmarable:ebde37d45a8c6a72b48d5e13a0989c9d484d5ab3@github.com/rmarable/Ec2InstanceMaker.git 
cd Ec2InstanceMaker
python3 make-instance.py -N testenv01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a 
python3 access_instance.py -N testenv01
