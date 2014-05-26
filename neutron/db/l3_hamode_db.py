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
from neutron.db import agents_db
from neutron.db import db_base_plugin_v2 as basev2
from neutron.db import l3_db
from neutron.db import model_base
from neutron.db import models_v2
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

# TODO(safchain) move this to the extra table when the DVR patch will
# be merged
setattr(l3_db.Router, 'ha',
        sa.Column(sa.Boolean(), default=False, nullable=False))
setattr(l3_db.Router, 'ha_vr_id',
        sa.Column(sa.Integer()))


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

    state = sa.Column(sa.Enum('master', 'slave', name='l3_ha_states'),
                      default='slave')


class L3HARouterNetwork(model_base.BASEV2, models_v2.HasId,
                        models_v2.HasTenant):
    """Host HA Network for a tenant.

    One HA Network is used per tenant, all HA Router port are created
    on this type of network.
    """

    __tablename__ = 'ha_router_networks'

    network_id = sa.Column(sa.String(36),
                           sa.ForeignKey('networks.id', ondelete="CASCADE"),
                           nullable=False)


class L3_HA_NAT_db_mixin(l3_db.L3_NAT_db_mixin):
    """Mixin class to add the high availability mode."""

    # Register dict extend functions for ports and networks
    basev2.NeutronDbPluginV2.register_dict_extend_funcs(
        l3.ROUTERS, ['_extend_router_ha_dict'])

    def _extend_router_ha_dict(self, router_res, router_db):
        router_res[l3_ha.HA_INFO] = {'enabled': router_db.ha,
                                     'vr_id': router_db.ha_vr_id}

    def get_ha_network(self, context, tenant_id):
        return (context.session.query(L3HARouterNetwork).
                filter(L3HARouterNetwork.tenant_id == tenant_id).
                first())

    def _set_vr_id(self, context, router):
        query = context.session.query(l3_db.Router).filter(
            l3_db.Router.tenant_id == router.tenant_id)

        allocated_vr_ids = set([r['ha_vr_id'] for r in query])
        available_vr_ids = VR_ID_RANGE - allocated_vr_ids
        if not available_vr_ids:
            raise l3_ha.NoVRIDAvailable()
        router.ha_vr_id = available_vr_ids.pop()

    def _create_ha_subnet(self, context, network):
        ha_cidr = cfg.CONF.l3_ha_net_cidr
        net = netaddr.IPNetwork(ha_cidr)

        if ('/' not in ha_cidr or net.network != net.ip):
            raise l3_ha.HANetworkCIDRNotValid(cidr=ha_cidr)

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
        return self._core_plugin.create_subnet(context, args)

    def _create_ha_network(self, context, tenant_id):
        session = context.session
        with session.begin(subtransactions=True):
            args = {'network':
                    {'name': "HA Router VRRP Network",
                     'tenant_id': '',
                     'shared': False,
                     'admin_state_up': True,
                     'status': constants.NET_STATUS_ACTIVE}}
            network = self._core_plugin.create_network(context, args)

            ha_network = L3HARouterNetwork(tenant_id=tenant_id,
                                           network_id=network['id'])
            subnet = self._create_ha_subnet(context, network)

            session.add(ha_network)

            return subnet

    def _get_number_of_agents(self, context):
        min_agents = cfg.CONF.min_l3_agents_per_router
        # TODO(safchain): use oslo.config types to check the validity
        # when it will be available
        if min_agents < 2:
            raise l3_ha.HAMinimumAgentsNumberNotValid()

        num_agents = len(self.get_l3_agents(context))
        max_agents = cfg.CONF.max_l3_agents_per_router
        if max_agents:
            if max_agents > num_agents:
                LOG.warn(_("Number of available agents lower than "
                           "max_l3_agents_per_router. L3 agents "
                           "available: %s"), num_agents)
            else:
                num_agents = max_agents

        if num_agents < min_agents:
            raise l3_ha.HANotEnoughAvailableAgents()

        return num_agents

    def _create_ha_interfaces(self, context, router):
        ha_network = self.get_ha_network(context, router.tenant_id)
        if ha_network:
            subnets = self._core_plugin._get_subnets_by_network(
                context, ha_network.network_id)
            if not subnets:
                subnet = self._create_ha_subnet(context, ha_network)
            else:
                subnet = subnets[0]
        else:
            subnet = self._create_ha_network(context,
                                             router.tenant_id)

        router_id = router.id
        num_agents = self._get_number_of_agents(context)

        for index in range(num_agents):
            device_name = 'HA Router VRRP port %s' % index

            port = self._core_plugin.create_port(context, {
                'port':
                {'tenant_id': '',
                 'network_id': subnet['network_id'],
                 'fixed_ips': attributes.ATTR_NOT_SPECIFIED,
                 'mac_address': attributes.ATTR_NOT_SPECIFIED,
                 'admin_state_up': True,
                 'device_id': router_id,
                 'device_owner': constants.DEVICE_OWNER_ROUTER_HA_INTF,
                 'name': device_name}})

            portbinding = L3HARouterAgentPortBinding(port_id=port['id'],
                                                     router_id=router_id)
            context.session.add(portbinding)

    def _delete_ha_interfaces(self, context, router):
            ctx = context.elevated()
            device_filter = {'device_id': [router.id],
                             'device_owner':
                             [constants.DEVICE_OWNER_ROUTER_HA_INTF]}
            ports = self._core_plugin.get_ports(ctx, filters=device_filter)
            for port in ports:
                self._core_plugin._delete_port(ctx, port['id'])

    def _notify_ha_interfaces_updated(self, context, router_id, command):
        self.l3_rpc_notifier.routers_updated(context, [router_id], command)

    def _add_ha(self, context, router):
        self._create_ha_interfaces(context.elevated(), router)
        self._set_vr_id(context, router)

    def _remove_ha(self, context, router):
        self._delete_ha_interfaces(context, router)
        router.ha_vr_id = None

    def _create_router_db(self, context, router, tenant_id, gw_info):
        notify = False
        with context.session.begin(subtransactions=True):
            router_db = super(L3_HA_NAT_db_mixin, self)._create_router_db(
                context, router, tenant_id, gw_info)

            if _is_ha_router(router):
                router_db['ha'] = True
                self._add_ha(context, router_db)
                notify = True

        if notify:
            self._notify_ha_interfaces_updated(context, router_db.id,
                                               'add_ha_interfaces')
        return router_db

    def _update_router_db(self, context, router_id, data, gw_info):
        notify_command = None
        ha = data.pop('ha')
        with context.session.begin(subtransactions=True):
            router_db = super(L3_HA_NAT_db_mixin, self)._update_router_db(
                context, router_id, data, gw_info)

            if router_db.ha and not ha:
                self._remove_ha(context, router_db)
                notify_command = 'del_ha_interfaces'
                router_db.ha = ha
            elif not router_db.ha and ha:
                self._add_ha(context, router_db)
                notify_command = 'add_ha_interfaces'
                router_db.ha = ha

        if notify_command:
            self._notify_ha_interfaces_updated(context, router_db.id,
                                               notify_command)
        return router_db

    def update_router_state(self, context, router_id, state, host=None):
        with context.session.begin(subtransactions=True):
            bindings = self.get_ha_router_port_binding(context, [router_id],
                                                       host=host)
            for binding in bindings:
                binding.update({'state': state})

    def delete_router(self, context, id):
        with context.session.begin(subtransactions=True):
            router = self._get_router(context, id)
            if _is_ha_router(router):
                ctx = context.elevated()
                device_filter = {'device_id': [id],
                                 'device_owner':
                                 [constants.DEVICE_OWNER_ROUTER_HA_INTF]}
                ports = self._core_plugin.get_ports(ctx, filters=device_filter)
                for port in ports:
                    self._core_plugin._delete_port(ctx, port['id'])

            return super(L3_HA_NAT_db_mixin, self).delete_router(context, id)

    def get_ha_router_port_binding(self, context, router_ids, host=None):
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

        return res

    def _process_sync_ha_data(self, context, routers, host):
        routers_dict = {}
        for router in routers:
            routers_dict[router['id']] = router

        bindings = self.get_ha_router_port_binding(context,
                                                   routers_dict.keys())

        ports = []
        for binding in bindings:
            port_dict = self._make_port_binding_dict(binding)
            ports.append(port_dict)

            if not host or (binding.agent and (binding.agent.host == host)):
                router = routers_dict.get(binding.router_id)
                router_ifaces = router.get(constants.HA_INTERFACE_KEY, [])
                router_ifaces.append(port_dict)
                router[constants.HA_INTERFACE_KEY] = router_ifaces
                router[constants.HA_ROUTER_STATE_KEY] = binding.state

        for router in routers_dict.values():
            interfaces = router.get(constants.HA_INTERFACE_KEY)
            if interfaces:
                self._populate_subnet_for_ports(context, interfaces)

        return routers_dict.values()


    def get_sync_data(self, context, host, router_ids=None, active=None):
        sync_data = super(L3_HA_NAT_db_mixin, self).get_sync_data(context,
                                                                  router_ids,
                                                                  active)
        return self._process_sync_ha_data(context, sync_data, host)


def _is_ha_router(router):
    """Return True if router to be handled is ha."""
    try:
        requested_router_type = router.ha
    except AttributeError:
        requested_router_type = router.get('ha')
    if attributes.is_attr_set(requested_router_type):
        return requested_router_type
    return cfg.CONF.l3_ha
