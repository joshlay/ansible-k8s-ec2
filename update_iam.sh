#!/usr/bin/bash
pushd roles/kube_builder/asg/templates/
ansible -m include_tasks -a ../tasks/master_iam.yml localhost -e @~/ansible-k8s-ec2/vars/main.yml
popd
pushd roles/etcd_autoscale/asg/templates/
ansible -m include_tasks -a ../tasks/etcd_iam.yml localhost -e @~/ansible-k8s-ec2/vars/main.yml

