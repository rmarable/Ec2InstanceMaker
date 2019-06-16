# INSTALL.md

## License and Disclaimer Information

Please refer to the LICENSE and DISCLAIMER.md documents included with this Open Source software for the specific terms and conditions that govern its use.

## Introduction

Ec2InstanceMaker is Open Source software that simplifies the automation
of creating, deleting, and administering cloud computing server resources
through an easy-to-use command line interface.  It can also be used as a 
teaching tool for those who wish to dive deep into cloud automation and to
learn more about the AWS ecosystem.

This toolkit is currently supported on local OSX and Linux environments and
EC2 instances spawned with the GenericEc2InstancePolicy.json template.  In
theory, it can also be used on Windows machines that have Python and an
appropriately configured Bash-Cygwin environment, but this method has not been
tested and will **not** be supported.

## Creating an Installation Environment on OSX

This section provides guidance for using this toolkit to launch new EC2
instances directly from OSX.  Please be forewarned that this method requires
installing external tools including Homebrew which may createe other unforeseen
problems with your local environment.

* Clone the Ec2InstanceMaker toolkit into your local ~/src directory:

```
$ mkdir ~/src
$ cd ~/src
$ git clone https://github.com/rmarable/Ec2InstanceMaker.git
```

* Install Homebrew (OSX users only):

```
$ /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"
```

* Use Homebrew to install some other critical applications (OSX users only):

```
$ brew install ansible autoconf automake gcc jq libtool make readline
```

* Install Python3 using the guidance provided here:

https://realpython.com/installing-python/

* Configure the AWS CLI according to the guidelines provided in the AWS public
documentation:

https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-started.html

* Install and activate a virtual Python environment using virtualenv or pyenv.
Please visit "https://docs.python-guide.org/dev/virtualenvs/" for more details
on Python virtual envirionments.  This is something everyone should be using!

pyenv is cleaner and preferred, but it doesn't provide a prompt that will
display the current Python version like virtualenv does without some
additional steps.  Please follow the installation guidelines provided
here: https://github.com/pyenv/pyenv#installation

Please be **very** careful or you may inadvertedly damage your local Python
environment:

```
$ brew install pyenv
$ brew install pyenv-virtualenv
$ pyenv version 3.7.2
$ pyenv virtualenv ec2instancemaker
$ pyenv activate ec2instancemaker
```

virtualenv should **not** be installed in the Ec2InstanceMaker source folder
to help keep your source tree clean and organized:

```
$ pip install virtualenv
$ virtualenv --version
16.4.3
$ mkdir -p ~/src/ec2instancemaker
$ virtualenv -p /usr/local/bin/python3.7 ~/src/ec2instancemaker
$ export VIRTUALENVWRAPPER_PYTHON=/usr/local/bin/python3.7
$ source ~/src/ec2instancemaker/bin/activate
```

* Install the required Python libraries into the Python virtual environment
using the included requirements.txt files:

```
$ cd ~/src/Ec2InstanceMaker
$ pip install -r requirements.txt
$ cd ~
```

* You are now ready to build instances.  Please consult README.md for more
detailed information on leveraging the scripts in this toolkit.

## Creating an Installation Environment on Linux (local or EC2)

The linux-ec2-setup.sh script can be used to set up the Ec2InstanceMaker
operating environment on Linux.  This will also work on EC2 instances
running CentOS, Amazon Linux, or Ubuntu.

After building a new instance, check out the repository from Github and run
the script:

$ mkdir -p ~/src && cd ~/src
$ git clone https://github.com/rmarable/Ec2InstanceMaker.git
$ cd src/Ec2InstanceMaker
$ .ec2-setup.sh ack on htw HARDCORE 

## About make-instance.py

To view all available options for make-instance.py:

```
$ cd ~/src/Ec2InstanceMaker
$ ./make-instance.py --help
```

## Example Use Cases

Please review these common use cases that this tool can help address.

### Example: Building a Single Linux Instance 

