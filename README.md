# Ec2InstanceMaker - Easy Automation for Building Cloud Servers

Ec2InstanceMaker is an Open Source command line interface that makes it easy
to build, access, and destroy servers in the cloud.  It is also a useful
teaching tool for those who want to dive deep into cloud computing and
security paradigms, learn more about infrastructure automation, and explore
the AWS ecosystem.

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
computing at scale without requiring deep infrastructure knowledge or
extensive experience with the AWS stack.

You can find more information about EC2 and EC2 Spot by visiting:

* https://aws.amazon.com/ec2/
* https://aws.amazon.com/ec2/spot/

Ec2InstanceMaker makes extensive use of the Amazon Web Services SDK for
Python (boto3), Terraform, and jq.  It can be launched from local OSX or
Linux environments, or from an existing EC2 instance.
You can find more information about these tools by visiting:

* Boto3: https://boto3.amazonaws.com/v1/documentation/api/latest/index.html
* Terraform: https://www.terraform.io/
* jq: https://stedolan.github.io/jq/

Ec2InstanceMaker also requires Python 3.12 (or greater) and a functional Bash
shell environment.  As noted above, this tool can be run locally on OSX or
Linux, on an existing EC2 Linux instance, or from an EC2 instance spawned
from a previous invocation of Ec2InstanceMaker.  Please
refer to the "Installing Ec2InstanceMaker" section for detailed guidance on
how to properly configure your environment.

Running Ec2InstanceMaker locally on a Windows machine is **not** supported,
although it should work in theory with appropriately configured Python and
Bash-Cygwin environments.

## Ec2InstanceMaker Features

Ec2InstanceMaker provides the following features through its command line
interface:

* Installation of multiple operating systems on EC2 instances:
  * Amazon Linux 2023
  * Amazon Linux 2
  * AlmaLinux 9
  * AlmaLinux 10
  * Red Hat Enterprise Linux 9
  * Red Hat Enterprise Linux 10
  * Rocky Linux 9
  * Rocky Linux 10
  * Ubuntu 24.04LTS
  * Ubuntu 26.04LTS
  * Windows Server 2019
  * Windows Server 2022
  * Windows Server 2025

    OpenSuse and SLES may be supported in future releases.

* Error checking to ensure that the selected operating system and EC2 instance
type are compatible.

* Automatic support for both x86_64 and AWS Graviton (ARM64) EC2 instance
types.  The correct CPU architecture is detected directly from the
`--instance_type` you select -- there is no separate architecture flag to
set, and no way to accidentally request a mismatched OS/instance-type pair.
Just pick a Graviton instance type (e.g. `m7g.large`, `c8g.xlarge`) and the
matching AMI is selected automatically.  (Windows Server does not run on
Graviton -- AWS does not publish Windows AMIs for ARM64 -- so Windows
`base_os` values are restricted to x86_64 instance types.)

* Administrative control over the allowed EC2 instance types that can be deployed.

* Custom AMI support to enable deployment of standardized cloud computing environments.  Please see "Working with Custom AMIs" below for more details on how to leverage these options which include:
  * Spawning of new instances from previously built user-supplied "custom AMIs."
  * Creation of new "golden images" using EC2 instances spawned from Ec2InstanceMaker as the source.
  * Easy inclusion of user customization scripts within the Ec2InstanceMaker provisioning process.
  * Optional construction of "golden images" with encrypted root EBS volumes.
  * Incorporation into existing DevOps CI/CD piplines.

* Multiple instances with identical configurations built at the same time a.k.a. "instance families."

* Command line designation of dev, test, stage, and prod operating levels.

* Deployment of EC2 Spot instances with a adjustable price buffer to help
prevent terminations caused by Spot market fluctuations.

* Deployment of instance families into EC2 placement groups utilizing the
"cluster" strategy.

* Variable EBS root volume sizes up to 16 TB.

* Automatic application of EBS optimization for supported instance types.

* Attachment of provisioned IOPS, throughput optizimed, and general purpose
(gp2) SSD EBS volumes during the instance creation process.

* Selective disabling of Intel HyperThreading.

* Custom i.e. user-provided EC2 security groups.

* Custom i.e. user-provided IAM instance profiles that can be created from user-supplied JSON policy documents or pre-existing IAM roles.  Guidance for how to communicate with your local DevOps team to get their support with deployment of tthe aforementioned policy documents is also provided in the "Note to DevOps Teams" section below.

