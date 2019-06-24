################################################################################
# Name:         linux-ec2-setup.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 14, 2019
# Last Changed: June 21, 2019
# Purpose:      Setup Ec2InstanceMaker to spawn new instances from an instance
#		created by Ec2InstanceMaker
################################################################################

#!/bin/bash

# Define the Terraform version to deploy.
# https://www.terraform.io/downloads.html

TERRAFORM_VERSION=0.12.2

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

cd $SCRATCH_DIR
wget https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip
unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip
sudo cp terraform /usr/local/bin

exit 0

# Install the Ec2InstanceMaker runtime environment.

if [[ `cat /etc/os-release | grep ID_LIKE | grep -c centos` -gt 1 ]]
then
	sudo yum install -y autoconf automake gcc git jq libtool python3 python3-devel python3-pip 
elif [[ `cat /etc/os-release | grep ID_LIKE | grep -c debian` -gt 1 ]]
then
	sudo apt-get -y install autoconf automake gcc git jq libtool python3 python3-dev python3-pip 
else
	echo "*** ERROR ***"
	echo "Unsupported Linux version!!!"
	echo "Aborting..."
	exit 1
fi
cd $SRC_DIR
sudo pip3 install -r requirements.txt

# Cleanup and exit.

rm -rf $SCRATCH_DIR
echo "Finished setting up Ec2InstanceMaker."
echo "Exiting..."
exit 0