This example builds a single t2.micro development instance called "dev01"
running Amazon Linux 2 with a 20 GB gp2 EBS root volume using the default
IAM role which grants "general" S3 and EC2 permissions in us-east-1c.  The
instance is owned by the computational biology team ("compbio") and used by
project "XRV-243":

```
$ ./make-instance.py -A us-east-1c -N dev01 -O rmarable -E rodney.marable@gmail.com -B alinux2 --ebs_root_volume_size=20 --instance_owner_department=compbio --project_id="XRV-243"
```

Building the same instance using Spot (which can achieve up to 90% savings over
ondemand pricing):

```
$ ./make-instance.py -A us-east-1c -N dev01 -O rmarable -E rodney.marable@gmail.com -B alinux2 --ebs_root_volume_size=20 --instance_owner_department=compbio --project_id="XRV-243" --request_type=spot

### Example: Building a Single Windows Instance 

This example builds a single t2a.micro development instance called "dev001"
running Windows Server 2019 with a 30 GB gp2 EBS root volume using the default
IAM role which grants "general" S3 and EC2 permissions in us-west-2b.  The
instance is owned by the computational chemistry team ("compchem") and is not
used by any active project:

```
$ ./make-instance.py -A us-west-2b -N dev001 -O rmarable -E rodney.marable@gmail.com -B windows2019 --instance_owner_department=compchem
```

Again, building this same instance using Spot (which can achieve up to 90%
savings over ondemand pricing):

```
$ ./make-instance.py -A us-west-2b -N dev001 -O rmarable -E rodney.marable@gmail.com -B windows2019 --instance_owner_department=compchem --request_type=spot
```

### Example: Building Multiple Spot Instances

This example builds a family of five t3.micro test instances called "fam01"
running Ubuntu 18.04 LTS, each with a 10 GB gp2 EBS root volume using the
default IAM role in eu-central-1a.  These instances are owned by the HPC team:

```
$ ./make-instance.py -A eu-central-1a -N fam01 -B ubuntu1804 -O rmarable -E rodney.marable@gmail.com --ebs_root_volume_size=10 --instance_owner_department=hpc --request_type=spot --instance_type=t3.micro -C 5
```

Building the same instance family using Spot:

```
$ ./make-instance.py -A eu-central-1a -N fam01 -B ubuntu1804 -O rmarable -E rodney.marable@gmail.com --ebs_root_volume_size=10 --instance_owner_department=hpc --request_type=spot --instance_type=t3.micro -C 5 --request_type=spot
```

Building this instance family using Spot and Windows:

```
$ ./make-instance.py -A eu-central-1a -N fam01 -B windows2019 -O rmarable -E rodney.marable@gmail.com --ebs_root_volume_size=10 --instance_owner_department=hpc --request_type=spot --instance_type=t3.micro -C 5
```

### Example: Accessing an Instance 

Ec2InstanceMaker provides an easy mechanism to access individual instances or
specific members of a particular instance family over SSH.  Following every 
build, Ec2InstanceMaker will dump access and deletion information to the
console for user convenience.  For mulitple instance "families," a selection
menu for each instance will be provided.

To access the instance "dev01" created above:

```
$ ./access_instance.py -N dev01
The authenticity of host '54.211.214.242 (54.211.214.242)' can't be established.
ECDSA key fingerprint is SHA256:UVSoegQW2atQEAtUptey2lWCiqqAj0rnZzSp8R1+t8k.
Are you sure you want to continue connecting (yes/no)? yes
Warning: Permanently added '54.211.214.242' (ECDSA) to the list of known hosts.
Last login: Tue Jun 11 18:21:59 2019 from 72-21-196-64.amazon.com

       __|  __|_  )
       _|  (     /   Amazon Linux 2 AMI
      ___|\___|___|

https://aws.amazon.com/amazon-linux-2/
[ec2-user@ip-172-31-94-248 ~]$ exit
logout
Connection to 54.211.214.242 closed.
```

To access members of the instance family "fam01" created above:

```
$ ./access_instance.py -N fam01

+------+---------------+----------------+
| Item | Instance Name |   IP Address   |
+------+---------------+----------------+
|  1   |    fam01-0    | 35.159.20.166  |
|  2   |    fam01-1    | 18.194.109.142 |
|  3   |    fam01-2    | 18.196.63.105  |
|  4   |    fam01-3    |  3.121.85.13   |
|  5   |    fam01-4    |  52.59.85.37   |
+------+---------------+----------------+

Select an instance to access using SSH:
3

Opening an SSH connection to: fam01-2

The authenticity of host '18.196.63.105 (18.196.63.105)' can't be established.
ECDSA key fingerprint is SHA256:cRiWZtwLHbHRndGVHoihDaJP9Cge0xrrY1MIDe0RZhY.
Are you sure you want to continue connecting (yes/no)? yes
Warning: Permanently added '18.196.63.105' (ECDSA) to the list of known hosts.
Welcome to Ubuntu 18.04.2 LTS (GNU/Linux 4.15.0-1039-aws x86_64)

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/advantage

  System information as of Thu Jun 13 14:35:06 UTC 2019

  System load:  0.0               Processes:           89
  Usage of /:   12.5% of 9.63GB   Users logged in:     0
  Memory usage: 16%               IP address for ens5: 172.31.17.180
  Swap usage:   0%

 * Ubuntu's Kubernetes 1.14 distributions can bypass Docker and use containerd
   directly, see https://bit.ly/ubuntu-containerd or try it now with

     snap install microk8s --classic

66 packages can be updated.
28 updates are security updates.


Last login: Thu Jun 13 14:02:21 2019 from 54.239.6.177
ubuntu@ip-172-31-17-180:~$ exit
logout
Connection to 18.196.63.105 closed.

Reconnect to fam01-2 by running this command:

$ ./access_instance.py -N fam01
```

### Example: Deleting an Instance

Ec2InstanceMaker deploys a custom "kill-instance" script for each single instance or all members of an "instance family."

To delete the "dev01" instance created above:

```
$ ./kill-instance.dev01.sh

EC2 instance "dev01" is marked for termination.

################################################################################
################  Please type CTRL-C within 5 seconds to abort  ################
################################################################################

Destroying instance: dev01

aws_instance.dev01[0]: Refreshing state... [id=i-02f4f8fafb5f438ac]
aws_instance.dev01[0]: Destroying... [id=i-02f4f8fafb5f438ac]
aws_instance.dev01[0]: Still destroying... [id=i-02f4f8fafb5f438ac, 10s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-02f4f8fafb5f438ac, 20s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-02f4f8fafb5f438ac, 30s elapsed]
aws_instance.dev01[0]: Destruction complete after 30s

Destroy complete! Resources: 1 destroyed.
Deleted EC2 keypair: dev01-25201411062019_us-east-1
Deleted SSH keypair file: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01/dev01-25201411062019_us-east-1.pem
Deleted directory: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01
Deleted SNS topic: arn:aws:sns:us-east-1:147724377207:Ec2_Instance_SNS_Alerts_dev01-25201411062019
Deleted file: ./vars_files/dev01.yml
Deleted file: ./active_instances/dev01.serial
Deleted file: kill-instance.dev01.sh

###############################################################################
##           Finished deleting EC2 instance: dev01
###############################################################################

Exiting...
```

Deleting the instance family "fam01" looks exactly the same:

```
$ ./kill-instance.fam01.sh
[output snipped]
```

## Deactivating the virtual Python environment

When you are done working with Ec2InstanceMaker, disable the virtual Python
environment:

```
$ pyenv deactivate
```

If you are using virtualenv:

```
$ deactivate
```

Please consult README.md for additional information on how to use the
make-instance.py script.
 
## Reporting Bugs & Requesting New Features

Please report any bugs, issues, or otherwise unexpected behavior to Rodney Marable (rodney.marable@gmail.com) through the normal Github issue reporting channel for this project:

https://github.com/rmarable/Ec2InstanceMaker/issues

Pull requests providing additional functionality or bug fixes are always welcome:

https://github.com/rmarable/Ec2InstanceMaker/pulls
