################################################################################
# Name:		dockerfile
# Author:	Chris Mitchell <chris@ginkgobioworks.com>
# Modified By:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	July 29, 2019
# Last Changed:	July 29, 2019
# Purpose:	Run Ec2InstanceMaker from a Docker container
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

# Build the container from the official Docker Python-3.7 image.

FROM python:3.7-alpine

# Set some critical environment variables.

ENV APP_HOME=/Ec2InstanceMaker PATH=$APP_HOME:${PATH}

# Create the Ec2InstanceMaker source tree.

RUN mkdir -p $APP_HOME
WORKDIR $APP_HOME

# Copy the local Ec2InstanceMaker repository to the Docker container.

COPY . $APP_HOME
COPY templates $APP_HOME/templates

# Install the packages and Python libraries required by Ec2InstanceMaker.

RUN apk update -q && apk add \
  autoconf \
  automake \
  g++ \
  gcc \
  git \
  jq \
  libc-dev \
  libffi-dev \
  libtool \
  linux-headers \
  make \
  openssl-dev
RUN pip3 install -r requirements.txt

# Install Terraform.

ARG TERRAFORM_VERSION=0.12.3
RUN wget https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip
RUN unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip
RUN cp terraform /usr/local/bin && rm terraform_*
RUN chmod 0755 /usr/local/bin/terraform

# Set the default entrypoint.
# See above for guidance on interacting with Ec2InstanceMaker interactively.
# Alternatively, override ENTRYPOINT to run the companion scripts to access
# the instance(s) over SSH or to delete them.

ENTRYPOINT ["make-instance.py"]
