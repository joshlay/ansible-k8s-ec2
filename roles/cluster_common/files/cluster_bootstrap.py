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
        else:
            print("unable to retrieve instance metadata, exiting")
            sys.exit(1)
        self.session = Session(region_name=self.i['region'])
        self.autoscaling = self.session.client('autoscaling')
        self.ec2 = self.session.client('ec2')
        self.elb = self.session.client('elb')
        self.tags = {}
        rawtags = self.ec2.describe_tags(DryRun=False, Filters=[ { 'Name': 'resource-id', 'Values': [self.i["instanceId"]]} ] )
        for tag in rawtags['Tags']:
            self.tags[tag['Key']] = tag['Value']
        self.cluster = self.tags['KubernetesCluster']
    def get_autoscaling_peer_ips(self):
        parent_asg = self.autoscaling.describe_auto_scaling_instances(InstanceIds=[self.i["instanceId"]])
        my_asg_name = parent_asg['AutoScalingInstances'][0]['AutoScalingGroupName']
        my_asg = self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[my_asg_name])
        if len(my_asg['AutoScalingGroups']) > 0:
            peer_ids = [ instance['InstanceId'] for instance in my_asg['AutoScalingGroups'][0]['Instances'] ]    
            peers = self.ec2.describe_instances(InstanceIds=peer_ids)
            peer_ips = [ peer['PrivateIpAddress'] for peer in peers['Reservations'][0]['Instances'] ]
            return peer_ips
        else:
            print("Instance is not a member of an autoscaling group!  This script is not useful here.")
            sys.exit(1)
    def get_initial_cluster_string(self):
        peer_ips = self.get_autoscaling_peer_ips()
        initial_cluster = ','.join([ f"https://{ ip }:2380" for ip in peer_ips ])
        return initial_cluster
    def find_load_balancer(self, role):
        if role in ['etcd', 'k8s']:
            elb_name = f"{self.cluster}-{role}"
        else:
            print("this script looks for 'k8s' or 'etcd' LBs, but somehow was asked for something else.")
            sys.exit(1)
        b = self.elb.describe_load_balancers(LoadBalancerNames=[elb_name])
        balancers = b['LoadBalancerDescriptions']
        if len(balancers) != 1:
            print("this script expects exactly one prepared ELB and cannot continue.")
            sys.exit(1)
        else:
            return balancers[0]['DNSName']
    def render_kubeconfig(self):
        f = open('/usr/local/share/etcd_autoscale/etcd_kubeconfig.yaml.j2')
        t = f.read()
        f.close()
        template = Template(t)
        kubeconfig = template.render(
          hostname = self.i['hostname'],
          private_ip = self.i['privateIp'],
          initial_cluster = self.get_initial_cluster_string(),
          )
        return kubeconfig
    def write_manifest(self):
        print("rendering manifest")
        manifest_invocation = subprocess.check_call('kubeadm init phase etcd local --config /tmp/kubeconfig.yaml'.split())
        pass
    def create_certs(self):
        print("creating certs")
        create_invocation = subprocess.check_call('kubeadm init phase certs etcd-ca')
        pass
    def write_kubeconfig(self):
        kubeconfig = self.render_kubeconfig()
        m = open('/tmp/kubeconfig.yaml', 'w')
        mm = m.write(kubeconfig)
        m.close()
    def main(self):
        self.write_kubeconfig()
        self.write_manifest()

if __name__ == '__main__':
    print("helping")
    helper = etcd_helper()
    helper.main()
else:
    print(__name__)

