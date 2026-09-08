# EC2InstanceMaker - Example Use Cases

This document summarizes some use cases that this Open Source software project
was tested against.  These command line invocations can be applied in any AWS
environment by pasting the appropriate command line into a shell and substituting your username, email address, instance name, and other required parameters as needed.

## Single Ondemand Instance With Defaults

t2.micro instances with 8 GB unencrypted gp2 EBS root volumes.

### Amazon Linux 2023
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev01 --base_os=al2023`

### Amazon Linux 2:
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev01 --base_os=alinux2`

### AlmaLinux 9
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev11 --base_os=alma9`

### AlmaLinux 10
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev12 --base_os=alma10`

### Red Hat Enterprise Linux 9
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev04 --base_os=rhel9`

### Red Hat Enterprise Linux 10
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev05 --base_os=rhel10`

### Rocky Linux 9
Requires subscribing to the "Rocky Linux 9 (Official)" AWS Marketplace listing first -- see "Troubleshooting" below.
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev06 --base_os=rocky9`

### Rocky Linux 10
Requires subscribing to the "Rocky Linux 10 (Official)" AWS Marketplace listing first -- see "Troubleshooting" below.
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev07 --base_os=rocky10`

### Ubuntu 24.04 LTS
`$ ./make_instance.py -N dev02 -O rmarable -E rodney.marable@gmail.com -A us-east-1b --base_os=ubuntu2404`

### Ubuntu 26.04 LTS
`$ ./make_instance.py -N dev03 -O rmarable -E rodney.marable@gmail.com -A us-east-1b --base_os=ubuntu2604`

### Windows Server 2022
`$ ./make_instance.py -N dev09 -O rmarable -E rodney.marable@gmail.com -A us-east-1b -T t3a.micro --base_os=windows2022`

### Windows Server 2025
`$ ./make_instance.py -N dev10 -O rmarable -E rodney.marable@gmail.com -A us-east-1b -T t3a.micro --base_os=windows2025`

### AlmaLinux 9 on AWS Graviton (ARM64)
No separate architecture flag is needed -- picking a Graviton instance type (any `*g*`-family type, e.g. `m7g.large`, `c8g.xlarge`) automatically selects the matching ARM64 AMI.
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev13 --base_os=alma9 -T m7g.large`

## Single Ondemand Instance With Larger EBS Root Volume

t2.micro instances with larger unencrypted gp2 EBS root volumes.  A sampling of "df" output for some operating systems is provided to confirm the root volume was sized as configured on the command line.

## Spawning Child Instances from Ec2InstanceMaker-created Instances

Ec2InstanceMaker creates generic IAM roles, policies, and instance templates that are individualized as much as possible for each instance or instance family.  However, by default, Ec2InstanceMaker-spawned instances cannot spwan children.

`ExtendedEc2InstancePolicy.json` (found in the templates/ subdirectory) can be used to allow Ec2InstanceMaker-spawned instances to create children.

### Amazon Linux 2
Create the parent instance which will have permission to spawn children through the ExtendedEc2InstancePolicy JSON policy document that lives in the `templates` subdirectory:the `:

`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev01 --base_os=alinux2 --iam_json_policy=ExtendedEc2InstancePolicy.json`

