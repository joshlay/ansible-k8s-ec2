#!/usr/bin/python
import requests
import json
import yaml
import os
import sys
from boto3.session import Session
from jinja2 import Template
import subprocess
from etcd3 import Etcd3Client
from time import sleep
# this would be hard, shell out to `kubectl`
# from kubernetes import client, config
# from kubernetes.client.rest import ApiException

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
        self.s3 = self.session.client('s3')
        self.i['hostname'] = self.ec2.describe_instances(InstanceIds=[self.i['instanceId']])['Reservations'][0]['Instances'][0]['PrivateDnsName']
        self.elb = self.session.client('elb')
        self.tags = {}
        rawtags = self.ec2.describe_tags(DryRun=False, Filters=[ { 'Name': 'resource-id', 'Values': [self.i["instanceId"]]} ] )
        for tag in rawtags['Tags']:
            self.tags[tag['Key']] = tag['Value']
        self.cluster = self.tags['KubernetesCluster']
        self.cluster_bucket = f"{self.cluster}-lockbox"
        self.peer_ids = self.get_asg_member_instances()
        self.peer_names = self.get_asg_instance_dns_names(self.peer_ids)
        self.etcd_hosts = self.find_etcd_hosts()
        self.member_string = f"{self.i['hostname']}=https://{self.i['hostname']}:2380"
        self.provider_id = f"aws:///{ self.i['availabilityZone'] }/{ self.i['instanceId'] }"
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
    def fetch_certs(self):
        def get_cert(path):
            try:
                response = self.s3.get_object(
                   Bucket=self.cluster_bucket,
                   Key=f"certs/{path}"
                   )
            except:
                print("unable to get secrets from bucket, fail")
                sys.exit(1)
            cert_data = response['Body']
            cert_raw = cert_data.read()
            cert = cert_raw.decode("utf-8")
            cert_data.close()
            return cert
        def write_cert(cert, path, prefix):
            m = open(f'{prefix}/{path}', 'w')
            mm = m.write(cert)
            m.close()

        cert_paths = [
            "etcd/ca.key",
            "etcd/ca.crt",
            "ca.key",
            "ca.crt",
            "front-proxy-ca.crt",
            "front-proxy-ca.key",
            "sa.pub",
            "sa.key"
            ]
        path_prefix = "/etc/kubernetes/pki"
        print(f"fetching certs from {self.cluster_bucket}")
        for path in cert_paths:
            cert = get_cert(path)
            write_cert(cert, path, path_prefix)

        pass
    def create_client_certs(self, mode):
        if mode == 'etcd':
            phases = [
                    'certs etcd-server',
                    'certs etcd-peer',
                    'certs etcd-healthcheck-client'
                    ]
        elif mode == 'master-client':
            phases = [
                    'certs apiserver-kubelet-client',
                    'certs front-proxy-client',
                    'certs apiserver-etcd-client',
                    ]
        elif mode == 'worker-client':
            phases = [
                    'certs apiserver-kubelet-client',
                    'certs front-proxy-client'
                    ]
        elif mode == 'master-apiserver':
            phases = [
                    'certs apiserver'
                    ]
        for phase in phases:
            command = f"kubeadm init phase {phase}"
            if mode == 'master-apiserver':
               command = command + " --config /tmp/kubeconfig.yaml"
               print(f"executing '{command}'")
            manifest_invocation = subprocess.check_call(command.split())
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
        join_invocation = 'kubeadm token --config /etc/kubernetes/kubeadm.conf create --print-join-command'
        command = subprocess.run(join_invocation.split(), capture_output=True)
        if command.returncode == 0:
            invocation = command.stdout.decode('utf-8').strip()
            return invocation
        else:
            print(command.stderr)
            return False
    def render_node_kubeconfig(self):
        f = open('/usr/local/share/k8s_autoscale/kubeadm-config.yaml.j2')
        t = f.read()
        f.close()
        template = Template(t)
        kubeconfig = template.render(
          hostname = self.i['hostname'],
          private_ip = self.i['privateIp'],
          provider_id = self.provider_id,
          role = self.mode,
          cluster_name = self.cluster,
          etcd_server_urls = self.get_etcd_client_urls(),
          kube_vip = self.find_load_balancer(role='k8s'),
          )
        return kubeconfig

    def upload_kubeconfig(self):
        upload = self.s3.upload_file('/etc/kubernetes/admin.conf', self.cluster_bucket, 'admin/kubeconfig')
        os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
        command = 'kubectl -n kube-system get configmap kubeadm-config -o yaml'
        result = subprocess.run(command.split(), capture_output=True)
        if result.returncode == 0:
            parsed = yaml.load(result.stdout)
            f = open('/etc/kubernetes/kubeadm.conf', 'w')
            ff = f.write(parsed['data']['ClusterConfiguration'])
            f.close()
            kupload = self.s3.upload_file('/etc/kubernetes/kubeadm.conf', self.cluster_bucket, 'admin/kubeadm.conf')
        else:
            print("unable to capture kubeadm config!")
    def get_kubeadm_config(self):
        try:
            response = self.s3.get_object(
                       Bucket=self.cluster_bucket,
                       Key="admin/kubeadm.conf")
            data = response['Body']
            raw = data.read()
            k = raw.decode("utf-8")
            data.close()
            m = open(f'/etc/kubernetes/kubeadm.conf', 'w')
            mm = m.write(k)
            m.close()
            response = self.s3.get_object(
                       Bucket=self.cluster_bucket,
                       Key="admin/kubeconfig")
            data = response['Body']
            raw = data.read()
            k = raw.decode("utf-8")
            data.close()
            m = open(f'/etc/kubernetes/admin.conf', 'w')
            mm = m.write(k)
            m
            return True
        except:
            print(f'No kubeconfig discovered in {self.cluster_bucket}, am I new?')
            return False
    def new_master(self):
        etcd_retries = 30
        client = self.get_etcd_client(retries=etcd_retries)
        if client:
            self.etcd_client = client
        else: 
            print('Unable to connect to etcd cluster! Failing.')
            sys.exit(1)
        print("helping with kube master node")
        kubeconfig = self.render_node_kubeconfig()
        self.write_tmp_kubeconfig(kubeconfig)
        self.create_client_certs('master-apiserver')
        call = subprocess.run(
            ['kubeadm', 'init', '--config', '/tmp/kubeconfig.yaml', '--upload-certs'],
            capture_output=True,
            errors=False
            )
        sleep(90)
        self.upload_kubeconfig()
        self.apply_manifests()
    def write_sysconfig(self):
        argstring = f'KUBELET_EXTRA_ARGS=" --provider-id={ self.provider_id }"'
        f = open('/etc/sysconfig/kubelet', 'w')
        f.write(argstring)
        f.close()
    def join_node(self):
        join_command = self.get_join_token()
        if self.mode == 'master':
            join_command += " --control-plane"
        join_command += ' --ignore-preflight-errors=FileAvailable--etc-kubernetes-pki-ca.crt'
        join = subprocess.check_call(join_command.split())
    def get_kubeclient(self):
        try:
            if "KUBERNETES_SERVICE_HOST" in os.environ:
                config.load_incluster_config()
                print('using in cluster config')
            else:
                config.load_kube_config()
                print('using local kube config')
        except:
            print('unable to load cluster configuration, exiting...')
            sys.exit(1)
        self.kubecore = client.CoreV1Api()
        self.kubeapps = client.AppsV1Api()
    def fetch_s3_manifest_list(self):
        rawlist = self.s3.list_objects(
                Bucket=self.cluster_bucket,
                Prefix='manifests/'
                )
        try:
            manifests = [ l['Key'] for l in rawlist['Contents'] if not l['Key'].endswith('/') ]
            return manifests
        except KeyError:
            return False
    def apply_manifests(self):
        manifest_yaml = self.get_manifest_yaml()
        for manifest in manifest_yaml:
            temp_path = f"/dev/shm/bootstrap-manifest-{ manifest_yaml.index(manifest) }"
            f = open(temp_path, 'w')
            f.write(manifest)
            f.close()
            os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
            attempt = subprocess.run(
                [ 'kubectl', 'apply', '-f', temp_path ],
                capture_output=True,
                errors=False
                )
            if attempt.returncode == 0:
               print(attempt.stdout.decode('utf-8'))
            else:
               print(attempt.stderr.decode('utf-8'))
    def get_manifest_yaml(self):
        manifests = self.fetch_s3_manifest_list()
        manifest_yaml = []
        if not manifests:
            print('no manifests discovered in s3 bucket, what do I do?')
        else:
            for manifest_key in manifests:
                response = self.s3.get_object(
                   Bucket=self.cluster_bucket,
                   Key=f"{manifest_key}"
                   )
                stream = response['Body']
                byteobj = stream.read()
                manifest = byteobj.decode("utf-8")
                stream.close()
                manifest_yaml.append(manifest)
        # There's no `kubectl apply` equivalent for this client
        # I don't want to write code to handle each kind
        #        manifest_yaml += [ yaml.load(part) for part in manifest.split('---') ] 
        # remove 'extra' documents introduced by --- splits
        #while None in manifest_yaml:
        #    manifest_yaml.remove(None)
        return manifest_yaml
    def main(self):
        self.fetch_certs()
        if self.mode == "etcd":
            etcd_retries = 3
            client = self.get_etcd_client(retries=etcd_retries)
            if client:
                self.get_etcd_cluster_state = "existing"
                self.etcd_client = client
            else: 
                self.get_etcd_cluster_state = "new"
            print("helping with etcd cluster")
            kubeconfig = self.render_etcd_kubeconfig()
            self.write_tmp_kubeconfig(kubeconfig)
            self.create_client_certs('etcd')
            self.write_etcd_manifest()
            if self.get_etcd_cluster_state == "existing":
                self.add_etcd_member()
        elif self.mode in ["master", "worker"]:
            self.write_sysconfig()
            self.create_client_certs(f"{self.mode}-client")
            fetchconfigs = self.get_kubeadm_config()
            if fetchconfigs:
                self.join_node()
            else:
                self.new_master()
        elif self.mode == "worker":
            print("helping with kube worker node")
            self.create_client_certs('worker-client')
        else:
            print("Mode of operation not defined! Choose from 'etcd', 'master', or 'worker'")

if __name__ == '__main__':
    print("helping")
    helper = cluster_helper()
    helper.main()
else:
    print(__name__)

