################################################################################
# Name:		dockerfile
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	August 8, 2019
# Last Changed:	August 8, 2019
# Purpose:	Run Ec2InstanceMaker from an Amazon Linux Docker container
################################################################################
#
# Usage:
#
# - Install Docker by visiting: https://docs.docker.com/install/.
# - Clone the Ec2InstanceMaker repository into $SRC_DIR.
# - Build the container and launch Ec2InstanceMaker interactively.
#   $ mkdir -p $SRC_DIR
#   $ cd $SRC_DIR
#   $ git clone https://github.com/rmarable/Ec2InstanceMaker.git
#   $ cd $SRC_DIR/Ec2InstanceMaker
#   $ docker build -t ec2instancemaker
#   $ docker run -it --entrypoint=/bin/bash ec2instancemaker:latest -i
#   # pwd
#   /Ec2InstanceMaker
#   # ./make-instance.py -h
#
################################################################################
#
# If needed, run "aws configure" on the container before spawning instances.
# Since the Docker container needs AWS credentials here, you must choose one
# of the options below or make-instance.py WILL NOT work:
# - Paste "aws_access_key_id" and "aws_secret_access_key" where indicated.
# - Use environment variables per the guidance included in the AWS public 
#   documentation (this is more secure and is the recommended method):
#   https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html
#
#ENV AWS_ACCESS_KEY_ID=[aws_access_key_id]
#ENV AWS_SECRET_ACCESS_KEY=[aws_secret_access_key]

################################################################################
################      Do *NOT* change anything below this comment!    ##########
################################################################################

# Build the container from the official Amazon Linux Docker image.

FROM amazonlinux:latest

# Set the PATH and APP_HOME environment variables.

ENV APP_HOME=/Ec2InstanceMaker PATH=$APP_HOME:${PATH}

# Create the Ec2InstanceMaker source tree.

RUN mkdir -p $APP_HOME
WORKDIR $APP_HOME

# Copy the local Ec2InstanceMaker repository to the Docker container.

COPY . $APP_HOME
COPY templates $APP_HOME/templates

# Install the packages and Python libraries required by Ec2InstanceMaker.

RUN yum update -y
RUN yum install -y \
  autoconf \
  automake \
  g++ \
  gcc \
  git \
  jq \
  libc-devel \
  libffi-devel \
  libtool \
  linux-headers \
  make \
  openssh \
  openssl-devel \
  python3 \
  python3-devel \
  python3-pip \
  wget \
  zip

RUN pip3 install -r requirements.txt

# Install Terraform.

ARG TERRAFORM_VERSION=0.12.3
RUN wget https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip
RUN unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip
RUN cp terraform /usr/local/bin && rm terraform_*
RUN chmod 0755 /usr/local/bin/terraform

# To use Bash interactively (which is recommended), uncomment ENTRYPOINT above
# and build the container as follows:
#
# $ docker build -t ec2instancemaker .
# $ docker run -it --entrypoint=/bin/bash ec2instancemaker:latest -i
# # pwd
# /Ec2InstanceMaker
# ./make-instance.py -h

ENTRYPOINT ["make-instance.py"]
