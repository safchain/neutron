# Copyright (C) 2014 eNovance SAS <licensing@enovance.com>
#
# Author: Sylvain Afchain <sylvain.afchain@enovance.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import netaddr
from oslo.config import cfg
import sqlalchemy as sa
from sqlalchemy import orm

from neutron.api.v2 import attributes
from neutron.common import constants
from neutron.common import exceptions
from neutron.db import agents_db
from neutron.db import db_base_plugin_v2 as basev2
from neutron.db import l3_db
from neutron.db import l3_gwmode_db
from neutron.db import models_v2
from neutron.db.models_v2 import model_base
from neutron.extensions import l3
from neutron.extensions import l3_ext_ha_mode as l3_ha
from neutron.openstack.common import log as logging

VR_ID_RANGE = set(range(1, 255))

LOG = logging.getLogger(__name__)

L3_HA_OPTS = [
    cfg.BoolOpt('l3_ha',
                default=False,
                help=_('Enable the HA mode of virtual routers')),
    cfg.IntOpt('max_l3_agents_per_router',
               default=2,
               help=_('Maximun number of agents on which a router will be '
                      'scheduled.')),
    cfg.IntOpt('min_l3_agents_per_router',
               default=2,
               help=_('Minimum number of agents on which a router will be '
                      'scheduled.')),
    cfg.StrOpt('l3_ha_net_cidr',
               default='169.254.0.0/16',
               help=_('Network address used for the l3 ha admin network.')),
]
cfg.CONF.register_opts(L3_HA_OPTS)

# Modify the Router Data Model adding the virtual router id
setattr(l3_db.Router, 'ha_vr_id',
        sa.Column(sa.Integer()))


class NoVRIDAvailable(exceptions.NeutronException):
    message = _("No more Virtual Router Identifier (VRID) available, "
                "the limit of number of HA Routers per tenant is 254 due to "
                "the size of the VRID field of the VRRP protocol.")


class HANetworkCIDRNotValid(exceptions.NeutronException):
    message = _("The HA Network cidr specified in the configuration file"
                " isn't valid; %(cidr)s")


class HANotEnoughAvailableAgents(exceptions.NeutronException):
    message = _("Not enough l3 agents availables to ensure HA.")


class HAMinimumAgentsNumberNotValid(exceptions.NeutronException):
    message = _("min_l3_agents_per_router config parameter is not valid, "
                "it has to be at least more thant 2 for HA.")


class L3HARouterAgentPortBinding(model_base.BASEV2):
    """Represent agent binding state of a ha router port.

    A HA Router has one HA port per agent on which it is spawned,
    This binding table stores which port is used for a HA router by an
    l3 agent.
    """

    __tablename__ = 'ha_router_agent_port_bindings'

    port_id = sa.Column(sa.String(36), sa.ForeignKey('ports.id',
                                                     ondelete='CASCADE'),
                        nullable=False, primary_key=True)
    port = orm.relationship(models_v2.Port)

    router_id = sa.Column(sa.String(36), sa.ForeignKey('routers.id',
                                                       ondelete='CASCADE'),
                          nullable=False)

    l3_agent_id = sa.Column(sa.String(36),
                            sa.ForeignKey("agents.id",
                                          ondelete='CASCADE'))
    agent = orm.relationship(agents_db.Agent)

    priority = sa.Column(sa.Integer, default=50)


class L3HARouterNetwork(model_base.BASEV2, models_v2.HasId,
                        models_v2.HasTenant):
    """Host HA Network for a tenant.

    One HA Network is used per tenant, all HA Router port are created
    on this type of network.
    """

    __tablename__ = 'ha_router_networks'

    network_id = sa.Column(sa.String(36),
                           sa.ForeignKey('networks.id', ondelete="CASCADE"),
                           nullable=False, unique=True)


