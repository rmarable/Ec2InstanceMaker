################################################################################
# Name:         linux-ec2-setup.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 14, 2019
# Last Changed: July 29, 2019
# Purpose:      Configure an instance spawned by Ec2InstanceMaker to create 
#		new instances using Ec2InstanceMaker
################################################################################

#!/bin/bash

# Define the Terraform version to deploy.
# https://www.terraform.io/downloads.html

TERRAFORM_VERSION=0.12.3

################################################################################
#           	No more user-configurable options exist below here!            #
################################################################################

# Define some critical shell variables.

SCRATCH_DIR=/tmp/_Ec2InstanceMaker
SRC_DIR=`pwd`

# Create a temporary scratch directory.

if [ ! -d $SCRATCH_DIR ]
then
	mkdir -p $SCRATCH_DIR
fi

# Install Terraform.

echo ""
echo "Installing Terraform..."
cd $SCRATCH_DIR
wget https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip
unzip -o terraform_${TERRAFORM_VERSION}_linux_amd64.zip
sudo cp terraform /usr/local/bin
sudo chmod 0755 /usr/local/bin/terraform

# Install the Ec2InstanceMaker runtime environment.

if [[ `cat /etc/os-release | grep ID_LIKE | grep -c centos` -gt 0 ]]
then
	sudo yum install -y autoconf automake gcc git jq libtool python3 python3-devel python3-pip 
elif [[ `cat /etc/os-release | grep ID_LIKE | grep -c debian` -gt 0 ]]
then
	sudo apt-get -y install autoconf automake gcc git jq libtool python3 python3-dev python3-pip 
else
	echo ""
	echo "*** ERROR ***"
	echo "Unsupported Linux version!!!"
	echo "Aborting..."
	exit 1
fi

# Install the Python libraries required by Ec2InstanceMaker.

echo ""
echo "Installing the Python libraries required by Ec2InstanceMaker..."
cd $SRC_DIR
sudo pip3 install -r requirements.txt

# Cleanup and exit.

rm -rf $SCRATCH_DIR
echo ""
echo "Finished setting up Ec2InstanceMaker!"
echo ""
echo "*****************"
echo "*** IMPORTANT ***"
echo "*****************"
echo "The default JSON policy document does *NOT* provide adequate permissions"
echo "for Ec2InstanceMzker-built instances to spwan children of their own."
echo ""
echo "Please set \"--iam_json_policy=ExtendedEc2InstancePolicy.json\" or work"
echo "with your DevOps team to construct a custom IAM role that provides the"
echo "appropriate permissions and include it by setting:"
echo "\"--iam_role=ROLE_NAME\""
echo ""
echo "Please refer to README and EXAMPLE_USE_CASES for additional guidance on"
echo "how to utilize this toolkit."
echo ""
echo "Exiting..."
exit 0