* Control of the IAM namespace used by roles, instance profiles, and policies
through the "iam_name_prefix" parameter.  If this switch is not set, all IAM
entitities default to using "Ec2InstanceMaker" as the prefix.  This makes it
easier for DevOps teams to incorporate Ec2InstanceMaker into architectures that
are based on users assuming predefined roles to perform activities in the AWS
environment.

* Email notifications via SNS whenever an instance is created or deleted.

* Identification of the instance owner, email address, and department using
an easily extendable tagging framework.  The instance can also be associated
with a specific project identification tag.  The department tagging mechanism
is easily customizable to meet your use case.

* Additional instance customization hooks using EC2 instance userdata or
post-installation shell scripts.  Please see the "Instance Customization"
section for more details.

* Custom scripting to automate deletion the instance or all members of the
instance family at once.

* Single-command access to Linux instances via AWS Systems Manager Session
Manager (`aws ssm start-session`) — no inbound SSH port needs to be
reachable from anywhere.  If multiple instances were created together, an
easy-to-use menu is provided for the user to select the instance of interest.

* For Windows instances, mapping of IP addresses to decrypted Administrator
passwords in an easy-to-parse table dumped to the console, plus an SSM
port-forwarding tunnel for RDP (`localhost:13389`) instead of requiring
3389 reachable from anywhere.

* Never exposes SSH/RDP to `0.0.0.0/0` -- the security group's ingress
rule is always scoped by `--ssh_allowed_ips` (defaults to the instance's
own VPC CIDR; an explicit `0.0.0.0/0` is refused outright).