class L3_HA_NAT_db_mixin(l3_gwmode_db.L3_NAT_db_mixin):
    """Mixin class to add the high availability mode."""

    # Register dict extend functions for ports and networks
    basev2.NeutronDbPluginV2.register_dict_extend_funcs(
        l3.ROUTERS, ['_extend_router_ha_dict'])

    def _extend_router_ha_dict(self, router_res, router_db):
        if router_db.ha_vr_id:
            router_res[l3_ha.HA_INFO] = router_db.ha_vr_id

    def get_ha_network(self, context, tenant_id):
        return (context.session.query(L3HARouterNetwork).
                filter(L3HARouterNetwork.tenant_id == tenant_id).
                first())

    def _set_vr_id(self, context, router_id):
        with context.session.begin(subtransactions=True):
            router = self._get_router(context, router_id)

            query = context.session.query(l3_db.Router).filter(
                l3_db.Router.tenant_id == router.tenant_id)

            allocated_vr_ids = set([r['ha_vr_id'] for r in query])
            available_vr_ids = VR_ID_RANGE - allocated_vr_ids
            if not available_vr_ids:
                raise NoVRIDAvailable()

            router.ha_vr_id = available_vr_ids.pop()

    def _get_or_create_ha_network_subnet(self, context, tenant_id):
        ha_cidr = cfg.CONF.l3_ha_net_cidr
        net = netaddr.IPNetwork(ha_cidr)

        if ('/' not in ha_cidr or net.network != net.ip):
            raise HANetworkCIDRNotValid(cidr=ha_cidr)

        session = context.session
        with session.begin(subtransactions=True):
            ha_network = self.get_ha_network(context, tenant_id)
            if ha_network:
                subnets = self._core_plugin._get_subnets_by_network(
                    context, ha_network.network_id)
                return subnets[0]

            args = {'network':
                    {'name': "HA Router VRRP Network",
                     'tenant_id': '',
                     'shared': False,
                     'admin_state_up': True,
                     'status': constants.NET_STATUS_ACTIVE}}
            network = self._core_plugin.create_network(context, args)

            args = {'subnet':
                    {'network_id': network['id'],
                     'tenant_id': '',
                     'name': 'HA Router VRRP Subnet',
                     'ip_version': 4,
                     'cidr': cfg.CONF.l3_ha_net_cidr,
                     'enable_dhcp': False,
                     'host_routes': attributes.ATTR_NOT_SPECIFIED,
                     'dns_nameservers': attributes.ATTR_NOT_SPECIFIED,
                     'allocation_pools': attributes.ATTR_NOT_SPECIFIED,
                     'gateway_ip': attributes.ATTR_NOT_SPECIFIED}}
            subnet = self._core_plugin.create_subnet(context, args)

            ha_network = L3HARouterNetwork(tenant_id=tenant_id,
                                           network_id=network['id'])
            session.add(ha_network)

        return subnet

    def _create_ha_interfaces(self, context, router):
        with context.session.begin(subtransactions=True):
            subnet = self._get_or_create_ha_network_subnet(context,
                                                           router['tenant_id'])
            router_id = router['id']

            min_agents = cfg.CONF.min_l3_agents_per_router
            # TODO(safchain): use oslo.config types to check the validity
            # when it will be available
            if min_agents < 2:
                raise HAMinimumAgentsNumberNotValid()

            num_agents = len(self.get_l3_agents(context))
            if num_agents < min_agents:
                raise HANotEnoughAvailableAgents()

            max_agents = cfg.CONF.max_l3_agents_per_router
            if max_agents:
                if max_agents > num_agents:
                    LOG.warn(_("Number of available agents lower than "
                               "max_l3_agents_per_router. L3 agents "
                               "available: %s"), num_agents)
                else:
                    num_agents = max_agents
            else:
                max_agents = num_agents

            if num_agents < min_agents:
                raise HANotEnoughAvailableAgents()

            for index in range(num_agents):
                device_name = 'HA Router VRRP port %s' % index

                port = self._core_plugin.create_port(context, {
                    'port':
                    {'tenant_id': '',
                     'network_id': subnet['network_id'],
                     'fixed_ips': attributes.ATTR_NOT_SPECIFIED,
                     'mac_address': attributes.ATTR_NOT_SPECIFIED,
                     'admin_state_up': True,
                     'device_id': router['id'],
                     'device_owner': constants.DEVICE_OWNER_ROUTER_HA_INTF,
                     'name': device_name}})

                portbinding = L3HARouterAgentPortBinding(port_id=port['id'],
                                                         router_id=router_id)
                context.session.add(portbinding)

    def _notify_ha_interface_created(self, context, router_id):
        self.l3_rpc_notifier.routers_updated(context, [router_id],
                                             'add_ha_interfaces')

    def _process_ha_router(self, context, router_dict):
            self._create_ha_interfaces(context.elevated(), router_dict)
            self._set_vr_id(context, router_dict['id'])
            self._notify_ha_interface_created(context, router_dict['id'])

    def create_router(self, context, router):
        with context.session.begin(subtransactions=True):
            router_dict = super(L3_HA_NAT_db_mixin,
                                self).create_router(context, router)

            if cfg.CONF.l3_ha:
                self._process_ha_router(context, router_dict)

        return router_dict

    def delete_router(self, context, id):
        if cfg.CONF.l3_ha:
            with context.session.begin(subtransactions=True):
                device_filter = {'device_id': [id],
                                 'device_owner':
                                 [constants.DEVICE_OWNER_ROUTER_HA_INTF]}
                ports = self._core_plugin.get_ports(context.elevated(),
                                                    filters=device_filter)
                for port in ports:
                    self._core_plugin._delete_port(context.elevated(),
                                                   port['id'])

        return super(L3_HA_NAT_db_mixin, self).delete_router(context, id)

    def get_router_port_binding(self, context, router_ids, host=None):
        query = context.session.query(
            L3HARouterAgentPortBinding).filter(
                L3HARouterAgentPortBinding.router_id.in_(router_ids))

        if host:
            query = query.join(agents_db.Agent).filter(
                agents_db.Agent.host == host)

        return query

    def _make_port_binding_dict(self, binding):
        port = binding.port

        res = {'id': port.id,
               'name': port.name,
               'network_id': port.network_id,
               'tenant_id': port.tenant_id,
               'mac_address': port.mac_address,
               'admin_state_up': port.admin_state_up,
               'status': port.status,
               'fixed_ips': [{'subnet_id': ip['subnet_id'],
                              'ip_address': ip['ip_address']}
                             for ip in port.fixed_ips],
               'device_id': port.device_id,
               'device_owner': port.device_owner}
        if binding.agent:
            res['agent_id'] = binding.agent.id
            res['agent_host'] = binding.agent.host
            res['priority'] = binding.priority

        return res

    def _process_sync_ha_data(self, context, routers, host):
        routers_dict = {}
        for router in routers:
            routers_dict[router['id']] = router

        bindings = self.get_router_port_binding(context, routers_dict.keys())

        ports = []
        for binding in bindings:
            port_dict = self._make_port_binding_dict(binding)
            ports.append(port_dict)

            if not host or (binding.agent and (binding.agent.host == host)):
                router = routers_dict.get(binding.router_id)
                router_ifaces = router.get(constants.HA_INTERFACE_KEY, [])
                router_ifaces.append(port_dict)
                router[constants.HA_INTERFACE_KEY] = router_ifaces

        for router in routers_dict.values():
            interfaces = router.get(constants.HA_INTERFACE_KEY)
            if interfaces:
                self._populate_subnet_for_ports(context, interfaces)

        return routers_dict.values()

    def get_sync_data(self, context, router_ids=None, active=None, host=None):
        sync_data = super(L3_HA_NAT_db_mixin, self).get_sync_data(context,
                                                                  router_ids,
                                                                  active)
        if not cfg.CONF.l3_ha:
            return sync_data

        return self._process_sync_ha_data(context, sync_data, host)
