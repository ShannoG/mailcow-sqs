#!/bin/bash

## Userdata
#Rough steps:
#assumption is that we are booting into an instance with a disk attached with a formatted LV called "lv_mailcow"
# if not true, we'll just terminate cloudinit and this can be handled manually.

# Set some env vars
TOKEN=`curl -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600"` \
AWS_REGION=`curl -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region`

apt -o DPkg::Lock::Timeout=90 update
apt -o DPkg::Lock::Timeout=90 install awscli -y

lvdisplay vg_mailcow
if [ $? -ne 0 ]; then
  echo "vg_mailcow not found, exiting"
  exit 1
  fi

#check if the volume exists at /dev/mapper/vg_mailcow-lv_mailcow
lvdisplay /dev/mapper/vg_mailcow-lv_mailcow
if [ $? -ne 0 ]; then
  echo "lv_mailcow not found, exiting"
  exit 1
  fi

# check if the mount target exists at /opt/mailcow
ls /opt/mailcow
if [ $? -ne 0 ]; then
  #mount dir doesn't exist...create it
   echo "mount dir doesn't exist, creating"
   mkdir /opt/mailcow
   fi

#check if it's mounted, if not, add to fstab and mount it
mount | grep /dev/mapper/vg_mailcow-lv_mailcow
if [ $? -ne 0 ]; then
  echo "lv_mailcow not mounted, adding to fstab and mounting"
  echo "/dev/mapper/vg_mailcow-lv_mailcow /opt/mailcow ext4 defaults 0 0" >> /etc/fstab
  mount -a
  #if we didn't succeed, exit
   if [ $? -ne 0 ]; then
     echo "failed to mount lv_mailcow, exiting"
     exit 1
     fi
  fi
   
# clone the mailcow repo
echo "cloning mailcow"
cd /opt/mailcow
git clone https://github.com/mailcow/mailcow-dockerized

cd mailcow-dockerized
# put in the config
echo "putting in config"
#grab settings.ini out of ssm parameter store and place the contents at conf/settings.ini
mkdir conf
aws --region $AWS_REGION ssm get-parameter --name /mailcow/conf/settings.ini --with-decryption --region $AWS_REGION --output text --query Parameter.Value > conf/settings.ini
#pull the contents of mailcow.conf from parameter store, and echo into mailcow.conf
aws --region $AWS_REGION ssm get-parameter --name /mailcow/mailcow.conf --with-decryption --region $AWS_REGION --output text --query Parameter.Value > mailcow.conf
#and docker-compose.override.yml
aws --region $AWS_REGION ssm get-parameter --name /mailcow/docker-compose.override.yml --with-decryption --region $AWS_REGION --output text --query Parameter.Value > docker-compose.override.yml


cd /opt/mailcow
# mkdir volumes (This should exist already)
mkdir -p /var/lib/docker/
ln -s /opt/mailcow/volumes/ /var/lib/docker/volumes

# install docker
while fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
echo "Waiting for other apt-get instances to exit"
# Sleep to avoid pegging a CPU core while polling this lock
sleep 1
done
TRIES=0
until curl -sSL https://get.docker.com | CHANNEL=stable sh || TRIES -gt 10
do
    echo "didn't install docker yet, trying again..."
    TRIES++
    sleep 10
done


#update apt and install docker-compose-plugin
apt -o DPkg::Lock::Timeout=90 update
apt -o DPkg::Lock::Timeout=90 install docker-compose-plugin -y

if [ $? -ne 0 ]; then
    echo "Failed to install docker by the looks. Exiting"
    exit 1
    fi


# start the mailcow stack
cd mailcow-dockerized
docker compose up -d