Access the parent, clone the parent repository, setup the Ec2InstanceMaker environment, and launch a child instance:
```
$ ./access_instance.py -N dev01
Opening an SSM Session Manager connection to: dev01

Starting session with SessionId: rmarable-0123456789abcdef0

[ec2-user@ip-172-31-45-18 ~]$ cd src
[ec2-user@ip-172-31-45-18 ~]$ git clone https://github.com/rmarable/Ec2InstanceMaker
Cloning into 'Ec2InstanceMaker'...
remote: Enumerating objects: 231, done.
remote: Counting objects: 100% (231/231), done.
remote: Compressing objects: 100% (77/77), done.
remote: Total 231 (delta 156), reused 228 (delta 153), pack-reused 0
Receiving objects: 100% (231/231), 130.89 KiB | 14.54 MiB/s, done.
Resolving deltas: 100% (156/156), done.
[ec2-user@ip-172-31-45-18 ~]$ cd Ec2InstanceMaker/
[ec2-user@ip-172-31-45-18 ~]$ ./linux-ec2-setup.sh
...
<output snipped>
...
[ec2-user@ip-172-31-45-18 ~]$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev01 --base_os=alinux2
...
<output snipped>
...
================================================================================

Access the new alinux2 instance via SSM Session Manager:
./access_instance.py -N dev01

Delete the instance:
./kill-instance.dev01.sh

Build an AMI from the new instance:
./build-ami.dev01.sh

Exiting...
[ec2-user@ip-172-31-45-18 ~]$ ./access_instance.py -N dev01
Opening an SSM Session Manager connection to: dev01

Starting session with SessionId: rmarable-0fedcba9876543210

[ec2-user@ip-172-31-45-18 ~]$ exit
exit


Exiting session with sessionId: rmarable-0fedcba9876543210.

Reconnect to dev01 by running this command:

./access_instance.py -N dev01
```

## Using a Custom AMI:

DevOps teams may want to further control what AWS API calls that can be made by Ec2InceMaker-spawned instances, including the limiting or granting of the ability to create child instances with more granular permisisons, through the use of ccentralized IAM roles.

If you are encountering issues with "doing" things using these instances, please work with your DevOps team to create appropriate IAM role.

### Amazon Linux 2
`$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev01 --base_os=alinux2 --iam_role=CustomDevOpsIamRole`

```
$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev03 --base_os=alinux2 --ebs_root_volume_size=1000 --custom_ami=ami-00c8f252620d3a56e

[ec2-user@ip-172-31-44-153 ~]$ df -h
Filesystem      Size  Used Avail Use% Mounted on
devtmpfs        475M     0  475M   0% /dev
tmpfs           492M     0  492M   0% /dev/shm
tmpfs           492M  388K  492M   1% /run
tmpfs           492M     0  492M   0% /sys/fs/cgroup
/dev/xvda1     1000G  2.5G  998G   1% /
tmpfs            99M     0   99M   0% /run/user/1000
```

### Amazon Linux 2:
```
$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev03 --base_os=alinux2 --ebs_root_volume_size=785

[ec2-user@ip-172-31-45-192 ~]$ df -h
Filesystem      Size  Used Avail Use% Mounted on
devtmpfs        475M     0  475M   0% /dev
tmpfs           492M     0  492M   0% /dev/shm
tmpfs           492M  396K  492M   1% /run
tmpfs           492M     0  492M   0% /sys/fs/cgroup
/dev/xvda1      785G  2.2G  783G   1% /
tmpfs            99M     0   99M   0% /run/user/1000
```

## Single Ondemand Instance with Encrypted EBS Root Volume in a Non-Default VPC

Note: support for user-owned KMS keys will be provided in a future release.

### Amazon Linux 2:
```
$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N dev01 --ebs_encryption=true --preserve_ami=false
```

* Because `preserve_ami=false`, this AMI will **NOT** be preserved after the parent instance is terminated and will remain unavailable for launching new instances.  This is counterproductive for real-world use cases and thus is not a recommended best practice.  A more practical command line invocation for building new AMIs looks like this:

```
$ ./make_instance.py -A us-east-1a -N rmarable-foo-01 -O rmarable -E rodney.marable@gmail.com --vpc_name=vpc-prod --ebs_encryption=true --preserve_ami=true

```

The resulting `build_ami` script will be located in the top-level SRC tree and can now be used to create a new encrypted AMI:

```
$ ./build-ami.rmarable-foo-01.sh

Parsing the InstanceId of the AMI source...

##############################################################################
#                              *** WARNING ***                               #
#                 Preparing to shut down the source instance!                #
#    Please type CTRL-C within 5 seconds if this is *NOT* what you wanted!   #
##############################################################################

Shutting down the instance that will be used to build the new AMI...
This may take a few minutes so please be patient!

{
    "StoppingInstances": [
        {
            "CurrentState": {
                "Code": 64,
                "Name": "stopping"
            },
            "InstanceId": "i-0634e32e9d5ab3136",
            "PreviousState": {
                "Code": 16,
                "Name": "running"
            }
        }
    ]
}

Creating the new AMI...

Waiting for the new AMI to become available...

Waiting for the EBS snapshotting process to complete...

Tagging the new AMI...

Tagging the new EBS snapshot...

Restarting the source EC2 instance...

{
    "StartingInstances": [
        {
            "CurrentState": {
                "Code": 0,
                "Name": "pending"
            },
            "InstanceId": "i-0634e32e9d5ab3136",
            "PreviousState": {
                "Code": 80,
                "Name": "stopped"
            }
        }
    ]
}

Finished building: ami-01e6876676987e00e

To relaunch rmarable-foo-01 with this new AMI:
./kill-instance.rmarable-foo-01.sh
./make_instance.py -A us-east-1a -N rmarable-foo-01 -O rmarabro1 -E rodney.marable@sana.com --vpc_name=vpc-prod --ebs_encryption=true --preserve_ami=true --custom_ami=ami-01e6876676987e00e

Exiting...
```

## Using a custom prefix for all IAM entities

Building an EC2 instance using a custom prefix to name all IAM entities will only allow the user invoking the build to create, delete, or modify roles, policies, and instance profiles that include the aforementioned prefix.  This parameter defaults to "Ec2InstanceMaker":

```
$ ./make_instance.py -N dev01 -O rmarable -E rodney.marable@gmail.com -A us-west-2b --iam_name_prefix=MyEc2IamPrefix

Performing parameter validation...

Selected EC2 instance type: t2.micro

Selected base operating system: alinux2

** WARNING **
t2.micro does not support EBS optimization!
Disabling ebs_optimization for: dev01

Selected: ondemand (NOTE: spot instances are **MUCH** cheaper!)

Created EC2 keypair: dev01-48051518072019_us-west-2
Created EC2 instance role: MyEc2IamPrefix-role-dev01-48051518072019
Created EC2 instance profile: MyEc2IamPrefix-profile-dev01-48051518072019
Added: MyEc2IamPrefix-role-dev01-48051518072019 to MyEc2IamPrefix-profile-dev01-48051518072019

Saved dev01 build template: ./vars_files/dev01.yml

Generating templates for instance dev01...

<snipped>

$ ./kill-instance.dev01.sh

EC2 instance "dev01" is marked for termination.

################################################################################
################  Please type CTRL-C within 5 seconds to abort  ################
################################################################################

Destroying instance: dev01

aws_instance.dev01[0]: Refreshing state... [id=i-0bef75a01200a212f]
aws_instance.dev01[0]: Destroying... [id=i-0bef75a01200a212f]
aws_instance.dev01[0]: Still destroying... [id=i-0bef75a01200a212f, 10s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-0bef75a01200a212f, 20s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-0bef75a01200a212f, 30s elapsed]
aws_instance.dev01[0]: Destruction complete after 35s

Destroy complete! Resources: 1 destroyed.
Deleted EC2 keypair: dev01-48051518072019_us-west-2
Deleted SSH keypair file: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01/dev01-48051518072019_us-west-2.pem
Deleted directory: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01
Deleted CloudWatch Logs group: /ec2instancemaker/dev01

No AMI image tagged with dev01-48051518072019 was found.

Published instance termination message:
{
    "MessageId": "d6bb4831-d454-50b6-9754-1d9b129725ad"
}
Deleted SNS topic: arn:aws:sns:us-east-1:147724377207:Ec2_Instance_SNS_Alerts_dev01-48051518072019

Deleted IAM EC2 policy: MyEc2IamPrefix-policy-dev01-48051518072019
Deleted IAM EC2 instance profile: MyEc2IamPrefix-profile-dev01-48051518072019
Deleted IAM EC2 role: MyEc2IamPrefix-role-dev01-48051518072019

Deleted state files:
	./vars_files/dev01.yml
	./active_instances/dev01.serial

Removed symlinks:
	kill-instance.dev01.sh
	build-ami.dev01.sh

###############################################################################
    Finished deleting EC2 instance: dev01
###############################################################################

Exiting...
```

