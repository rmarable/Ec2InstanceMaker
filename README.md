# Ec2InstanceMaker - Simple Automated Framework for Building Cloud Servers 

Ec2InstanceMaker is an Open Source command line interface that makes it easy
to build, access, and destroy servers in the cloud.  It is also a useful
teaching tool for those who want to dive deep into cloud computing automation
and learn more about the AWS ecosystem.

## License Information

Please refer to the LICENSE document included with this Open Source software for the specific terms and conditions that govern its use.

## Disclaimer

By using this Open Source software:

* You accept all potential risks involved with your use of this Open Source software.

* You agree that the author shall have no responsibility or liability for any losses or damages incurred in conjunction with your use of this Open Source Software.

* You acknowledge that bugs may still be present, unexpected behavior might be observed, and some features may not be completely documented.

**This Open Source software is authored by Rodney Marable in his individual capacity and is neither endorsed nor supported by Amazon Web Services.**

You cannot create cases with AWS Technical Support or engage AWS support engineers in public forums if you have any questions, problems, or issues using this Open Source software.

```
"Play at your own risk!"
 -- Planet Patrol
```

## About Ec2InstanceMaker

Ec2InstanceMaker is an Open Source command line wrapper toolkit that eases the
automation, creation, and destruction of Amazon Elastic Compute Cloud (EC2)
instance fleets.  This tool is designed to enable anyone to leverage cloud
computing resources at scale without requiring deep infrastructure knowledge
or extensive experience with the AWS stack.

You can find more information about Amazon EC2 and EC2 Spot, by visiting:

https://aws.amazon.com/ec2/
https://aws.amazon.com/ec2/spot/

Ec2InstanceMaker can be run locally (OSX or Linux) or on an existing EC2
Linux instance.  Please refer to the "Installing Ec2InstanceMaker" section
for detailed guidance on how to properly configure your environment.

Running Ec2InstanceMaker locally on a Windows machine is **not** supported,
although it should work in theory with appropriately configured Python and
Bash-Cygwin environments.

## Ec2InstanceMaker Features

Ec2InstanceMaker provides the following features through its command line
interface:

* Installing instances on multiple operating systems:
  * Amazon Linux
  * Amazon Linux 2
  * CentOS 6
  * CentOS 7
  * Ubuntu 14.04LTS
  * Ubuntu 16.04LTS
  * Ubuntu 18.04LTS)
  * Windows Server 2019

Note: support for Redhat, OpenSuse, and SLES will be provided in future
releases.

* Error checking to ensure that only supported instance types for each operating
system can be created.

* Administrative control over the allowed EC2 instance types that can be deployed.

* Creation of new instances from custom AMIs.  Please see below for notes on enabling EBS root volume encryption.

* Multiple instances with identical configurations built at the same time a.k.a. "instance families."

* Command line designation of dev, test, stage, and prod operating levels. 

* EC2 Spot instances with a adjustable price buffer to help prevent instance
terminations caused by Spot market changes.

* Variable EBS root volume sizes up to 16 TB with optimization for supported
instance types.

* Provisioned IOPS, throughput optizimed, and general purpose (gp2) SSD EBS
volumes.

* Selective disabling of Intel HyperThreading.

* Custom EC2 security groups.

* Custom IAM instance profiles that can be created from user-supplied JSON policy documents or pre-existing IAM roles.

* Email notifications via SNS whenever an instance is created or deleted.

* Identification of the instance owner, email address, and department using
an easily extendable tagging framework.  The instance can also be associated
with a specific project identification tag.

* Additional instance customization hooks using EC2 instance userdata or
post-installation shell scripts.

* Custom scripting to automate deletion the instance or all members of the
instance family at once.

* Single-command SSH access to Linux instances.  If multiple instances were
created together, an easy-to-use menu is provided for the user to select the
instance of interest.

* For Windows instances, mapping of IP addresses to decrypted Administrator
passwords in an easy-to-parse table dumped to the console.  This information
can be pasted into RDC for easy access.

