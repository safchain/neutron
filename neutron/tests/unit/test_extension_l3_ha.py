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

import contextlib
import mock
from oslo.config import cfg

from neutron.api.v2 import attributes
from neutron.common import constants as l3_constants
from neutron import context
from neutron.db import api as qdbapi
from neutron.db import db_base_plugin_v2
from neutron.db import l3_agentschedulers_db
from neutron.db import l3_hamode_db
from neutron.db import l3_hamode_rpc
from neutron.db import model_base
from neutron.extensions import l3
from neutron.extensions import l3_ext_ha_mode
from neutron import manager
from neutron.openstack.common import uuidutils
from neutron.plugins.common import constants as service_constants
from neutron.tests.unit import test_api_v2
from neutron.tests.unit import test_l3_plugin


_uuid = uuidutils.generate_uuid
_get_path = test_api_v2._get_path


class L3HATestExtensionManager(object):

    def get_resources(self):
        attr_map = attributes.RESOURCE_ATTRIBUTE_MAP
        attr_map.update(l3.RESOURCE_ATTRIBUTE_MAP)
        extended_attrs = l3_ext_ha_mode.EXTENDED_ATTRIBUTES_2_0
        for resource, resource_attrs in extended_attrs.items():
            if attr_map.get(resource):
                attr_map[resource].update(resource_attrs)
            else:
                attr_map[resource] = resource_attrs
        return l3.L3.get_resources()

    def get_actions(self):
        return []

    def get_request_extensions(self):
        return []


class TestL3HAIntPlugin(test_l3_plugin.TestL3NatBasePlugin,
                        l3_hamode_db.L3_HA_NAT_db_mixin,
                        l3_agentschedulers_db.L3AgentSchedulerDbMixin):

    supported_extension_aliases = ["external-net", "router", "l3-ha",
                                   "l3_agent_scheduler"]


class TestL3HAServicePlugin(db_base_plugin_v2.CommonDbMixin,
                            l3_hamode_db.L3_HA_NAT_db_mixin,
                            l3_agentschedulers_db.L3AgentSchedulerDbMixin):

    supported_extension_aliases = ["router", "l3-ha",
                                   "l3_agent_scheduler"]

    def __init__(self):
        qdbapi.register_models(base=model_base.BASEV2)

    def get_plugin_type(self):
        return service_constants.L3_ROUTER_NAT

    def get_plugin_description(self):
        return "L3 Routing Service Plugin for testing"


class L3HATestCaseMixin(test_l3_plugin.L3NatTestCaseMixin):
    fmt = 'json'

    @contextlib.contextmanager
    def ha_router(self, name='router1', admin_state_up=True,
                  fmt=None, tenant_id=_uuid(),
                  external_gateway_info=None, set_context=False,
                  ha=True, **kwargs):

        if 'arg_list' in kwargs:
            arg_list = kwargs['arg_list'] + ('ha',)
        else:
            arg_list = ('ha',)

        kwargs['ha'] = ha
        kwargs['arg_list'] = arg_list

        router = self._make_router(fmt or self.fmt, tenant_id, name,
                                   admin_state_up, external_gateway_info,
                                   set_context, **kwargs)
        yield router
        self._delete('routers', router['router']['id'])