* Every instance is tagged `ManagedBy: Ec2InstanceMaker`, so tooling (this
toolkit's own `manage_instance.py` included) can safely identify and act
on only instances this toolkit created.

* `manage_instance.py` for starting, stopping, rebooting, or fully
terminating a previously-built instance or family after the fact, without
needing to re-run `make_instance.py` -- also reports status (`-s`) and
lists every managed instance in a region (`-l`).

* CloudWatch Agent logging on by default -- ships cloud-init and system
logs to CloudWatch Logs with a configurable retention period
(`--log_retention_days`), optionally preserved past termination
(`--preserve_cloudwatch_logs`).

* User-owned customization via `custom_user_scripts/`, kept separate from
the toolkit-controlled `templates/` directory -- both a real pre-login
(cloud-init) hook and a post-boot hook, selectable per build via
`--custom_user_scripts`.

* Operability in Turbot environnments.  Please visit https://www.turbot.com for more information.

* Restriction of attaching public IP addresses for environments that require additional security.

* Selection of a specific VPC.  Note that the VPC *must* be named or Terraform will fail.

## Installation Instructions for the Impatient

These instructions are provided for the impatient and/or lazy.

* Install Homebrew (if using OSX).

* Install and configure the AWS CLI.

* Install Terraform.

* Install jq.

* Create and activate a virtual Python 3.12 environment: `python3.12 -m venv .venv`.

* Install the Python libraries in Ec2InstanceMaker/requirements.txt into the
freshly created virtual Python environment.

* Build away!

**It is strongly suggested that the reader carefully review the installation documentation (INSTALL.md) to avoid potentially costly and time-consuming mistakes.**

## Note to DevOps Teams

As noted above, Ec2InstanceMaker is intended to reduce the administrative burden required for DevOps teams to support the diverse compute and storage needs of their stakeholders; conversely, it can empower scientists, engineers, statisticians, and analysts to compute at scale without needing to get assistnace from their Devops team.

* **Ec2InstanceMaker supports the use of private IP addresses for environments that require enhanced security.**
  * Deployment of public IP addresses can be disabled by setting `--public_ip=false`.
  * Please note that if this feature is invoked within a compute environment that does not live in the target VPC, the provisioning process will fail with a "connection refused" error:
`Error: timeout - last error: dial tcp 172.31.32.161:22: connect: connection refused`
  * A more descriptive error will be returned in a future release.

* **Ec2InstanceMaker can deploy into any active VPC that is named.**

* **Ec2InstanceMaker does not make any changes to the existing networking environment already present in the operator's AWS account.**
  * These tools do not create new VPCs, subnets, Internet or NAT gateways, routes, or Transit Gateways.
  * They do not modify Route53 configurations, change default routes, or otherwise impact or deploy any infrastructure that is not explicitly documented or easily inferred by reviewing the code.

* **Ec2InstanceMaker creates generic IAM roles, policies, and instance templates that are individualized as much as possible for each instance or instance family.  By default, Ec2InstanceMaker-spawned instances cannot spwan children.**
  * These JSON templates are located in the templates/ subdirectory and contain all required IAM permissions to work with the AWS services listed below:
    * EC2
    * AutoScaling
    * S3
    * SQS
    * SNS
    * IAM
    * SSM
    * STS
    * Directory Service (DS)
    * EC2 Messages
  * If you run into permissions problems building instances or provisioning storage resources, it's usually because of an IAM issue.  When speaking with your DevOps professionals, the following options are suggested:
    * Set `--iam_json_policy=ExtendedEc2InstancePolicy.json` to use the included JSON policy document which permits children instances to be spawned.
    * Work with your DevOps team to construct a custom IAM role that provides appropriate permissions for your environment, then include it by setting `--iam_role=$ROLE_NAME` when invoking `make_instance.py.`  Please refer to the EXAMPLE_USE_CASES document for additional guidance.
  * DevOps teams should also be aware that additional granular control over the IAM namespace can be realized by setting `--iam_name_prefix` to a chosen value.  This makes it eaiser to incorporate Ec2InstanceMaker into environments that perfer to have users assume a set of standard roles to perform tasks in the AWS environment.

For example:
```
$ ./make_instance.py -N dev01 -O rmarable -E rodney.marable@gmail.com -A us-west-2b --iam_name_prefix=MyEc2IamPrefix
```

This command will create an EC2 instance role, instance profile, and policy prepended with MyEc2IamPrefix.  The user can only create, delete, or modify IAM entities that are prepended with "MyEc2IamPrefix."

## Using Ec2InstanceMaker

Ec2InstanceMaker is a collection of scripts and user-configurable templates.

**Scripts.** Please see below for more details on how the scripts are used.
* make_instance.py
* access-instance.py
* kill-instance.$INSTANCE_NAME.py

**Templates**.  Ec2InstanceMaker provides some generic templates that can be
customized to permit more granular control over the IAM EC2 instance policy
that is used to create the instance profiles that are created by the toolkit.

  * **MinimalEc2InstancePolicy.json** is a bare-bones template that allows only
EC2 and S3 API calls.
  * **GenericEc2InstancePolicy.json** provides enough permissions for an EC2
"jumphost" spawned by make_instance.py to in turn create additional instances.
In addition to allowing EC2 and S3, it also permits maintenance of SQS
queues, SNS topic administration, IAM role and instance profile maintenance,
and access to SSM.  However, please note that
this template does *NOT* provide adequate permissions for instances built with
Ec2InstanceMzker to spwan children of their own.
  * **ExtendedEc2InstancePolicy.json** is equivalent to `GenericEc2InstancePolicy.json` permissions for an EC2 except that it grants Ec2InstanceMaker-spawned
instances appropriate permissions to spawn children.
  * **Ec2AdminInstancePolicy.json** is provides the EC2 instance profile with
full adminstrator rights over the AWS account in question.  *Use this policy
judiciously!*
  * **build_instance.j2** permits the operator to leverage Terraform's
post-install hook to perform further configuration of EC2 instances using a
shell script.
  * **instance_userdata.j2** will allow the operator to leverage EC2 instance
userdata to perform additional configuration.  Please reference:

https://docs.aws.amazon.com/AWSEC2/latest/WindowsGuide/ec2-instance-metadata.html#instancedata-add-user-data

### Using make_instance.py

**make_instance.py** builds EC2 instances for a wide variety of use cases.

```
$ ./make_instance.py -h
usage: make_instance.py [-h] --az AZ --instance_name INSTANCE_NAME
                        --instance_owner INSTANCE_OWNER --instance_owner_email
                        INSTANCE_OWNER_EMAIL
                        [--base_os {al2023,alinux2,alma9,alma10,rhel9,rhel10,rocky9,rocky10,ubuntu2404,ubuntu2604,windows2019,windows2022,windows2025}]
                        [--count COUNT] [--custom_ami CUSTOM_AMI]
                        [--custom_user_scripts CUSTOM_USER_SCRIPTS]
                        [--debug_mode {true,false}]
                        [--ebs_encryption {true,false}]
                        [--ebs_optimized {true,false}]
                        [--ebs_root_volume_iops EBS_ROOT_VOLUME_IOPS]
                        [--ebs_root_volume_size EBS_ROOT_VOLUME_SIZE]
                        [--ebs_root_volume_type {gp2,io1,st1}]
                        [--ebs_device_volume_iops EBS_DEVICE_VOLUME_IOPS]
                        [--ebs_device_volume_size EBS_DEVICE_VOLUME_SIZE]
                        [--ebs_device_volume_type {gp2,io1,st1}]
                        [--ec2_keypair EC2_KEYPAIR]
                        [--enable_placement_group {true,false}]
                        [--hyperthreading {true,false}]
                        [--iam_json_policy IAM_JSON_POLICY]
                        [--iam_name_prefix IAM_NAME_PREFIX]
                        [--iam_role IAM_ROLE]
                        [--instance_owner_department INSTANCE_OWNER_DEPARTMENT]
                        [--request_type {ondemand,spot}]
                        [--instance_type INSTANCE_TYPE]
                        [--prod_level {dev,test,stage,prod}]
                        [--enable_cloudwatch_logs {true,false}]
                        [--log_retention_days LOG_RETENTION_DAYS]
                        [--placement_group_strategy {cluster,spread}]
                        [--preserve_ami {true,false}]
                        [--preserve_cloudwatch_logs {true,false}]
                        [--project_id PROJECT_ID] [--public_ip PUBLIC_IP]
                        [--security_group SECURITY_GROUP]
                        [--spot_buffer SPOT_BUFFER]
                        [--ssh_allowed_ips SSH_ALLOWED_IPS]
                        [--turbot_account TURBOT_ACCOUNT]
                        [--vpc_name VPC_NAME]

make_instance.py: Command-line interface to build EC2 instances

options:
  -h, --help            show this help message and exit
  --az AZ, -A AZ        AWS Availability Zone (REQUIRED)
  --instance_name INSTANCE_NAME, -N INSTANCE_NAME
                        name of the instance(s) (REQUIRED)
  --instance_owner INSTANCE_OWNER, -O INSTANCE_OWNER
                        ActiveDirectory username of the instance_owner
                        (REQUIRED)
  --instance_owner_email INSTANCE_OWNER_EMAIL, -E INSTANCE_OWNER_EMAIL
                        Email address of the instance_owner (REQUIRED)
  --base_os {al2023,alinux2,alma9,alma10,rhel9,rhel10,rocky9,rocky10,ubuntu2404,ubuntu2604,windows2019,windows2022,windows2025}, -B {al2023,alinux2,alma9,alma10,rhel9,rhel10,rocky9,rocky10,ubuntu2404,ubuntu2604,windows2019,windows2022,windows2025}
                        instance base operating system (default = al2023
                        a.k.a. Amazon Linux 2023)
  --count COUNT, -C COUNT
                        number of EC2 instances to create (default = 1)
  --custom_ami CUSTOM_AMI
                        ami-id of a custom Amazon Machine Image (default =
                        UNDEFINED)
  --custom_user_scripts CUSTOM_USER_SCRIPTS
                        comma-separated list of custom_user_scripts/ names to
                        run (default = default); each name needs
                        custom_user_prelogin_script.j2_<name> and/or
                        custom_user_postboot_script.j2_<name> to exist
  --debug_mode {true,false}, -D {true,false}
                        Enable debug mode (default = false)
  --ebs_encryption {true,false}
                        enable EBS encryption where possible (default = false)
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
  --ebs_device_volume_iops EBS_DEVICE_VOLUME_IOPS
                        amount of provisioned IOPS for the EBS secondary
                        volume when ebs_root_volume_type=io1 (default = 0)
  --ebs_device_volume_size EBS_DEVICE_VOLUME_SIZE
                        Secondary EBS volume size in GB (Linux default = 8,
                        Windows default = 30)
  --ebs_device_volume_type {gp2,io1,st1}
                        EBS secondary volume type (default = gp2)
  --ec2_keypair EC2_KEYPAIR
                        define an EC2 key pair name to provide SSH or Remote
                        Desktop access (default = ec2_keypair_default)
  --enable_placement_group {true,false}, --enable_pg {true,false}
                        Place the new instances in an EC2 placement group
                        using the "cluster" strategy (default = false)
  --hyperthreading {true,false}, -H {true,false}
                        enable Intel Hyperthreading (default = true)
  --iam_json_policy IAM_JSON_POLICY, -J IAM_JSON_POLICY
                        Use a pre-existing JSON policy document in the
                        /templates subdirectory to set permissions for
                        iam_role (default = GenericEc2InstancePolicy.json
  --iam_name_prefix IAM_NAME_PREFIX
                        Provide a prefix for the IAM entities associated with
                        the instance (default = Ec2InstanceMaker)
  --iam_role IAM_ROLE   Apply a pre-existing IAM role to the instance(s)
  --instance_owner_department INSTANCE_OWNER_DEPARTMENT
                        Department of the instance_owner (default = compbio)
  --request_type {ondemand,spot}
                        choose between ondemand or spot instances (default =
                        ondemand)
  --instance_type INSTANCE_TYPE, -T INSTANCE_TYPE
                        EC2 instance type (default = t2.micro); CPU
                        architecture (x86_64 or Graviton/ARM64) is auto-
                        detected, no separate flag needed
  --prod_level {dev,test,stage,prod}
                        Operating stage of the jumphost (default = dev)
  --enable_cloudwatch_logs {true,false}
                        Install and configure the CloudWatch Agent on the
                        instance(s) to ship logs to CloudWatch Logs (default =
                        true)
  --log_retention_days LOG_RETENTION_DAYS
                        Number of days to retain CloudWatch Logs for the
                        instance(s) (default = 30)
  --placement_group_strategy {cluster,spread}, --pg_strategy {cluster,spread}
                        Designate an EC2 placement group strategy (default =
                        cluster)
  --preserve_ami {true,false}
                        Preserve any AMI image built from the instance(s)
                        post-termination (default = true)
  --preserve_cloudwatch_logs {true,false}
                        Preserve the CloudWatch Logs group when the
                        instance(s) are terminated (default = false)
  --project_id PROJECT_ID, -P PROJECT_ID
                        Project name or ID number (default = UNDEFINED)
  --public_ip PUBLIC_IP, -p PUBLIC_IP
                        Attach a public IP address to the instance(s) (default
                        = true)
  --security_group SECURITY_GROUP, -S SECURITY_GROUP
                        Primary security group name for the EC2 instance
                        (default = ec2instancemaker_sg)
  --spot_buffer SPOT_BUFFER
                        pricing buffer to protect from Spot market
                        fluctuations: spot_price = spot_price +
                        spot_price*spot_buffer
  --ssh_allowed_ips SSH_ALLOWED_IPS
                        CIDR block allowed to reach the instance's SSH/RDP
                        port (default = the CIDR of the instance's own VPC).
                        Never accepts 0.0.0.0/0.
  --turbot_account TURBOT_ACCOUNT
                        Turbot account ID (default = DISABLED)
  --vpc_name VPC_NAME   Name of the VPC (default = vpc_default)
```

`--instance_owner_department` is free text — pass whatever your
organization's own department/team taxonomy uses; this toolkit doesn't
maintain a list of valid values.

### Building Instances

The minimum required arguments are the Availability Zone, the instance owner's
username and email address, and the instance name.  All other parameters will
fall back to the default of a single ondemand t2.micro instance running Amazon
Linux 2 with an 8 GB EBS gp2 unencrypted volume as the root device, using the
supplied JSON policy document to generate an IAM instance profile providing
EC2 and S3 access:

```
$ ./make_instance.py -A us-east-2a -N ec2-testinstance01 -O rmarable -E rodney.marable@gmail.com
```

To build a Windows instance using (mostly) default values:

```
./make_instance.py -N dev01 -O rmarable -E rmarable@amazon.com -A us-east-1b -T t3a.micro -B windows2019
```

If the user provides illegal parameter values or if any of the required AWS
resources fail to deploy, the script will loudly echo an appropriate error
before aborting.  In the example below, the user attempts to build an EBS root
device that is larger than 16 TB:

```
$ ./make_instance.py -A us-east-2a -N ec2-testinstance01 -O rmarable -E rodney.marable@gmail.com --ebs_root_volume_size=16049311

Performing parameter validation...

Selected EC2 instance type: t2.micro (x86_64)
** WARNING **
t2.micro does not support EBS optimization!
Disabling ebs_optimization for: ec2-testinstance01

** ERROR **
Maximum allowed EBS volume size is 16 TB (16000 GB)!

Please resolve this error and retry the instance build.
Aborting...
```

### Accessing Instances

**access_instance.py** provides an easy mechanism for connecting to
Ec2InstanceMaker-built instances via **AWS Systems Manager Session
Manager** (`aws ssm start-session`) — not direct SSH/RDP. No inbound
SSH/RDP port needs to be reachable from wherever you run this; the
instance just needs its SSM Agent registered (see "Prerequisites" below).

For a single Linux instance:

```
$ ./access_instance.py -N dev01
Opening an SSM Session Manager connection to: dev01

Starting session with SessionId: rmarable-0123456789abcdef0

sh-5.2$ exit
exit


Exiting session with sessionId: rmarable-0123456789abcdef0.

Reconnect to dev01 by running this command:

$ ./access_instance.py -N dev01
```

For a single Windows instance, `access_instance.py` decrypts the
Administrator password as before, then opens an SSM port-forwarding
tunnel for RDP instead of requiring 3389 reachable from anywhere — point
your Remote Desktop client at `localhost:13389` while the tunnel is open:

```
$ ./access_instance.py -N dev01
Access the new instance via Remote Desktop with this information:

+------+---------------+---------------+----------------------------------+
| Item | Instance Name |   IP Address  |      Adminstrator Password       |
+------+---------------+---------------+----------------------------------+
|  1   |     dev01     | 34.201.49.101 | K?Uf.@Roy-?D-W-?GPDW@4_BaT%=?EiD |
+------+---------------+---------------+----------------------------------+

Opening a Remote Desktop tunnel to: dev01
Connect your Remote Desktop client to: localhost:13389
Press Ctrl+C to close the tunnel when finished.

Starting session with SessionId: rmarable-0123456789abcdef0
Port 13389 opened for sessionId rmarable-0123456789abcdef0.
Waiting for connections...
```

When working with Linux instance families, `access_instance.py` provides an
interactive menu allowing the user to select the specific instance of interest:

```
$ ./access_instance.py -N fam01

+------+---------------+----------------+---------------------+
| Item | Instance Name |   IP Address   |      Instance ID    |
+------+---------------+----------------+---------------------+
|  1   |    fam01-0    | 18.204.42.129  | i-0123456789abcdef0 |
|  2   |    fam01-1    |  34.237.2.35   | i-0fedcba9876543210 |
|  3   |    fam01-2    |  3.83.36.203   | i-0a1b2c3d4e5f60789 |
+------+---------------+----------------+---------------------+

Select an instance to access using SSM Session Manager:
2

Opening an SSM Session Manager connection to: fam01-1

Starting session with SessionId: rmarable-0fedcba9876543210

sh-5.2$ exit
exit


Exiting session with sessionId: rmarable-0fedcba9876543210.

Reconnect to fam01-1 by running this command:

$ ./access_instance.py -N fam01
```

Windows families work the same way — the password table for every member
prints first, then you're prompted which one to open the RDP tunnel to.

The "-m" switch can be used to access a specific instance as it is listed
in the table.  This enables access_instance.py to be used for other automated
tasks.

**Prerequisites:** the [Session Manager plugin for the AWS
CLI](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
must be installed locally (separate from the AWS CLI itself) — `aws ssm
start-session` fails without it. The instance's SSM Agent must also be
registered, which normally happens automatically at boot for every
`base_os` this toolkit supports — `rhel9`, `rhel10`, `rocky9`, and
`rocky10`'s standard AMIs don't preinstall it, so Ec2InstanceMaker
installs and enables it via cloud-init for those four specifically.

`$ ./access_instance.py -N fam01 -m 3`

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
|    dev01-1    |  3.216.27.77  | uMP)sOvC2w7WXLi3h3L2%9VjI5Ovwo7c |
|    dev01-2    |  34.237.0.213 | 9T2cye$ytrEp9)niIlNv@@P;ORu8f&pu |
+---------------+---------------+----------------------------------+

