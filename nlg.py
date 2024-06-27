import openstack
from openstack.cloud import OpenStackCloudException
import logging
import sys
import threading
import time
import uuid
import ipaddress
import argparse

DEFAULT_PREFIX = 'nlg-'
DEFAULT_CIDR = '172.16.0.0/14'


def execution_time(func):
    def inner1(*args, **kwargs):
        start_time = time.time()
        _result = func(*args, **kwargs)
        finish_time = time.time()
        logging.info(f'{func.__name__}({args}) completed in '
                     f'{round(finish_time - start_time, 3)}s.')
        return _result

    return inner1


class NlgProject:
    def __init__(self, project_id=None, project=None):
        self.quota = None
        self.project_id = project_id
        self.project = project
        self.networks = []
        self.subnets = []
        self.routers = []
        self.cidrs = self.get_cidrs()

    def get_networks(self):
        return self.networks

    def get_subnets(self):
        return self.subnets

    def get_routers(self):
        return self.routers

    def get_quota(self):
        return self.quota

    def set_quota(self, quota):
        self.quota = quota

    @staticmethod
    def get_cidrs():
        l_cidrs = []
        _ns = list(ipaddress.ip_network(DEFAULT_CIDR).
                   subnets(new_prefix=24))
        for ipv4net in _ns:
            l_cidrs.append(str(ipv4net))
        return set(l_cidrs)


