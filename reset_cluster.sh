#!/bin/bash
ansible-playbook all.yml --tags reset
ansible-playbook all.yml --limit ec2-etcd -e state=absent
sed -i -e 's/^s/#s/' ansible.cfg
ansible-playbook all.yml --limit ec2-etcd --tags always
sed -i -e 's/^#s/s/' ansible.cfg
ansible-playbook all.yml --limit ec2-etcd --tags common,etcd
ansible-playbook all.yml --skip-tags common,etcd
