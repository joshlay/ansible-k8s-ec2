#!/usr/bin/python
import requests
import json
import os
import sys
from boto3.session import Session
from jinja2 import Template
import subprocess

metaurl='http://169.254.169.254/latest/meta-data/'

class etcd_cert_helper(object):
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
        self.acm = self.session.client('acm')
    def create_certs(self):
        print("creating certs")
        pass
    def is_my_cert_here(self,certlist):
        for cert in certlist:
            if False:
                correct_cert = cert
                return correct_cert
            else:
                print("how did we get here")
                return False

    def put_cert(self, cert):
        """
        this puts the certs where they go
        """
        return True
    def main(self):
        certlist = self.acm.list_certificates()
        if certlist['ResponseMetadata']['HTTPStatusCode'] != 200:
            print("unable to retrieve certificates, exiting")
            sys.exit(1)
        certs = certlist['CertificateSummaryList']
        my_cert = self.is_my_cert_here(certlist)
        if my_cert:
            try_put = put_cert(my_cert)
        else:
            self.create_certs()
        

if __name__ == '__main__':
    print("helping")
    helper = etcd_cert_helper()
    helper.main()
else:
    print(__name__)