class Nlg:
    def __init__(self, domain_id, ext_net_id, cleanup=True, force_quota=True,
                 debug=False):
        logging.info(f'Debug set to: {debug}')
        openstack.enable_logging(debug=debug)
        self.force_cleanup = cleanup
        self.force_quota = force_quota
        self.target_domain_id = domain_id
        self.external_network_id = ext_net_id
        self.conn = openstack.connect()
        self.projects = []
        self.print_resource_counts()

    @staticmethod
    def get_uuid():
        return str(uuid.uuid4())[:8:]

    @execution_time
    def list_resources(self, name_prefix=DEFAULT_PREFIX):
        all_projects = self.conn.identity.projects(
            domain_id=self.target_domain_id)

        for project in all_projects:
            logging.debug(f'Project: {project.name}, ID: {project.id}')
            if name_prefix in project.name:
                nlgproject = NlgProject(project_id=project.id)
                networks = self.conn.network.networks(project_id=project.id)
                logging.debug(f'Networks: {networks}')
                nlgproject.get_networks().extend(networks)
                subnets = self.conn.network.subnets(project_id=project.id)
                logging.debug(f'Subnets: {subnets}')
                nlgproject.get_subnets().extend(subnets)
                routers = self.conn.network.routers(project_id=project.id)
                logging.debug(f'Routers: {routers}')
                nlgproject.get_routers().extend(routers)
                quota = self.conn.get_network_quotas(project.id)
                nlgproject.set_quota(quota)
                self.projects.append(nlgproject)

    def print_resource_counts(self):
        self.projects = []
        self.list_resources()
        logging.info("All projects: %s", len(self.projects))
        for project in self.projects:
            logging.info("############################################")
            logging.info(f"Project ID: {project.project_id}")
            logging.info("Routers: %s", len(project.get_routers()))
            logging.info("Networks: %s", len(project.get_networks()))
            logging.info("Subnets: %s", len(project.get_subnets()))

    def set_quota(self, project_id, quota=None):
        _q = dict()
        if quota:
            _q['networks'] = quota.networks
            _q['subnets'] = quota.subnets
            _q['routers'] = quota.routers
            _q['ports'] = quota.ports
        else:
            _q['networks'] = -1
            _q['subnets'] = -1
            _q['routers'] = -1
            _q['ports'] = -1
        logging.info(f'Setting quota: {_q}')
        self.conn.set_network_quotas(project_id, **_q)

    def cleanup(self, uid, project):
        logging.info(f' T-{uid} deleting resources')
        try:
            for router in project.get_routers():
                ports = self.conn.list_router_interfaces(router)
                for port in ports:
                    if port.device_owner != "network:router_gateway":
                        self.conn.remove_router_interface(router,
                                                          port_id=port.id)
                self._delete_router(router.id)
            for subnet in project.get_subnets():
                _cidr = subnet['cidr']
                self._delete_subnet(subnet.name)
                project.cidrs.add(_cidr)
            for network in project.get_networks():
                self._delete_network(network.name)
            self._delete_project(project.project_id)

        except OpenStackCloudException as err:
            logging.error(f'Cleanup failed: {err}')

    def gen_load(self, uid, networks_per_router=5):
        logging.info(f' T-{uid} creating resources')
        project_name = DEFAULT_PREFIX + uid
        nlgproject = self._create_project(project_name)
        self._create_router(uid, nlgproject)
        self.create_networks(uid, networks_per_router, nlgproject)

    def create_networks(self, uid, count, nlgproject):
        for _ in range(count):
            network_name = DEFAULT_PREFIX + uid + '-' + self.get_uuid()
            self._create_network(network_name, nlgproject)
            _s = self._create_subnet(network_name, nlgproject)
            # currently we only support a single router per tenant
            router = nlgproject.get_routers()[0]
            self.conn.add_router_interface(router, _s.id)

    @execution_time
    def _create_router(self, uid, nlgproject):
        router_name = DEFAULT_PREFIX + uid
        _r = self.conn.create_router(name=router_name,
                                     project_id=nlgproject.project.id,
                                     ext_gateway_net_id=self.external_network_id)
        nlgproject.get_routers().append(_r)
        return _r

    @execution_time
    def _create_network(self, network_name, nlgproject):
        _n = self.conn.create_network(name=network_name,
                                      project_id=nlgproject.project.id)
        nlgproject.get_networks().append(_n)
        return _n

    @execution_time
    def _create_subnet(self, subnet_name, nlgproject):
        _s = self.conn.create_subnet(name=subnet_name,
                                     network_name_or_id=subnet_name,
                                     cidr=nlgproject.cidrs.pop(),
                                     project_id=nlgproject.project.id)
        nlgproject.get_subnets().append(_s)
        return _s

    @execution_time
    def _create_project(self, project_name):
        _pr = self.conn.create_project(name=project_name,
                                       domain_id=self.target_domain_id)
        nlg_project = NlgProject(project_id=_pr.id, project=_pr)
        self.projects.append(nlg_project)
        if self.force_quota:
            self.set_quota(nlg_project.project.id)
        return nlg_project

    @execution_time
    def _delete_router(self, router_id):
        self.conn.delete_router(router_id)

    @execution_time
    def _delete_network(self, network_name):
        self.conn.delete_network(network_name)

    @execution_time
    def _delete_subnet(self, subnet_name):
        self.conn.delete_subnet(subnet_name)

    @execution_time
    def _delete_project(self, project_id):
        self.conn.delete_project(project_id, domain_id=self.target_domain_id)


class CreationRunner(threading.Thread):

    def __init__(self, unique_id, nlg_object, **kwargs):
        threading.Thread.__init__(self)
        self.name = 'T-' + unique_id
        self.uid = unique_id
        self.nlg = nlg_object
        self.max_networks = kwargs['max_networks']
        self.creation_time = time.time()

    @execution_time
    def run(self):
        logging.info(f'Thread {self.name} started. No of networks: '
                     f'{self.max_networks}')
        self.nlg.gen_load(self.uid, networks_per_router=self.max_networks)


class CleanupRunner(threading.Thread):

    def __init__(self, unique_id, nlg_object, **kwargs):
        threading.Thread.__init__(self)
        self.name = 'T-' + unique_id
        self.uid = unique_id
        self.nlg = nlg_object
        self.creation_time = time.time()
        idx = kwargs['idx']
        self.project = self.nlg.projects[idx]

    @execution_time
    def run(self):
        logging.info(f'Thread {self.name} started. Cleanup of project: '
                     f'{self.project.project_id}')

        self.nlg.cleanup(self.uid, self.project)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("nlg.log"),
        logging.StreamHandler(sys.stdout)
    ]
)


