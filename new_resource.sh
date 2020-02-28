#!/bin/bash
export RESOURCE="$1"
if [ -z $RESOURCE ]; then
echo "resource name not supplied, exiting"
exit 1
fi
echo "creating project resources for $RESOURCE"
mkdir -vp roles/${RESOURCE}/{tasks,templates,defaults}
export TASKS="roles/$RESOURCE/tasks"
export TEMPLATES="roles/${RESOURCE}/templates"
export YAML1="$TEMPLATES/${RESOURCE}-namespace-service.yaml"
export MAINTASK="${TASKS}/main.yml"
[ -f $MAINTASK ] || cat <<- EOF > $MAINTASK
- name: set up namespace and service
  delegate_to: localhost
  k8s:
    apply: yes
    resource_definition: "{{ lookup('template', '$(basename $YAML1)' ) }}"
    kubeconfig: "{{ playbook_dir }}/local_cache/kubectl.conf"
- name: set up deployment
  delegate_to: localhost
  k8s:
    apply: yes
    resource_definition: "{{ lookup('template', '$(basename ${TEMPLATES}/deployment.yaml)' ) }}"
    kubeconfig: "{{ playbook_dir }}/local_cache/kubectl.conf"
EOF

[ -f $YAML1 ] || cat <<- EOF > $YAML1
apiVersion: v1
kind: Namespace
metadata:
  name: ${RESOURCE}
spec:
  finalizers:
  - kubernetes
---
apiVersion: v1
kind: Service
metadata:
  name: ${RESOURCE}
  namespace: ${RESOURCE}
  labels:
    app: ${RESOURCE}
  annotations:
spec:
  selector:
    app: ${RESOURCE}
  ports: 
    - protocol: TCP
      port: 8080
      containerPort: 8080
---
apiVersion: extensions/v1beta1
kind: Ingress
metadata:
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: selfsigned-issuer
  name: $RESOURCE
  namespace: $RESOURCE
spec:
  tls:
    - hosts:
      - ${RESOURCE}.randomuser.org
      secretName: ${RESOURCE}-tls
  rules: 
    - host: ${RESOURCE}.randomuser.org
      http:
        paths:
          - backend:
              serviceName: ${RESOURCE}
              servicePort: 8080
EOF
[ -f ${TEMPLATES}/deployment.yaml ] || cat <<- EOF > ${TEMPLATES}/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${RESOURCE}
  namespace: ${RESOURCE}
  labels:
    app: ${RESOURCE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${RESOURCE}
  template:
    metadata:
      labels:
        app: ${RESOURCE}
    spec:
      containers:
        - name: placeholder
          image: fedora:31
          command: ["/sbin/init"]
EOF