Future releases will provide DevOps teams with increased control over user permissions in these more controlled environments.

## Building a New AMI from a Fresh Instance

A standard AMI build script is provided with each new build.  To register a new AMI, please run the following command:

`$ ./build-ami.dev01.sh`

To build a new "golden" AMI or custom image, add your desired changes as a
`custom_user_scripts/custom_user_postboot_script.j2_<name>` (select it at
build time with `--custom_user_scripts <name>`) and they will be executed
as part of the instance deployment process.  See "Instance Customization"
in `README.md` and `custom_user_scripts/README.md` for the full
explanation.  Then, simply run the build-ami script as noted above.

Currently, Ec2InstanceMaker only supports one active AMI and EBS snapshot per
instance invocation.  If an existing image is detected, you **must** delete it
when prompted before the AMI build will continue.

Support for managing multiple AMI images associated with the same instance
build may be added in a future release.

## Launching Instances With Private IP Addresses

For environments that require enhanced security, Ec2InstanceMaker supports launching instances with only the private IP address.

$ ./make_instance.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N fn2187 --base_os=al2023 --public_ip=false

## Launching Instances In Another VPC

To launch instances in non-default VPCs, provide the name of the desired destination VPC using the `vpc_name` flag:

```
$ ./make_instance.py -A us-east-1a -N rmarable-foo-01 -O rmarable -E rodney.marable@gmail.com --vpc_name=vpc-dev
```

If a subnet does not exist for the Availability Zone that was selected, the script will return an error.  The default VPC will be used if an explicit value is nnot provided.

## Destroying an Instance

A custom "kill" script is generated when the instance is created:

```
$ ./kill-instance.dev01.sh

EC2 instance "dev01" is marked for termination.

################################################################################
################  Please type CTRL-C within 5 seconds to abort  ################
################################################################################

Destroying instance: dev01

aws_instance.dev01[0]: Refreshing state... [id=i-02dbe6023c8a10040]
aws_instance.dev01[0]: Destroying... [id=i-02dbe6023c8a10040]
aws_instance.dev01[0]: Still destroying... [id=i-02dbe6023c8a10040, 10s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-02dbe6023c8a10040, 20s elapsed]
aws_instance.dev01[0]: Still destroying... [id=i-02dbe6023c8a10040, 30s elapsed]
aws_instance.dev01[0]: Destruction complete after 30s

Destroy complete! Resources: 1 destroyed.
Deleted EC2 keypair: dev01-17011225062019_us-east-1
Deleted SSH keypair file: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01/dev01-17011225062019_us-east-1.pem
Deleted directory: /Users/rmarable/src/public/Ec2InstanceMaker/instance_data/dev01
Deleted CloudWatch Logs group: /ec2instancemaker/dev01
Published instance termination message:
{
    "MessageId": "2110aa6a-d5b5-5b37-8d7f-ffe69d93ff5d"
}
Deleted SNS topic: arn:aws:sns:us-east-1:147724377207:Ec2_Instance_SNS_Alerts_dev01-17011225062019
Deleted file: ./vars_files/dev01.yml
Deleted file: ./active_instances/dev01.serial
Deleted file: kill-instance.dev01.sh

###############################################################################
##           Finished deleting EC2 instance: dev01
###############################################################################

Exiting...
```
