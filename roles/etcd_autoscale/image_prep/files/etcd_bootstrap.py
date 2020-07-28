#!/usr/bin/python
import requests
import json
import os
import sys
from boto3.session import Session
from jinja2 import Template
import subprocess

metaurl='http://169.254.169.254/latest/meta-data/'

class etcd_helper(object):
    def __init__(self):
        r = requests.get('http://169.254.169.254/latest/dynamic/instance-identity/document')
        if r.ok:
            self.i = json.loads(r.content)
            self.i['hostname'] = os.getenv('HOSTNAME')
            self.tags = {}
            tag_command = f'aws --region { self.i["region"] } ec2 describe-tags --filters Name=resource-id,Values={self.i["instanceId"]}'
            for t in json.loads(subprocess.check_output(tag_command.split()).decode('utf8'))['Tags']:
                self.tags[t['Key']] = t['Value']
            self.cluster = self.tags['KubernetesCluster']
        else:
            print("unable to retrieve instance metadata, exiting")
            sys.exit(1)
        
        self.session = Session(region_name=self.i['region'])
        self.autoscaling = self.session.client('autoscaling')
        self.ec2 = self.session.client('ec2')
    def get_autoscaling_peer_ips(self):
        parent_asg = self.autoscaling.describe_auto_scaling_instances(InstanceIds=[self.i["instanceId"]])
		my_asg_name = parent_asg['AutoScalingInstances'][0]['AutoScalingGroupName']
        my_asg = self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[my_asg_name])
	    peer_ids = [ instance['InstanceId'] for instance in my_asg['AutoScalingGroups'][0]['Instances'] ]	
        peers = self.ec2.describe_instances(InstanceIds=peer_ids)
        peer_ips = [ peer['Instances'][0]['PrivateIpAddress'] for peer in peers['Reservations'] ]
		return peer_ips
	def get_initial_cluster_string(self):
        peer_ips = self.get_autoscaling_peer_ips()
        initial_cluster = 'https://'.join([ ip + ":2380," for ip in peer_ips ]).strip(',')
        return initial_cluster
    def render_manifest(self):
		f = open('/usr/local/share/etcd_autoscale/manifest_template.j2')
        t = f.read()
        f.close()
        template = Template(t)
        manifest = template.render(
          hostname = self.i['hostname']
          private_ip = self.i['private_ip']
          initial_cluster = self.get_initial_cluster_string()
		  )
		return manifest
    def create_certs(self):
        print("creating certs")
        create_invocation = subprocess.check_call('kubeadm init phase certs etcd-ca')
        pass
    def write_manifest(self):
        manifest = self.render_manifest()
        m = open('/etc/kubernetes/manifests/etcd.yaml')
        mm = m.write(manifest)
        m.close()
    def main(self):
        self.write_manifest()

if __name__ == '__main__':
    print("helping")
    helper = etcd_helper()
    helper.main()
else:
    print(__name__)

