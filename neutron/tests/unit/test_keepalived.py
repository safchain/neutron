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

from neutron.agent.linux import keepalived
from neutron.tests import base

# Keepalived user guide:
# http://www.keepalived.org/pdf/UserGuide.pdf
CONFIG_EXPECTED = ['global_defs {',
                   '\tnotification_email {',
                   '\t\ttest@test.test',
                   '\t}',
                   '\tnotification_email_from from@from.from',
                   '\tsmtp_server smtp.smtp.com',
                   '\tsmtp_connect_timeout 300',
                   '}',
                   'vrrp_sync_group group1 {',
                   '\tgroup {',
                   '\t\tinstance1',
                   '\t}',
                   '\tnotify_master "/tmp/script.sh"',
                   '}',
                   'vrrp_sync_group group2 {',
                   '\tgroup {',
                   '\t\tinstance2',
                   '\t}',
                   '}',
                   'vrrp_instance instance2 {',
                   '\tstate MASTER',
                   '\tinterface eth4',
                   '\tvirtual_router_id 44',
                   '\tpriority 50',
                   '\tmcast_src_ip 224.0.0.1',
                   '\ttrack_interface {',
                   '\t\teth4',
                   '\t}',
                   '\tvirtual_ipaddress {',
                   '\t\t192.168.3.0/24 dev eth6',
                   '\t\t192.168.4.0/24 dev eth7',
                   '\t}',
                   '\tvirtual_ipaddress_excluded {',
                   '\t\t192.168.55.0/24 dev eth10',
                   '\t}',
                   '}',
                   'vrrp_instance instance1 {',
                   '\tstate MASTER',
                   '\tinterface eth0',
                   '\tvirtual_router_id 33',
                   '\tpriority 50',
                   '\tadvert_int 5',
                   '\tauthentication {',
                   '\t\tauth_type AH',
                   '\t\tauth_pass pass123',
                   '\t}',
                   '\ttrack_interface {',
                   '\t\teth0',
                   '\t}',
                   '\tvirtual_ipaddress {',
                   '\t\t192.168.1.0/24 dev eth1',
                   '\t\t192.168.2.0/24 dev eth2',
                   '\t\t192.168.3.0/24',
                   '\t}',
                   '\tvirtual_ipaddress_excluded {',
                   '\t\t192.168.55.0/24 dev eth10',
                   '\t}',
                   '\tvirtual_routes {',
                   '\t\t0.0.0.0/0 via 192.168.1.1 dev eth1',
                   '\t}',
                   '}']


class KeepalivedConfBaseMixin(object):

    def _get_config(self):
        config = keepalived.KeepalivedConf()

        global_defs = keepalived.KeepalivedGlobaldefs()
        global_defs.add_notification_email('test@test.test')
        global_defs.set_email_from('from@from.from')
        global_defs.set_smtp_server('smtp.smtp.com', 300)

        config.set_global_defs(global_defs)

        group1 = keepalived.KeepalivedGroup('group1')
        group2 = keepalived.KeepalivedGroup('group2')

        group1.set_notify('master', '/tmp/script.sh')

        instance1 = keepalived.KeepalivedInstance('instance1', 'MASTER',
                                                  'eth0', 33, advert_int=5)
        instance1.set_authentication('AH', 'pass123')
        instance1.track_interfaces.append("eth0")

        vip_address1 = keepalived.KeepalivedVipAddress('192.168.1.0/24',
                                                       'eth1')

        vip_address2 = keepalived.KeepalivedVipAddress('192.168.2.0/24',
                                                       'eth2')

        vip_address3 = keepalived.KeepalivedVipAddress('192.168.3.0/24')

        vip_address_ex = keepalived.KeepalivedVipAddress('192.168.55.0/24',
                                                         'eth10')

        instance1.vip_addresses.append(vip_address1)
        instance1.vip_addresses.append(vip_address2)
        instance1.vip_addresses.append(vip_address3)
        instance1.vip_addresses_excluded.append(vip_address_ex)

        virtual_route = keepalived.KeepalivedVirtualRoute("0.0.0.0/0",
                                                          "192.168.1.1",
                                                          "eth1")
        instance1.virtual_routes.append(virtual_route)

        group1.add_instance(instance1)

        instance2 = keepalived.KeepalivedInstance('instance2', 'MASTER',
                                                  'eth4', 44,
                                                  mcast_src_ip='224.0.0.1')
        instance2.track_interfaces.append("eth4")

        vip_address1 = keepalived.KeepalivedVipAddress('192.168.3.0/24',
                                                       'eth6')

        vip_address2 = keepalived.KeepalivedVipAddress('192.168.4.0/24',
                                                       'eth7')

        instance2.vip_addresses.append(vip_address1)
        instance2.vip_addresses.append(vip_address2)
        instance2.vip_addresses_excluded.append(vip_address_ex)

        group2.add_instance(instance2)

        config.add_group(group1)
        config.add_instance(instance1)
        config.add_group(group2)
        config.add_instance(instance2)

        return config


