# neutron-load-generator
Neutron Load Generator

This python script can be used to:
* Populate OpenStack with projects, networks, subnets and routers.
* Generate excessive load on the Neutron API
Before running change the *clouds.yaml* file to match your OpenStack env settings. Check the [openstackclient configuration](https://docs.openstack.org/python-openstackclient/latest/configuration/index.html) for details.

```commandline
$ python nlg.py --help
usage: nlg.py [-h] [-p PROJECTS] [-n NETWORKS] [-t THREADS] [-c] [-q] [-d] domain-id ext-net-id

This script generates load/creates resources on the OpenStack cloud. It creates projects, next networks and subnets in those projects and finally a router per project.It attaches each project's subnet to
the corresponding router.

positional arguments:
  domain-id             Target domain id where all the projects are created.
  ext-net-id            External network id to attach to each router.

optional arguments:
  -h, --help            show this help message and exit
  -p PROJECTS, --projects PROJECTS
                        Number of projects to create. Default 5.
  -n NETWORKS, --networks NETWORKS
                        Maximum number of networks per project. The actual number will be randomly generated from range between 2 and this value for each for the projects. Default 10.
  -t THREADS, --threads THREADS
                        Number of threads to run when creating or deleting resources. Default 10.
  -c, --create-resources
                        By default the script will run in the cleanup mode. It will not create any resources unless this flag is set. Default False.
  -q, --force-quota     Set unlimited quota for the created projects. Default False.
  -d, --debug           Enable debug logging. Default False.
```

### Resource cleanup example

```commandline
python nlg.py 148c5b14aec449e3b64135794e5061ce cd275f37-deec-4f0f-9ad3-4816ca52678e
```

### Resource creation example

```commandline
python nlg.py 148c5b14aec449e3b64135794e5061ce cd275f37-deec-4f0f-9ad3-4816ca52678e -q -t 8 -c
```