class L3HATestCaseBase(L3HATestCaseMixin):

    def test_ha_router_create(self):
        name = 'router1'
        tenant_id = _uuid()
        expected_value = [('name', name), ('tenant_id', tenant_id),
                          ('admin_state_up', True), ('status', 'ACTIVE'),
                          ('external_gateway_info', None)]

        with self.ha_router(name=name, admin_state_up=True,
                            tenant_id=tenant_id) as r1:
            for k, v in expected_value:
                self.assertEqual(r1['router'][k], v)

            body = self._show('routers', r1['router']['id'])
            ha = body['router']['ha']
            self.assertTrue(ha['enabled'])
            ha_vr_id1 = ha['vr_id']

            with self.ha_router(name=name, admin_state_up=True,
                                tenant_id=tenant_id) as r2:
                body = self._show('routers', r2['router']['id'])
                ha = body['router']['ha']
                self.assertTrue(ha['enabled'])
                ha_vr_id2 = ha['vr_id']

                self.assertEqual(ha_vr_id1 + 1, ha_vr_id2)

    def test_no_ha_router_create(self):
        with self.ha_router(ha=False) as r:
            body = self._show('routers', r['router']['id'])
            ha = body['router']['ha']
            self.assertFalse(ha['enabled'])

    def test_no_ha_router_create_with_ha_conf_enabled(self):
        cfg.CONF.set_override('l3_ha', True)

        with self.ha_router(ha=False) as r:
            body = self._show('routers', r['router']['id'])
            ha = body['router']['ha']
            self.assertTrue(ha['enabled'])

    def test_migration_from_ha(self):
        with self.ha_router() as r:
            body = self._show('routers', r['router']['id'])
            ha = body['router']['ha']
            self.assertTrue(ha['enabled'])

            self._update('routers', r['router']['id'],
                         {'router': {'ha': False}})
            body = self._show('routers', r['router']['id'])
            ha = body['router']['ha']
            self.assertFalse(ha['enabled'])

    def test_migration_to_ha(self):
        with self.ha_router(ha=False) as r:
            body = self._show('routers', r['router']['id'])
            ha = body['router']['ha']
            self.assertFalse(ha['enabled'])

            self._update('routers', r['router']['id'],
                         {'router': {'ha': True}})
            body = self._show('routers', r['router']['id'])
            ha = body['router']['ha']
            self.assertTrue(ha['enabled'])

    def test_ha_network_tenant_visibility(self):
        tenant_id1 = _uuid()
        with self.ha_router(name='router1', admin_state_up=True,
                            tenant_id=tenant_id1):
            ctx = context.Context('', tenant_id1)
            result = self._list('networks', neutron_context=ctx)
            networks = result['networks']
            self.assertEqual(0, len(networks))

            ctx = context.get_admin_context()
            result = self._list('networks', neutron_context=ctx)
            networks = result['networks']
            self.assertEqual(1, len(networks))

    def test_router_create_one_ha_network_per_tenant(self):
        ctx = context.get_admin_context()

        tenant_id1, tenant_id2 = _uuid(), _uuid()
        with self.ha_router(name='router1', admin_state_up=True,
                            tenant_id=tenant_id1):
            result = self._list('networks', neutron_context=ctx)
            networks = result['networks']
            self.assertEqual(1, len(networks))
            self.assertEqual('HA Router VRRP Network', networks[0]['name'])

            net_id1 = networks[0]['id']
            self.assertEqual(1, len(networks[0]['subnets']))

            subnet_id1 = networks[0]['subnets'][0]
            self.assertIsNotNone(subnet_id1)

            # create a new router for the same tenant in order to check
            # whether the ha network is reused
            with self.ha_router(name='router2', admin_state_up=True,
                                tenant_id=tenant_id1):
                result = self._list('networks', neutron_context=ctx)
                networks = result['networks']
                self.assertEqual(1, len(networks))
                self.assertEqual('HA Router VRRP Network', networks[0]['name'])

                net_id2 = networks[0]['id']
                self.assertEqual(1, len(networks[0]['subnets']))

                subnet_id2 = networks[0]['subnets'][0]
                self.assertIsNotNone(subnet_id2)

                self.assertEqual(net_id1, net_id2)

            # create a new router for a new tenant in order to check
            # whether a new ha network is created
            with self.ha_router(name='router1', admin_state_up=True,
                                tenant_id=tenant_id2):
                result = self._list('networks', neutron_context=ctx)
                networks = result['networks']
                self.assertEqual(2, len(networks))

                self.assertEqual('HA Router VRRP Network', networks[0]['name'])
                self.assertEqual('HA Router VRRP Network', networks[1]['name'])