class KeepalivedConfTestCase(base.BaseTestCase,
                             KeepalivedConfBaseMixin):

    def test_config_generation(self):
        config = self._get_config()
        self.assertEqual(CONFIG_EXPECTED, config.build_config())

    def test_config_with_reset(self):
        config = self._get_config()
        self.assertEqual(CONFIG_EXPECTED, config.build_config())

        config.reset()
        self.assertEqual([], config.build_config())

    def test_state_exception(self):
        group = keepalived.KeepalivedGroup('group2')

        self.assertRaises(keepalived.InvalidNotifyStateException,
                          group.set_notify,
                          'aaa', '/tmp/script.sh')

        self.assertRaises(keepalived.InvalidInstanceStateException,
                          keepalived.KeepalivedInstance,
                          'instance1', 'aaaa', 'eth0', 33)

    def test_remove_adresses_by_interface(self):
        config = self._get_config()
        instance = config.get_instance('instance1')
        instance.remove_vips_vroutes_by_interface('eth2')
        instance.remove_vips_vroutes_by_interface('eth10')

        expected = ['global_defs {',
                    '\tnotification_email {',
                    '\t\ttest@test.test',
                    '\t}',
                    '\tnotification_email_from from@from.from',
                    '\tsmtp_server smtp.smtp.com',
                    '\tsmtp_connect_timeout 300',
                    '}',
                    'vrrp_sync_group group1 {',
                    '\tgroup {',
                    '\t\tinstance1',
                    '\t}',
                    '\tnotify_master "/tmp/script.sh"',
                    '}',
                    'vrrp_sync_group group2 {',
                    '\tgroup {',
                    '\t\tinstance2',
                    '\t}',
                    '}',
                    'vrrp_instance instance2 {',
                    '\tstate MASTER',
                    '\tinterface eth4',
                    '\tvirtual_router_id 44',
                    '\tpriority 50',
                    '\tmcast_src_ip 224.0.0.1',
                    '\ttrack_interface {',
                    '\t\teth4',
                    '\t}',
                    '\tvirtual_ipaddress {',
                    '\t\t192.168.3.0/24 dev eth6',
                    '\t\t192.168.4.0/24 dev eth7',
                    '\t}',
                    '\tvirtual_ipaddress_excluded {',
                    '\t\t192.168.55.0/24 dev eth10',
                    '\t}',
                    '}',
                    'vrrp_instance instance1 {',
                    '\tstate MASTER',
                    '\tinterface eth0',
                    '\tvirtual_router_id 33',
                    '\tpriority 50',
                    '\tadvert_int 5',
                    '\tauthentication {',
                    '\t\tauth_type AH',
                    '\t\tauth_pass pass123',
                    '\t}',
                    '\ttrack_interface {',
                    '\t\teth0',
                    '\t}',
                    '\tvirtual_ipaddress {',
                    '\t\t192.168.1.0/24 dev eth1',
                    '\t\t192.168.3.0/24',
                    '\t}',
                    '\tvirtual_routes {',
                    '\t\t0.0.0.0/0 via 192.168.1.1 dev eth1',
                    '\t}',
                    '}']

        self.assertEqual(expected, config.build_config())


class KeepalivedManagerTestCase(base.BaseTestCase,
                                KeepalivedConfBaseMixin):

    def test_keepalived_manager_spawn(self):
        config = self._get_config()

        with contextlib.nested(mock.patch('neutron.agent.linux.'
                                          'utils.replace_file'),
                               mock.patch('neutron.agent.linux.ip_lib.'
                                          'IpNetnsCommand.'
                                          'execute')) as (replace,
                                                          execute):
            kalive = keepalived.KeepalivedManager('router1', config)
            kalive.spawn()

            execute.assert_called_once_with(
                ['keepalived', '-P', '-f', '/tmp/router1/keepalived.conf',
                 '-p', '/tmp/router1.pid',
                 '-r', '/tmp/router1-vrrp.pid'])

            replace.assert_called_once_with('/tmp/router1/keepalived.conf',
                                            '\n'.join(CONFIG_EXPECTED))


class KeepalivedNotifyScriptManagerTestCase(base.BaseTestCase):

    def test_get_notifiers_path(self):
        script_manager = keepalived.KeepalivedNotifyScriptManager('router1')

        script_manager.add_notify('master', 'master.sh')
        script_manager.add_notify('backup', 'backup1.sh')
        script_manager.add_notify('backup', 'backup2.sh')
        script_manager.add_notify('fault', 'fault.sh')

        with contextlib.nested(
            mock.patch('os.stat'),
            mock.patch('os.chmod'),
            mock.patch('os.makedirs'),
            mock.patch('neutron.agent.linux.utils.replace_file')) as (stat,
                                                                      chmod,
                                                                      makedirs,
                                                                      replace):
            master, backup, fault = script_manager.get_notifiers_path()

            self.assertEqual(master, '/tmp/router1/notify_master.sh')
            self.assertEqual(backup, '/tmp/router1/notify_backup.sh')
            self.assertEqual(fault, '/tmp/router1/notify_fault.sh')

            expected = [mock.call('/tmp/router1/notify_master.sh',
                                  '#!/usr/bin/env bash\n'
                                  'master.sh'),
                        mock.call('/tmp/router1/notify_backup.sh',
                                  '#!/usr/bin/env bash\n'
                                  'backup1.sh\n'
                                  'backup2.sh'),
                        mock.call('/tmp/router1/notify_fault.sh',
                                  '#!/usr/bin/env bash\n'
                                  'fault.sh')]

            replace.assert_has_calls(expected)
