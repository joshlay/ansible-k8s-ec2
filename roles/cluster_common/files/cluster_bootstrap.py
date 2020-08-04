#!/usr/bin/python
import requests
import json
import os
import sys
from boto3.session import Session
from jinja2 import Template
import subprocess
from etcd3 import Etcd3Client

metaurl='http://169.254.169.254/latest/meta-data/'

class cluster_helper(object):
    def __init__(self):
        try:
            self.mode = sys.argv[1]
        except:
            self.mode = None
        r = requests.get('http://169.254.169.254/latest/dynamic/instance-identity/document')
        if r.ok:
            self.i = json.loads(r.content)
        else:
            print("unable to retrieve instance metadata, exiting")
            sys.exit(1)
        self.session = Session(region_name=self.i['region'])
        self.autoscaling = self.session.client('autoscaling')
        self.ec2 = self.session.client('ec2')
        self.i['hostname'] = self.ec2.describe_instances(InstanceIds=[self.i['instanceId']])['Reservations'][0]['Instances'][0]['PrivateDnsName']
        self.elb = self.session.client('elb')
        self.tags = {}
        rawtags = self.ec2.describe_tags(DryRun=False, Filters=[ { 'Name': 'resource-id', 'Values': [self.i["instanceId"]]} ] )
        for tag in rawtags['Tags']:
            self.tags[tag['Key']] = tag['Value']
        self.cluster = self.tags['KubernetesCluster']
        self.peer_ids = self.get_asg_member_instances()
        self.peer_names = self.get_asg_instance_dns_names(self.peer_ids)
        self.etcd_hosts = self.find_etcd_hosts()
        if self.mode == 'etcd':
            etcd_retries = 3
        else:
            etcd_retries = 30
        client = self.get_etcd_client(retries=etcd_retries)
        if client:
            self.get_etcd_cluster_state = "existing"
            self.etcd_client = client
        else: 
            self.get_etcd_cluster_state = "new"
        self.member_string = f"{self.i['hostname']}=https://{self.i['hostname']}:2380"
    def get_asg_member_instances(self):
        parent_asg = self.autoscaling.describe_auto_scaling_instances(InstanceIds=[self.i["instanceId"]])
        my_asg_name = parent_asg['AutoScalingInstances'][0]['AutoScalingGroupName']
        my_asg = self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[my_asg_name])
        if len(my_asg['AutoScalingGroups']) > 0:
            peer_ids = [ instance['InstanceId'] for instance in my_asg['AutoScalingGroups'][0]['Instances'] ]    
        else:
            print("Instance is not a member of an autoscaling group!  This script is not useful here.")
            sys.exit(1)
        return peer_ids
    def get_asg_instance_dns_names(self, instance_ids):
        peers = self.ec2.describe_instances(InstanceIds=instance_ids)
        peer_names = []
        for reservation in peers['Reservations']:
            for peer in reservation['Instances']:
                peer_names.append(peer['PrivateDnsName'])
                
        return peer_names
    def get_initial_cluster_string(self):
        hosts = []
        if self.get_etcd_cluster_state == "existing":
            for member in self.etcd_client.members:
                member_string = f"{member.name}={member.peer_urls[0]}"
                hosts.append(member_string)
            if self.member_string not in hosts:
                hosts.append(self.member_string)
            return ','.join(hosts)
        elif self.get_etcd_cluster_state == "new":
            return ','.join([ f"{ host }=https://{ host }:2380" for host in self.peer_names ])
            
    def get_etcd_client(self, retries=1):
        print(f"attempting to connect to etcd endpoint, {retries} attempts remaining.")
        peers_checked = 0
        print(f'using client certs for mode {self.mode}')
        if self.mode == "etcd":
            key = '/etc/kubernetes/pki/etcd/peer.key'
            cert = '/etc/kubernetes/pki/etcd/peer.crt'
        else:
            key = '/etc/kubernetes/pki/apiserver-etcd-client.key'
            cert = '/etc/kubernetes/pki/apiserver-etcd-client.crt'
        for peer in self.etcd_hosts:
            try:
                print(f'attempting connection to { peer }')
                client = Etcd3Client(
                    host=peer,
                    port=2379, ca_cert='/etc/kubernetes/pki/etcd/ca.crt',
                    cert_key=key,
                    cert_cert=cert,
                    )
                s = client.status()
                return client
            except:
                if peers_checked < len(self.etcd_hosts):
                     continue
            else:
                retries -= 1
                if retries > 0:
                    print("Unable to find etcd server, will retry...")
                    sleep(5)
                    client = get_etcd_client(retries=retries)
                    return client
                else:
                    print("No etcd connection discovered! is the cluster new?")
                    return None
    def find_etcd_hosts(self):
        autoscale_groups = self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[f'{self.cluster}-etcd'])
        if ( autoscale_groups['ResponseMetadata']['HTTPStatusCode'] == 200 ) and ( len(autoscale_groups['AutoScalingGroups']) == 1 ):
            etcd_asg = autoscale_groups['AutoScalingGroups'][0]
            etcd_instances = [ instance['InstanceId'] for instance in etcd_asg['Instances'] ]
            etcd_hosts = self.get_asg_instance_dns_names(etcd_instances)
            return etcd_hosts

        else:
            print("could not find match for etcd asg, failed.")
            sys.exit(1)
        etcd_group
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
    def render_etcd_kubeconfig(self):
        f = open('/usr/local/share/etcd_autoscale/etcd_kubeconfig.yaml.j2')
        t = f.read()
        f.close()
        template = Template(t)
        kubeconfig = template.render(
          hostname = self.i['hostname'],
          private_ip = self.i['privateIp'],
          initial_cluster = self.get_initial_cluster_string(),
          cluster_state = self.get_etcd_cluster_state
          )
        return kubeconfig
    def write_etcd_manifest(self):
        print("rendering manifest")
        manifest_invocation = subprocess.check_call('kubeadm init phase etcd local --config /tmp/kubeconfig.yaml'.split())
        pass
    def create_certs(self):
        print("creating certs")
        create_invocation = subprocess.check_call('kubeadm init phase certs etcd-ca')
        pass
    def write_tmp_kubeconfig(self, kubeconfig):
        m = open('/tmp/kubeconfig.yaml', 'w')
        mm = m.write(kubeconfig)
        m.close()
    def add_etcd_member(self):
        try:
            new_member = self.etcd_client.add_member([f"https://{self.i['hostname']}:2380"])
        except:
            print("failed to add member to cluster")
            sys.exit(1)
    def get_etcd_client_urls(self):
        client_urls = []
        for member in self.etcd_client.members:
            client_urls += member.client_urls
        return client_urls
    def get_join_token(self):
        return False
    def render_node_kubeconfig(self):
        f = open('/usr/local/share/k8s_autoscale/kubeadm-config.yaml.j2')
        t = f.read()
        f.close()
        template = Template(t)
        kubeconfig = template.render(
          hostname = self.i['hostname'],
          private_ip = self.i['privateIp'],
          provider_id = f"aws://{ self.i['availabilityZone'] }/{ self.i['instanceId'] }",
          role = self.mode,
          cluster_name = self.cluster,
          etcd_server_urls = self.get_etcd_client_urls(),
          kube_vip = self.find_load_balancer(role='k8s'),
          join_token = self.get_join_token()
          )
        return kubeconfig

    def main(self):
        if self.mode == "etcd":
            print("helping with etcd cluster")
            kubeconfig = self.render_etcd_kubeconfig()
            self.write_tmp_kubeconfig(kubeconfig)
            self.write_etcd_manifest()
            if self.get_etcd_cluster_state == "existing":
                self.add_etcd_member()
        elif self.mode == "master":
            print("helping with kube master node")
            kubeconfig = self.render_node_kubeconfig()
            self.write_tmp_kubeconfig(kubeconfig)

        elif self.mode == "worker":
            print("helping with kube worker node")
        else:
            print("Mode of operation not defined! Choose from 'etcd', 'master', or 'worker'")

if __name__ == '__main__':
    print("helping")
    helper = cluster_helper()
    helper.main()
else:
    print(__name__)

