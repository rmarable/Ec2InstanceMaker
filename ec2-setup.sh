################################################################################
# Name:         ec2-setup.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   June 14, 2019
# Last Changed: June 15, 2019
# Purpose:      Setup Ec2InstanceMaker on an Ec2InstanceMaker-created instance
################################################################################

#!/bin/bash

GIT_ACCESS=rmarable:ebde37d45a8c6a72b48d5e13a0989c9d484d5ab3
SRC=~/src
TERRAFORM_VERSION=0.12.2

mkdir $SRC && cd $SRC

git clone https://${GIT_ACCESS}@github.com/rmarable/Ec2InstanceMaker.git 

cd Ec2InstanceMaker
sudo yum install -y python3 python3-devel python3-pip jq autoconf automake libtool gcc git
sudo pip3 install -r requirements.txt

wget https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip
unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip
sudo cp terraform /usr/local/bin

if [ $1 == "test" ]
then
	python3 make-instance.py -N tester01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a 
	python3 access_instance.py -N tester01
else
	echo "Finished setting up Ec2InstanceMaker."
	echo "Exiting..."
	exit 0
fi