* Operability in Turbot (https://www.turbot.com) environments.

## Installation Instructions for the Impatient

These instructions are provided for the impatient and/or lazy.

* Install Homebrew (if using OSX).

* Install and configure the AWS CLI.

* Install Terraform.

* Install Ansible.

* Create and enable a virtual Python-3.7.x environment.

* Install the Python libraries in Ec2InstanceMaker/requirements.txt into the
freshly created virtual Python environment.

* Build away!

**It is strongly suggested that the reader carefully review the installation documentation (INSTALL.md) to avoid potentially costly and time-consuming mistakes.** 

## Using Ec2InstanceMaker

Ec2InstanceMaker is a collection of scripts and user-configurable templates.

* Scripts
  * make-instance.py
  * access-instance.py
  * kill-instance.$INSTANCE_NAME.py

Please see below for more details on how the scripts are used.

* Templates
  * **GenericEc2InstancePolicy.json.** This default JSON policy document allows
EC2 and S3 API calls and can be customized to permit fine-grained control of
the IAM EC2 instance policy.
  * **Ec2AdminInstancePolicy.json.**  This JSON policy document grants admin
rights to the IAM EC2 instance profile.  **Use this policy judiciously!**
  * **build_instance.j2.** This template permits the operator to leverage the
Terraform post-install hook to perform further configuration of EC2 instances.
  * **instance_userdata.j2.** This template permits the operator to leverage
EC2 instance userdata to perform additional configuration.  Please reference:

https://docs.aws.amazon.com/AWSEC2/latest/WindowsGuide/ec2-instance-metadata.html#instancedata-add-user-data

### Using make-instance.py

**make-instance.py** builds EC2 instances for a wide variety of use cases. 
To view currently available options:

```
$ ./make-instance.py -h
usage: make-instance.py [-h] --az AZ --instance_name INSTANCE_NAME
                        --instance_owner INSTANCE_OWNER --instance_owner_email
                        INSTANCE_OWNER_EMAIL
                        [--ansible_verbosity ANSIBLE_VERBOSITY]
                        [--base_os {alinux,alinux2,centos6,centos7,ubuntu1404,ubuntu1604,ubuntu1804,windows2019}]
                        [--count COUNT] [--custom_ami CUSTOM_AMI]
                        [--debug_mode {true,false}]
                        [--ebs_encryption {true,false}]
                        [--ebs_optimized {true,false}]
                        [--ebs_root_volume_iops EBS_ROOT_VOLUME_IOPS]
                        [--ebs_root_volume_size EBS_ROOT_VOLUME_SIZE]
                        [--ebs_root_volume_type {gp2,io1,st1}]
                        [--hyperthreading {true,false}]
                        [--iam_json_policy IAM_JSON_POLICY]
                        [--iam_role IAM_ROLE]
                        [--instance_owner_department {analytics,clinical,commercial,compbio,compchem,datasci,design,development,hpc,imaging,manufacturing,medical,modeling,operations,proteomics,robotics,qa,research,scicomp}]
                        [--request_type {ondemand,spot}]
                        [--instance_type INSTANCE_TYPE]
                        [--prod_level {dev,test,stage,prod}]
                        [--project_id PROJECT_ID]
                        [--security_group SECURITY_GROUP]
                        [--spot_buffer SPOT_BUFFER]
                        [--turbot_account TURBOT_ACCOUNT]

make-instance.py: Command-line interface to build EC2 instances

optional arguments:
  -h, --help            show this help message and exit
  --az AZ, -A AZ        AWS Availability Zone (REQUIRED)
  --instance_name INSTANCE_NAME, -N INSTANCE_NAME
                        name of the instance (REQUIRED)
  --instance_owner INSTANCE_OWNER, -O INSTANCE_OWNER
                        ActiveDirectory username of the instance
                        instance_owner (REQUIRED)
  --instance_owner_email INSTANCE_OWNER_EMAIL, -E INSTANCE_OWNER_EMAIL
                        Email address of the instance instance_owner
                        (REQUIRED)
  --ansible_verbosity ANSIBLE_VERBOSITY, -V ANSIBLE_VERBOSITY
                        Set the Ansible verbosity level (default = none)
  --base_os {alinux,alinux2,centos6,centos7,ubuntu1404,ubuntu1604,ubuntu1804,windows2019}, -B {alinux,alinux2,centos6,centos7,ubuntu1404,ubuntu1604,ubuntu1804,windows2019}
                        cluster base operating system (default = alinux2
                        a.k.a. Amazon Linux 2)
  --count COUNT, -C COUNT
                        number of EC2 instances to create (default = 1)
  --custom_ami CUSTOM_AMI
                        ami-id of a custom Amazon Machine Image (default =
                        UNDEFINED)
  --debug_mode {true,false}, -D {true,false}
                        Enable debug mode (default = false)
  --ebs_encryption {true,false}
                        enable EBS encryption (default = false)
  --ebs_optimized {true,false}
                        use optimized EBS volumes (default = yes)
  --ebs_root_volume_iops EBS_ROOT_VOLUME_IOPS
                        amount of provisioned IOPS for the EBS root volume
                        when ebs_root_volume_type=io1 (default = 0)
  --ebs_root_volume_size EBS_ROOT_VOLUME_SIZE
                        EBS volume size in GB (Linux default = 8, Windows
                        default = 30)
  --ebs_root_volume_type {gp2,io1,st1}
                        EBS volume type (default = gp2)
  --hyperthreading {true,false}, -H {true,false}
                        enable Intel Hyperthreading (default = true)
  --iam_json_policy IAM_JSON_POLICY, -J IAM_JSON_POLICY
                        Use a pre-existing JSON policy document in the
                        /templates subdirectory to set permissions for
                        iam_role (default = GenericEc2InstancePolicy.json
  --iam_role IAM_ROLE   Apply a pre-existing IAM role to the instance
  --instance_owner_department {analytics,clinical,commercial,compbio,compchem,datasci,design,development,hpc,imaging,manufacturing,medical,modeling,operations,proteomics,robotics,qa,research,scicomp}
                        Department of the instance_owner (default = hpc)
  --request_type {ondemand,spot}
                        choose between ondemand or spot instances (default =
                        ondemand)
  --instance_type INSTANCE_TYPE, -I INSTANCE_TYPE
                        EC2 instance type (default = t2.micro)
  --prod_level {dev,test,stage,prod}
                        Operating stage of the jumphost (default = dev)
  --project_id PROJECT_ID, -P PROJECT_ID
                        Project name or ID number (default = UNDEFINED)
  --security_group SECURITY_GROUP, -S SECURITY_GROUP
                        Primary security group for the EC2 instance (default =
                        generic_ec2_sg)
  --spot_buffer SPOT_BUFFER
                        pricing buffer to protect from Spot market
                        fluctuations: spot_price = spot_price +
                        spot_price*spot_buffer
  --turbot_account TURBOT_ACCOUNT, -T TURBOT_ACCOUNT
                        Turbot account ID (default = DISABLED)
```

### Building Instances

The minimum required argments are the Availability Zone, the instance owner's
username and email address, and the instance name.  All other values will fall
back to the defaults (t2.micro running Amazon Linux 2 with an 8 GB EBS gp2
unencrypted volume as the root device, using the supplied JSON policy document
to generate an IAM instance profile that provides EC2 and S3 access:

```
$ ./make-instance.py -A us-east-2a -N ec2-testinstance01 -O rmarable -E rodney.marable@gmail.com
```

To build a Windows instance using (mostly) default values:

```
./make-instance.py -N dev01 -O rmarable -E rmarable@amazon.com -A us-east-1b -I t3a.micro -B windows2019
```

If the user provides illegal parameter values or if any of the required AWS
resources fail to deploy, the script will loudly echo an appropriate error 
before aborting.  In the example below, the user attempts to build an EBS root
device that is larger than 16 TB:

```
$ ./make-instance.py -A us-east-2a -N ec2-testinstance01 -O rmarable -E rodney.marable@gmail.com --ebs_root_volume_size=16049311

Performing parameter validation...

Selected EC2 instance type: t2.micro
*** WARNING ***
t2.micro does not support EBS optimization!
Disabling ebs_optimization for: ec2-testinstance01

*** ERROR ***
Maximum allowed EBS volume size is 16 TB (16000 GB)!

Please resolve this error and retry the instance build.
Aborting...
```

### Accessing Instances

**access-instance.py** provides an easy mechanism for making SSH connections
to EC2 Linux instances.  When working with Windows EC2 instances, this script
will provide the decrypted Administrator password and IP address which can
then be pasted into a Remote Desktop Client within an easy-to-parse table.

For a single Linux instance:

```
$ ./access_instance.py -N dev01
Last login: Thu Jun 13 03:55:11 2019 from 72-21-196-65.amazon.com

       __|  __|_  )
       _|  (     /   Amazon Linux 2 AMI
      ___|\___|___|

https://aws.amazon.com/amazon-linux-2/
[ec2-user@ip-172-31-6-21 ~]$ exit
logout
Connection to 3.215.135.101 closed.

Reconnect to dev01 by running this command:

$ ./access_instance.py -N dev01
```

For a single Windows instance:

```
Access the new instance via Windows Remote Desktop with this information:

+---------------+---------------+----------------------------------+
| Instance Name |   IP Address  |      Adminstrator Password       |
+---------------+---------------+----------------------------------+
|     dev01     | 34.201.49.101 | K?Uf.@Roy-?D-W-?GPDW@4_BaT%=?EiD |
+---------------+---------------+----------------------------------+

Reprint this table:
$ ./access-instance.py -N dev01
```

When working with Linux instance families, `access_instance.py` provides a
menu where the operator can select the specific instance of interest:

```
$ ./access_instance.py -N fam01

+------+---------------+----------------+
| Item | Instance Name |   IP Address   |
+------+---------------+----------------+
|  1   |    fam01-0    | 18.204.42.129  |
|  2   |    fam01-1    |  34.237.2.35   |
|  3   |    fam01-2    |  3.83.36.203   |
|  4   |    fam01-3    |  3.80.159.180  |
|  5   |    fam01-4    | 18.207.181.36  |
|  6   |    fam01-5    |  3.214.144.61  |
|  7   |    fam01-6    | 18.232.133.187 |
|  8   |    fam01-7    | 18.209.237.152 |
|  9   |    fam01-8    | 18.209.213.133 |
|  10  |    fam01-9    |  3.80.163.177  |
+------+---------------+----------------+

Select an instance to access using SSH:
4

Opening an SSH connection to: fam01-3

The authenticity of host '3.80.159.180 (3.80.159.180)' can't be established.
ECDSA key fingerprint is SHA256:K0pbmmEPLcAhltTT5kqYcEUNJiamr+3J+gzjpvZsdoI.
Are you sure you want to continue connecting (yes/no)? yes
Warning: Permanently added '3.80.159.180' (ECDSA) to the list of known hosts.
Last login: Thu Jun 13 13:44:17 2019 from 72-21-196-66.amazon.com
[centos@ip-172-31-5-254 ~]$ exit
logout
Connection to 3.80.159.180 closed.

Reconnect to fam01-3 by running this command:

$ ./access_instance.py -N fam01
```

When working with multiple Windows instances, a menu displaying the decrypted
Administrator password and IP address of each instance is dumped to the 
console.  This data can be pasted into an RDC client to access the instance
of choice:

```
$ ./access_instance.py -N dev01
Access the new instance family members with Windows Remote Desktop:

+---------------+---------------+----------------------------------+
| Instance Name |   IP Address  |      Adminstrator Password       |
+---------------+---------------+----------------------------------+
|    dev01-0    | 3.210.201.142 | GTm.%A8%NARIe$&ax=sWojSRKxU.LIRB |
|    dev01-1    |  3.216.27.77  | uMP)sOvC2w7WXLi3h3L2%9VjI5Ovwo*c |
|    dev01-2    |  34.237.0.213 | 9T2cye$ytrEp9)niIlNv@@P;ORu8f&pu |
+---------------+---------------+----------------------------------+

Reprint this table:
$ ./access_instance -N dev01
```

### Destroying Instances

**kill-instance.$INSTANCE_NAME.sh.** is a personalized script designed to 
terminate specific EC2 instances or instance families.  It is created by
make-instance.py and will delete itself when the instances it was built with
are terminated.

To invoke:

```
$ ./kill-instance.dev01.sh

EC2 instance "dev01" is marked for termination.

################################################################################
################  Please type CTRL-C within 5 seconds to abort  ################
################################################################################

Destroying instance: dev01

aws_instance.dev01[0]: Refreshing state... [id=i-02021ce214305ce85]
aws_instance.dev01[0]: Destroying... [id=i-02021ce214305ce85]
aws_instance.dev01[0]: Still destroying... [id=i-02021ce214305ce85, 10s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-02021ce214305ce85, 20s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-02021ce214305ce85, 30s elapsed]
aws_instance.dev01[0]: Destruction complete after 31s

Destroy complete! Resources: 1 destroyed.
Deleted EC2 keypair: dev01-53522312062019_us-east-1
Deleted SSH keypair file: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01/dev01-53522312062019_us-east-1.pem
Deleted directory: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01
Deleted SNS topic: arn:aws:sns:us-east-1:147724377207:Ec2_Instance_SNS_Alerts_dev01-53522312062019
Deleted IAM EC2 policy: ec2-instance-policy-dev01-53522312062019
Deleted IAM EC2 instance profile: ec2-instance-profile-dev01-53522312062019
Deleted IAM role: ec2-instance-role-dev01-53522312062019
Deleted file: ./vars_files/dev01.yml
Deleted file: ./active_instances/dev01.serial
Deleted file: kill-instance.dev01.sh

###############################################################################
##           Finished deleting EC2 instance: dev01
###############################################################################

Exiting...
```

## Instance Customization

The basic post-installation shell script performs a systems package update and
inserts a 45-second keep-alive interval to prevent SSH logouts from affecting
any ongoing interactive instance activity.

The instance userdata template disables Intel HyperThreading if `--enable_hyperthreading=false` and modifies the root volume on CentOS 6 instances so it can be expanded up to 16 TB without user intervention.

Ec2InstanceMaker also permits additional customization by pasting your custom
code between these commented stanzas in `templates/instance_userdata.j2` or
`templates/build_instance.j2`:

```
###############################################################
## Starting point for user-added EC2 instance customizations ##
###############################################################

<paste your custom code here>

#############################################################
## Ending point for user-added EC2 instance customizations ##
#############################################################
```

Please note that additional customization of Windows instances can only be performed through the userdata template (templates/instance_userdata.j2).

Support for post-installation Python or PowerShell scripts may be provided in
subsequent releases.

## Troubleshooting

* Python version 3.6 or greater is required by this software.  Additionally,
you must install the required libraries in requirements.txt.  If any of these
components are missing, you will observe missing Python module errors:

```
ModuleNotFoundError: No module named boto3'
```

* Ansible and Terraform must also be present in order for the scripts in this
toolkit to operate as expected.  If either of these applications are missing
from the installing user's path, make-instance.py will return an "application
is missing" error:

```
$ ./make-instance.py -N dev01 -O rmarable -E rmarable@amazon.com -A us-east-1b -I t3.micro -C 3 --request_type=spot

*** ERROR ***
Terraform is missing! Please visit: https://www.terraform.io/downloads

Please resolve this error and retry the instance build.
Aborting...
```

* CentOS and Ubuntu require subscribing to the appropriate operating system
channel in the AWS Marketplace:

```
Error: Error launching source instance: OptInRequired: In order to use this AWS Marketplace product you need to accept terms and subscribe. To do so please visit https://aws.amazon.com/marketplace/pp?sku=a1rz1wghrw6x9gn14lyded00r
```

If you observe this error while attempting to build an EC2 instance, please
follow the guidelines provided in the message output to subscribe to the OS 
channel through the AWS Marketplace.

* Ec2InstanceMaker supports EBS encryption but does not yet provide a mechanism
for building and attaching multiple EBS volumes to an EC2 instance during the
installation process, which would subsequently be encrypted when
`--enable_ebs_encryption=true`.  This feature will be provided in a future
release.

* Encryption of root volumes during the instance installation process is not
supported by Terraform at this time -- please see the open issue on Github:

https://github.com/terraform-providers/terraform-provider-aws/issues/8624)

If your use case requires an encrypted EBS root volume, please refer to these
guidelines in the AWS public documentation:

https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSEncryption.html

Some current suggested workarounds:

  * **Enable EBS encryption by default for all instances in the account.**  Here is a link to the AWS public documentation that explains how to enable encryption by default:

https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSEncryption.html#encryption-by-default

Please be advised that this is a per-Region setting that can't be disabled on
a per-volume or snapshot basis.  Furthermore, you will not be able to launch
instances that do not support encryption within the region in question.  This
link on the AWS public documentation summarizes the instances that can be
launched within the region in question for that account:

https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSEncryption.html#EBSEncryption_supported_instances

  * **Use a custom AMI.**  Launch a normal EC2 instance with Ec2InstanceMaker.
Capture a snapshot of this instance, encrypt it, and then use the
`--custom_ami` flag to launch the instance with this encrypted snapshot
attached.
