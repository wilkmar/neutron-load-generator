import openstack
from openstack.cloud import OpenStackCloudException
import logging
import sys
import threading
import time
import uuid
import ipaddress

f_quota = True
no_routers = 20
no_networks_per_router = 10
no_cycles = 10
cleanup_only = True


def execution_time(func):
    def inner1(*args, **kwargs):
        start_time = time.time()
        _result = func(*args, **kwargs)
        finish_time = time.time()
        logging.info(f'{func.__name__}({args}) completed in '
                     f'{round(finish_time - start_time, 3)}s.')
        return _result

    return inner1


class Nlg:

    default_prefix = 'nlg-'
    default_cidr = '172.16.0.0/14'

    def __init__(self, debug=False, force_quota=False):
        logging.info(f'Debug set to: {debug}')
        openstack.enable_logging(debug=debug)
        self.force_quota = force_quota
        self.conn = openstack.connect()
        self.networks = []
        self.subnets = []
        self.routers = []
        self.refresh_resources()
        auth = self.conn.config.config['auth']
        self.domain = self.conn.get_domain(
            name_or_id=auth['project_domain_name'])
        self.project = self.conn.identity.find_project(
            auth['project_name'],
            domain_id=self.domain.id)
        self.quota = self.conn.get_network_quotas(self.project.id)
        logging.debug(f'{self.quota}')
        if force_quota:
            self.set_quota()
        self.cidrs = self.get_cidrs()

    @staticmethod
    def get_uuid():
        return str(uuid.uuid4())[:8:]

    @staticmethod
    def get_cidrs():
        l_cidrs = []
        _ns = list(ipaddress.ip_network(Nlg.default_cidr).
                   subnets(new_prefix=24))
        for ipv4net in _ns:
            l_cidrs.append(str(ipv4net))
        return set(l_cidrs)

    def refresh_resources(self):
        self.networks = self.list_resources('network')
        self.subnets = self.list_resources('subnet')
        self.routers = self.list_resources('router')
        logging.debug(f'Networks: {self.networks}')
        logging.debug(f'Subnets: {self.subnets}')
        logging.debug(f'Routers: {self.routers}')

    @execution_time
    def list_resources(self, resource, name_prefix=default_prefix):
        _result = []
        if resource == 'network':
            resources = self.conn.network.networks()
        elif resource == 'subnet':
            resources = self.conn.network.subnets()
        elif resource == 'router':
            resources = self.conn.network.routers()
        else:
            resources = []

        for resource in resources:
            if name_prefix and name_prefix in resource['name']:
                _result.append(resource)
        return _result

    def set_quota(self, quota=None):
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
        self.conn.set_network_quotas(self.project.id, **_q)

    def cleanup(self):
        logging.info('Deleting resources')
        try:
            for router in self.routers:
                ports = self.conn.list_router_interfaces(router)
                for port in ports:
                    self.conn.remove_router_interface(router, port_id=port.id)
                self._delete_router(router.id)
            for subnet in self.subnets:
                _cidr = subnet['cidr']
                self._delete_subnet(subnet.name)
                self.cidrs.add(_cidr)
            for network in self.networks:
                self._delete_network(network.name)

            self.set_quota(quota=self.quota)
        except OpenStackCloudException as err:
            logging.error(f'Cleanup failed: {err}')

    def gen_load(self, uid, networks_per_router=5):
        logging.info(f' T-{uid} creating resources')
        router = self._create_router(uid)
        self.create_networks(uid, networks_per_router, router)

    def create_networks(self, uid, count, router):
        for _ in range(count):
            network_name = self.default_prefix + uid + '-' + self.get_uuid()
            self._create_network(network_name)
            _s = self._create_subnet(network_name)
            self.conn.add_router_interface(router, _s.id)

    @execution_time
    def _create_router(self, uid):
        router_name = self.default_prefix + uid
        _r = self.conn.create_router(name=router_name)
        self.routers.append(_r)
        return _r

    @execution_time
    def _create_network(self, network_name):
        _n = self.conn.create_network(name=network_name)
        self.networks.append(_n)
        return _n

    @execution_time
    def _create_subnet(self, subnet_name):
        _s = self.conn.create_subnet(name=subnet_name,
                                     network_name_or_id=subnet_name,
                                     cidr=self.cidrs.pop())
        self.subnets.append(_s)
        return _s

    @execution_time
    def _delete_router(self, router_id):
        self.conn.delete_router(router_id)

    @execution_time
    def _delete_network(self, network_name):
        self.conn.delete_network(network_name)

    @execution_time
    def _delete_subnet(self, subnet_name):
        self.conn.delete_subnet(subnet_name)


class LoadRunner(threading.Thread):

    def __init__(self, unique_id, nlg_object):
        threading.Thread.__init__(self)
        self.name = 'T-' + unique_id
        self.uid = unique_id
        self.nlg = nlg_object
        self.creation_time = time.time()

    @execution_time
    def run(self):
        logging.info(f'Thread {self.name} started.')
        self.nlg.gen_load(self.uid, networks_per_router=no_networks_per_router)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("nlg.log"),
        logging.StreamHandler(sys.stdout)
    ]
)


@execution_time
def main():
    try:
        threads = []
        nlg = Nlg(force_quota=f_quota)
        if not cleanup_only:
            for _ in range(no_routers):
                router_id = Nlg.get_uuid()
                threads.append(LoadRunner(f'{router_id}', nlg))
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
    finally:
        nlg.cleanup()


main()