Reprint this table:
$ ./access_instance -N dev01
```

### Managing Instances

**manage_instance.py** starts, stops, reboots, or fully terminates a
previously-built instance or family:

```
./manage_instance.py -N <instance_name> -A start|stop|reboot|terminate [-c]
```

It identifies which instance(s) it's allowed to act on via the
`ManagedBy: Ec2InstanceMaker` tag every instance gets at build time — a
`Name` tag collision with something else this toolkit didn't create can
never cause it to act on the wrong resource. `-c` skips the interactive
confirmation prompt.

```
$ ./manage_instance.py -N dev01 -A stop

The following instance(s) will be stopped:
  i-0123456789abcdef0  dev01  (running)

Type "yes" to continue: yes
Stop request sent for: i-0123456789abcdef0
```

`--region`/`-r` is optional — if omitted, it's read from the `region:`
field already recorded in `./vars_files/<instance_name>.yml`.

`-A terminate` does not just stop the instance's billing meter — it
delegates entirely to `kill-instance.<instance_name>.sh` (see "Destroying
Instances" below), so it gets the full teardown (security group, IAM,
SNS, CloudWatch Logs, local state), not a bare `TerminateInstances` call
that would leave those resources behind. This only works from the repo
checkout where the instance was built.

Note: this toolkit always requests **one-time** Spot Instances, which AWS
does not allow to be stopped and later restarted — `manage_instance.py`
refuses `-A start`/`-A stop` against a Spot Instance with a clear error
rather than letting AWS's own error surface unexplained. `-A reboot` and
`-A terminate` both work fine against Spot Instances.

`-S`/`--status` and `-l`/`--list-all` are alternatives to `-A`/`--action`
(exactly one of `-A`, `-S`, or `-l` must be given):

```
$ ./manage_instance.py -N dev01 -S

  i-0123456789abcdef0  dev01  (running)  Spot: No