class L3HAAgentDbTestCaseBase(L3HATestCaseMixin):

    def test_l3_agent_routers_query_interfaces(self):
        with self.ha_router():
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            self.assertEqual(1, len(routers))
            router = routers[0]

            self.assertIsNotNone(router.get('ha'))

            interfaces = router.get(l3_constants.HA_INTERFACE_KEY)
            self.assertEqual(2, len(interfaces))

            for interface in interfaces:
                self.assertEqual(l3_constants.DEVICE_OWNER_ROUTER_HA_INTF,
                                 interface['device_owner'])
                self.assertEqual('169.254.0.0/16',
                                 interface['subnet']['cidr'])
                self.assertIsNone(interface.get('agent_id'))
                self.assertIsNone(interface.get('agent_host'))

    def test_migration_from_ha(self):
        with self.ha_router():
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            router = routers[0]
            interfaces = router.get(l3_constants.HA_INTERFACE_KEY)
            self.assertEqual(2, len(interfaces))

            self._update('routers', router['id'],
                         {'router': {'ha': False}})
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            router = routers[0]
            interfaces = router.get(l3_constants.HA_INTERFACE_KEY)
            self.assertFalse(interfaces)

    def test_migration_to_ha(self):
        with self.ha_router(ha=False):
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            router = routers[0]
            interfaces = router.get(l3_constants.HA_INTERFACE_KEY)
            self.assertFalse(interfaces)

            self._update('routers', router['id'],
                         {'router': {'ha': True}})
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            router = routers[0]
            interfaces = router.get(l3_constants.HA_INTERFACE_KEY)
            self.assertEqual(2, len(interfaces))

    def test_vr_id_two_routers(self):
        with contextlib.nested(self.ha_router(),
                               self.ha_router()) as (r1, r2):
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            self.assertEqual(2, len(routers))
            self.assertNotEqual(routers[0]['ha']['vr_id'],
                                routers[1]['ha']['vr_id'])

    def test_vr_id_two_tenants(self):
        with contextlib.nested(self.ha_router(),
                               self.ha_router(tenant_id="alt_id")) as (r1, r2):
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            self.assertEqual(2, len(routers))
            self.assertEqual(routers[0]['ha']['vr_id'],
                             routers[1]['ha']['vr_id'])

    def _test_notify_op_agent(self, target_func, *args):
        l3_rpc_agent_api_str = (
            'neutron.api.rpc.agentnotifiers.l3_rpc_agent_api.L3AgentNotifyAPI')
        plugin = manager.NeutronManager.get_service_plugins()[
            service_constants.L3_ROUTER_NAT]
        oldNotify = plugin.l3_rpc_notifier
        try:
            with mock.patch(l3_rpc_agent_api_str) as notifyApi:
                plugin.l3_rpc_notifier = notifyApi
                kargs = [item for item in args]
                kargs.append(notifyApi)
                target_func(*kargs)
        except Exception:
            plugin.l3_rpc_notifier = oldNotify
            raise
        else:
            plugin.l3_rpc_notifier = oldNotify

    def _test_router_ha_op_agent(self, notifyApi):
        with self.ha_router():
            self.assertTrue(notifyApi.routers_updated.called)

    def test_router_ha_op_agent(self):
        self._test_notify_op_agent(self._test_router_ha_op_agent)

    def _test_router_ha_op_migration(self, notifyApi):
        with self.ha_router(ha=False) as r:
            self._update('routers', r['router']['id'],
                         {'router': {'ha': True}})
            self.assertTrue(notifyApi.routers_updated.called)

    def test_router_ha_op_migration(self):
        self._test_notify_op_agent(self._test_router_ha_op_migration)

    def test_one_ha_router_one_not(self):
        with self.ha_router(ha=False):
            with self.ha_router():
                routers = self.plugin.get_sync_data(
                    context.get_admin_context(), None)

                ha0 = routers[0]['ha']['enabled']
                ha1 = routers[1]['ha']['enabled']

                self.assertFalse(ha0 == ha1)

    def test_update_state(self):
        with self.ha_router() as r:
            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            state = routers[0].get(l3_constants.HA_ROUTER_STATE_KEY)
            self.assertEqual('slave', state)

            l3_rpc = l3_hamode_rpc.L3_HA_NAT_rpc_mixin()
            kwargs = {'router_id': r['router']['id'],
                      'state': 'master'}
            l3_rpc.update_router_state(context.get_admin_context(), **kwargs)

            routers = self.plugin.get_sync_data(
                context.get_admin_context(), None)
            state = routers[0].get(l3_constants.HA_ROUTER_STATE_KEY)
            self.assertEqual('master', state)


class L3HABaseMixin(object):

    def backup_resource_attribute_map(self):
        self._l3_attribute_map = {}
        for item in l3.RESOURCE_ATTRIBUTE_MAP:
            self._l3_attribute_map[item] = (
                l3.RESOURCE_ATTRIBUTE_MAP[item].copy())

    def restore_backup_attribute_map(self):
        l3.RESOURCE_ATTRIBUTE_MAP = self._l3_attribute_map


class L3HABaseForIntTests(L3HABaseMixin,
                          test_l3_plugin.L3BaseForIntTests):

    def setUp(self):
        plugin = 'neutron.tests.unit.test_extension_l3_ha.TestL3HAIntPlugin'

        self.backup_resource_attribute_map()
        ext_mgr = L3HATestExtensionManager()

        super(L3HABaseForIntTests, self).setUp(plugin=plugin, ext_mgr=ext_mgr)

        self.notify_ha_p = mock.patch('neutron.db.l3_hamode_db.'
                                      'L3_HA_NAT_db_mixin.'
                                      '_notify_ha_interfaces_updated')
        self.notify_ha = self.notify_ha_p.start()

        self.get_l3_agents_p = mock.patch('neutron.tests.unit.'
                                          'test_extension_l3_ha.'
                                          'TestL3HAIntPlugin.'
                                          'get_l3_agents',
                                          return_value=[1, 2, 3])
        self.get_l3_agents_p.start()

    def tearDown(self):
        self.notify_ha_p.stop()
        self.get_l3_agents_p.stop()
        self.restore_backup_attribute_map()
        super(L3HABaseForIntTests, self).tearDown()


