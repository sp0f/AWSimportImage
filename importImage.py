#!/usr/bin/python

import boto3
import json
from subprocess import check_output
from time import sleep, strftime 
import sys
import logging

# script configuration
SLEEP_TIME=10
DRBUCKET="drbucket-2"
LOGFILE='/var/log/dr.log'
LOGLEVEL=logging.DEBUG
BOTO_LOGLEVEL=logging.CRITICAL

# setting up logging
logging.basicConfig(filename=LOGFILE, level=LOGLEVEL, format='%(asctime)s %(message)s', datefmt='%d-%m-%Y %H:%M:%S')
logging.getLogger('botocore').setLevel(BOTO_LOGLEVEL)
logging.getLogger('boto3').setLevel(BOTO_LOGLEVEL)



# check required cmd params
if len(sys.argv) < 5:
	sys.exit('Usage: %s <VM name> <IP address> <subnet id> <instance type>' % sys.argv[0])

vm_name, ip_addr, subnet_id, instance_type = sys.argv[1:]


client_s3 = boto3.client('s3')

# get bucket object connected with needed VM
bucket_objects=client_s3.list_objects(Bucket=DRBUCKET, Prefix=vm_name)

# check if vm (as .ova) exist in bucket
if 'Contents' not in bucket_objects:
	logging.critical('[%s] object not found :( Exiting',vm_name)
	sys.exit('[%s] object not found. Exiting' % vm_name)

# sort available replicas (.ova) and find newest one
sorted_objects=sorted(bucket_objects['Contents'], key=lambda s: s['LastModified'], reverse=True)
s3key=sorted_objects[0]['Key']


logging.debug('[%s] found %d object(s), newest is %s dated %s', s3key, len(sorted_objects), sorted_objects[0]['Key'], sorted_objects[0]['LastModified'])

# dirty hack but i don't have time to fight with import_image()
json_string="{  \"Description\": \""+s3key+"\", \"DiskContainers\": [ { \"Description\": \"First CLI task\", \"UserBucket\": { \"S3Bucket\": \""+DRBUCKET+"\", \"S3Key\" : \""+s3key+"\" } } ]}"
import_output_dirty=check_output(["/usr/bin/aws","ec2","import-image","--cli-input-json", json_string])

# convert output to native python types
import_output=json.loads(import_output_dirty)
import_id=import_output['ImportTaskId']
logging.info('[%s] Import started. TaskId: %s', s3key, import_id)

client = boto3.client('ec2')

print "Waiting for import task to finish"
while client.describe_import_image_tasks(DryRun=False,ImportTaskIds=[import_id,])['ImportImageTasks'][0]['Status'] == 'active':
	sleep(SLEEP_TIME)

#	try:
#		sys.stdout.write("\rProgress: %s" % client.describe_import_image_tasks(DryRun=False,ImportTaskIds=[import_id,])['ImportImageTasks'][0]['Progress'])
#		sys.stdout.flush()
#	except KeyError:

if client.describe_import_image_tasks(DryRun=False,ImportTaskIds=[import_id,])['ImportImageTasks'][0]['Status'] == 'completed':
	ami_id=client.describe_import_image_tasks(DryRun=False,ImportTaskIds=[import_id,])['ImportImageTasks'][0]['ImageId']
	logging.info('[%s] Import successfull. New AMI id: %s', s3key, ami_id)
	print "Import task "+import_id+" have successfully created AMI "+ami_id+". Proceeding ...\n"
else:
	print "Import task "+import_id+" FAILED. Exiting ...\n"
	status_message=client.describe_import_image_tasks(DryRun=False,ImportTaskIds=[import_id,])['ImportImageTasks'][0]['StatusMessage']
	logging.error('[%s] Import failed. Reason: %s',s3key, status_message)
	sys.exit()

# load AMI object
ec2_resource=boto3.resource('ec2')
image=ec2_resource.Image(ami_id)

#print "Setting Name tag for new AMI"
currDate=strftime("%Y%m%d-%H:%M:%S")
image.create_tags(DryRun=False,Tags=[{'Key': 'Name', 'Value': s3key}, {'Key' : 'Created', 'Value' : currDate}])
logging.debug('[%s] Setting \'Name\' and \'Created\' tag for new AMI', s3key)

# run instance from ami
ec2_client=boto3.client('ec2')
response=ec2_client.run_instances(ImageId=ami_id,MinCount=1,MaxCount=1,SubnetId=subnet_id,InstanceType=instance_type,PrivateIpAddress=ip_addr)
instance_id=response['Instances'][0]['InstanceId']
logging.info('[%s] Starting new instance (%s)',s3key, instance_id)
print "Starting new instance ("+instance_id+")"
ec2_client.create_tags(DryRun=False,Resources=[instance_id,],Tags=[{"Key": "Name", "Value": vm_name},])
logging.debug('[%s] Tag new instance %s with name %s', s3key, instance_id, vm_name)