```

`-l`/`--list-all` lists every Ec2InstanceMaker-managed instance in a
region, identified the same way (the `ManagedBy` tag), without requiring
`--instance_name`. `--region`/`-r` is required for `-l` since there is no
per-instance `vars_files/<instance_name>.yml` to fall back to for a
region-wide listing. A `Spot` column is only shown if at least one
instance actually returned is a Spot Instance:

```
$ ./manage_instance.py -l -r us-east-1

+-------+---------------------+---------------+---------+----------------+------+
|  Name |     Instance ID     | Instance Type | Base OS | Instance Owner | Spot |
+-------+---------------------+---------------+---------+----------------+------+
| dev01 | i-0123456789abcdef0 |   t3.medium   | al2023  |     rmarable   |  No  |
| dev02 | i-0fedcba9876543210 |   t3.large    | ubuntu2204 |    rmarable |  Yes |
+-------+---------------------+---------------+---------+----------------+------+
```

### Destroying Instances

**kill-instance.$INSTANCE_NAME.sh** is a personalized script designed to
terminate specific EC2 instances, EC2 security groups, IAM entities, and any
associated storage resources that were tagged with the `instance_serial_nunber.`
It is generated by make_instance.py and will delete itself when all tagged
instances and resources are terminated. It also deletes the instance's
CloudWatch Logs group unless the instance was built with
`--preserve_cloudwatch_logs=true`, in which case the log group is left in
place for post-mortem debugging after termination.

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
Deleted CloudWatch Logs group: /ec2instancemaker/dev01
Deleted IAM EC2 policy: Ec2InstanceMaker-policy-dev01-53522312062019
Deleted IAM EC2 instance profile: Ec2InstanceMaker-profile-dev01-53522312062019
Deleted IAM role: Ec2InstanceMaker-role-dev01-53522312062019
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

The instance userdata template disables Intel HyperThreading if `--hyperthreading=false`.

Ec2InstanceMaker also permits user customization via `custom_user_scripts/`
— a directory kept separate from the toolkit-controlled `templates/`
directory on purpose, so it's always clear what's yours to edit. There are
two hooks, because they run at genuinely different points:

- **`custom_user_prelogin_script.j2_<name>`** — runs via cloud-init,
  before an operator can log in at all. Runs as root; keep it fast (a
  system tweak, an `/etc/hosts` entry, a MOTD banner), since it delays
  everything else on the instance until it finishes.
- **`custom_user_postboot_script.j2_<name>`** — runs after cloud-init
  finishes and the instance is fully bootstrapped (package manager
  updated, AWS CLI/git/gcc present). This is where a real software
  install, cloning a repo, or per-user dotfiles belong.

A module only needs one of the two files — a lightweight config tweak can
be prelogin-only, a pure software install can be postboot-only. Select
which modules run with `--custom_user_scripts` (comma-separated, default
`default`):

```
./make_instance.py ... --custom_user_scripts default
./make_instance.py ... --custom_user_scripts monitoring,R
```

Both files are real Jinja2 templates with the same variables every other
template gets (`instance_name`, `ec2_user`, `region`, `base_os`,
`package_manager`, etc.). See `custom_user_scripts/README.md` for the full
explanation, worked examples, and exactly which variables are available.

This provides operators and DevOps professionals with a powerful mechanism for quickly building and distributing "golden" AMI images that can be widely distributed throughout an enterprise, or for customized images that can be specifically tailored by individuals or teams.  Please see "Working with Custom AMIs" and "Building New AMIs with the build-ami Script" for additional details.

**Neither hook applies to Windows instances today** — this is a known,
documented gap, not an oversight. Additional customization of Windows
instances can only be performed through the userdata template
(`templates/instance_userdata.j2`), which is toolkit-controlled.

Support for joining a Windows Active Directory domain will be provided in a future release.  Support for PowerShell scripts may also be provided in subsequent releases.

## EC2 Placement Groups

Ec2InstanceMaker supports the use of EC2 placement groups by setting
`--enable_placement_group=true`.  The strategies supported currently are
"cluster' and "spread."  Future releases will support "partition" placement
groups with additional control over the number of allowed partitions through
command line switches.

The placement group will be named "ec2pg-$INSTANCE_SERIAL_NUMBER" and is tied
to the instance life cycle.

The script will abort if the operator attempts to place a single instance into
a placement group.

## Working with Custom AMIs ##

Ec2InstanceMaker supports building new instances from custom AMIs by using the `--custom_ami` switch.  If the custom_ami is not found, the script will return an error.

## Building New AMIs Using the build-ami Script ##

Ec2InstanceMaker provides a customized build script for each instance_serial_number which permits the operator to create and register a new AMI image.  By including custom code in the `build-instance.j2` template, users now have a powerful mechanism for quickly customizing AMIs without requiring DevOps assistance.  Conversely, DevOps professionals now have a means to disseminate curated "golden" AMIs throughout their environment.

To build a new AMI:
```
$ ./build-ami.$INSTANCE_NAME.sh
```

By default, any AMI created by this script will be preserved after the instance (or instance family) is terminated.

To modify this behavior, the instance(s) must be built by setting `preserve_ami=false` like this:
```
./make_instance.py -O rmarable -E rodney.marable@gmail.com -N dev01 -A us-east-1a --preserve_ami=false
```

This is *not* a recommended best practice and should only be enabled when testing.

## Troubleshooting

* Python version 3.12 or greater is required by this software.  Additionally,
you must install the required libraries in requirements.txt.  If any of these
components are missing, you will observe missing Python module errors:

```
ModuleNotFoundError: No module named boto3'
```

* Terraform must also be present in order for the scripts in this toolkit to
operate as expected.  If it is missing from the installing user's path,
make_instance.py will return an "application is missing" error:

```
$ ./make_instance.py -N dev01 -O rmarable -E rmarable@amazon.com -A us-east-1b -T t3.micro -C 3 --request_type=spot