class L3HABaseForSepTests(L3HABaseMixin,
                          test_l3_plugin.L3BaseForSepTests):

    def setUp(self):
        plugin = 'neutron.tests.unit.test_l3_plugin.TestNoL3NatPlugin'

        self.backup_resource_attribute_map()
        ext_mgr = L3HATestExtensionManager()

        l3_plugin = ('neutron.tests.unit.test_extension_l3_ha.'
                     'TestL3HAServicePlugin')

        service_plugins = {'l3_plugin_name': l3_plugin}

        super(L3HABaseForSepTests, self).setUp(
            plugin=plugin, ext_mgr=ext_mgr, service_plugins=service_plugins)

        self.notify_ha_p = mock.patch('neutron.db.l3_hamode_db.'
                                      'L3_HA_NAT_db_mixin.'
                                      '_notify_ha_interfaces_updated')
        self.notify_ha = self.notify_ha_p.start()

        self.get_l3_agents_p = mock.patch('neutron.tests.unit.'
                                          'test_extension_l3_ha.'
                                          'TestL3HAServicePlugin.'
                                          'get_l3_agents',
                                          return_value=[1, 2, 3])
        self.get_l3_agents_p.start()

    def tearDown(self):
        self.notify_ha_p.stop()
        self.get_l3_agents_p.stop()
        self.restore_backup_attribute_map()
        super(L3HABaseForSepTests, self).tearDown()


class L3HAAgentDbSepTestCase(L3HABaseMixin,
                             test_l3_plugin.L3BaseForSepTests,
                             L3HAAgentDbTestCaseBase):

    """Unit tests for methods called by the L3 agent for the
    case where separate service plugin implements L3 routing and the
    HA mode is enabled.
    """

    def setUp(self):
        cfg.CONF.set_override('max_l3_agents_per_router', 2)

        plugin = 'neutron.tests.unit.test_l3_plugin.TestNoL3NatPlugin'
        self.plugin = TestL3HAServicePlugin()

        self.backup_resource_attribute_map()
        ext_mgr = L3HATestExtensionManager()

        l3_plugin = ('neutron.tests.unit.test_extension_l3_ha.'
                     'TestL3HAServicePlugin')
        service_plugins = {'l3_plugin_name': l3_plugin}

        super(L3HAAgentDbSepTestCase, self).setUp(
            plugin=plugin, service_plugins=service_plugins, ext_mgr=ext_mgr)

        self.get_l3_agents_p = mock.patch('neutron.tests.unit.'
                                          'test_extension_l3_ha.'
                                          'TestL3HAServicePlugin.'
                                          'get_l3_agents',
                                          return_value=[1, 2, 3])
        self.get_l3_agents_p.start()

    def tearDown(self):
        self.get_l3_agents_p.stop()
        self.restore_backup_attribute_map()
        super(L3HAAgentDbSepTestCase, self).tearDown()


class L3HAAgentDbIntTestCase(L3HABaseMixin,
                             test_l3_plugin.L3BaseForIntTests,
                             L3HAAgentDbTestCaseBase):

    """Unit tests for methods called by the L3 agent for
    the case where core plugin implements L3 routing with HA.
    """

    def setUp(self):
        cfg.CONF.set_override('max_l3_agents_per_router', 2)

        plugin = 'neutron.tests.unit.test_extension_l3_ha.TestL3HAIntPlugin'
        self.plugin = TestL3HAIntPlugin()

        self.backup_resource_attribute_map()
        ext_mgr = L3HATestExtensionManager()

        super(L3HAAgentDbIntTestCase, self).setUp(plugin=plugin,
                                                  ext_mgr=ext_mgr)

        self.get_l3_agents_p = mock.patch('neutron.tests.unit.'
                                          'test_extension_l3_ha.'
                                          'TestL3HAIntPlugin.'
                                          'get_l3_agents',
                                          return_value=[1, 2, 3])
        self.get_l3_agents_p.start()

    def tearDown(self):
        self.get_l3_agents_p.stop()
        self.restore_backup_attribute_map()
        super(L3HAAgentDbIntTestCase, self).tearDown()


class L3HADBIntTestCase(L3HABaseForIntTests, L3HATestCaseBase):

    """Unit tests for core plugin with L3 routing integrated."""
    pass


class L3HADBSepTestCase(L3HABaseForSepTests, L3HATestCaseBase):

    """Unit tests for a separate L3 HA routing service plugin."""
    pass


class L3HADBIntTestCaseXML(L3HADBIntTestCase):
    fmt = 'xml'


class L3HADBSepTestCaseXML(L3HADBSepTestCase):
    fmt = 'xml'