def threads_control(threads_count,
                    projects_count,
                    runner_class,
                    *runner_args,
                    **runner_kwargs):
    idx = 0
    while idx < projects_count:
        threads = []
        _thread_count = min(threads_count,
                            projects_count - idx)
        for _ in range(_thread_count):
            start_time = time.time()
            uid = Nlg.get_uuid()
            runner_obj = runner_class(f'{uid}',
                                      *runner_args,
                                      **runner_kwargs,
                                      idx=idx)
            threads.append(runner_obj)
            idx += 1
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        finish_time = time.time()
        logging.info(f'Batch of {_thread_count} threads completed in '
                     f'{round(finish_time - start_time, 3)}s. '
                     f'Total threads completed: {idx}')


@execution_time
def main():
    parser = argparse.ArgumentParser(
        description="This script generates "
                    "load/creates resources on the OpenStack cloud. "
                    "It creates projects, next networks and subnets in those "
                    "projects and finally a router per project."
                    "It attaches each project's subnet to the corresponding"
                    "router.")
    parser.add_argument("domain_id", metavar="domain-id",
                        help="Target domain id where all the "
                             "projects are created.",
                        type=str)
    parser.add_argument("ext_net_id", metavar="ext-net-id",
                        help="External network id to attach "
                             "to each router.",
                        type=str)
    parser.add_argument("-p", "--projects",
                        help="Number of projects to create. Default 5.",
                        type=int, default=5)
    parser.add_argument("-n", "--networks",
                        help="Maximum number of networks per project. "
                             "Default 10.",
                        type=int, default=10)
    parser.add_argument("-t", "--threads",
                        help="Number of threads to run when creating or "
                             "deleting resources. Default 10.",
                        type=int, default=10)
    parser.add_argument("-c", "--create-resources",
                        help="By default the script will run in the cleanup "
                             "mode. It will not create any resources unless "
                             "this flag is set. Default False.",
                        action="store_true")
    parser.add_argument("-q", "--force-quota",
                        help="Set unlimited quota for the created projects. "
                             "Default False.",
                        action="store_true")
    parser.add_argument("-d", "--debug",
                        help="Enable debug logging. Default False.",
                        action="store_true")
    args = parser.parse_args()
    domain_id = args.domain_id
    ext_net_id = args.ext_net_id
    f_cleanup = not args.create_resources
    f_quota = args.force_quota
    projects_count = args.projects
    networks_count = args.networks
    threads_count = args.threads
    logging.info(f'Target domain id: {domain_id}')
    logging.info(f'External network id: {ext_net_id}')
    logging.info(f'Force cleanup set to: {f_cleanup}')
    logging.info(f'Force quota set to: {f_quota}')
    logging.info(f'Projects: {projects_count}')
    logging.info(f'Networks: {networks_count}')
    logging.info(f'Threads: {threads_count}')
    nlg = Nlg(domain_id,
              ext_net_id,
              cleanup=f_cleanup,
              force_quota=f_quota,
              debug=args.debug)
    no_projects = len(nlg.projects)
    if f_cleanup:
        if no_projects == 0:
            logging.info("No projects found, nothing to clean up.")
        else:
            logging.info("Performing cleanup only...")
            threads_control(
                threads_count,
                no_projects,
                CleanupRunner,
                nlg)
    else:
        if no_projects > 0:
            logging.info("Some resources already exist. "
                         "Are you sure that you want to proceed "
                         "without cleaning them first? Type 'yes' if you "
                         "want to proceed. Any other key to exit.")
            ans = input()
            if ans.lower() != "yes":
                logging.info("Exiting...")
                return
        threads_control(threads_count,
                        projects_count,
                        CreationRunner,
                        nlg,
                        max_networks=networks_count)
    nlg.print_resource_counts()


main()