** ERROR **
Terraform is missing! Please visit: https://www.terraform.io/downloads

Please resolve this error and retry the instance build.
Aborting...
```

* Rocky Linux requires subscribing to the appropriate operating system
channel in the AWS Marketplace.  (Amazon Linux, Amazon Linux 2023,
AlmaLinux, RHEL, and Ubuntu do **not** -- their AMIs carry no AWS
Marketplace product code and launch immediately.)

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

* Encryption of root volumes during the instance installation process is now
supported by Terraform.  When the instance is finished building, build a new encrypted AMI with the included `build-ami` script using the symlink in the top-level SRC tree.
  * Note the resulting AMI ID value and use it to launch subsequent instances with "--custom_ami=$AMI_ID".

You can also enable EBS encryption by default for all instances in the account by reviewing this link to the AWS public documentation:

https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSEncryption.html#encryption-by-default

Please be advised that this is a per-Region setting that can't be disabled on
a per-volume or snapshot basis.  Furthermore, you will not be able to launch
instances that do not support encryption within the region in question.  This
link on the AWS public documentation summarizes the instances that can be
launched within the region in question for that account:

https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSEncryption.html#EBSEncryption_supported_instances

* If the instance(s) cannot be built due to a lack of spot capacity, Terraform
will return a "capacity-not-available" error.  To resolve this, try increasing
the spot_buffer or using ondemand instead.
