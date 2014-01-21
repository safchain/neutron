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

import itertools
import os
import stat

from oslo.config import cfg

from neutron.agent.linux import external_process
from neutron.agent.linux import utils
from neutron.common import exceptions
from neutron.openstack.common import log as logging

VALID_STATES = ['MASTER', 'BACKUP']
VALID_NOTIFY_STATES = ['master', 'backup', 'fault']
VALID_AUTH_TYPES = ['AH', 'PASS']

LOG = logging.getLogger(__name__)


class InvalidInstanceStateException(exceptions.NeutronException):
    message = _('Invalid instance state: %(state)s, valid states are '
                'MASTER, BACKUP')


class InvalidNotifyStateException(exceptions.NeutronException):
    message = _('Invalid notify state: %(state)s, valid states are '
                'master, backup, fault')


class InvalidAuthenticationTypeExecption(exceptions.NeutronException):
    message = _('Invalid authentication type: %(type)s, valid type are '
                'AH, PASS')


class KeepalivedVipAddress(object):
    """A virtual address entry of a keepalived configuration."""

    def __init__(self, ip_address, interface_name=None):
        self.ip_address = ip_address
        self.interface_name = interface_name

    def build_config(self):
        if self.interface_name:
            return "%s dev %s" % (self.ip_address, self.interface_name)

        return self.ip_address

    def __eq__(self, other):
        return self.ip_address == other.ip_address


class KeepalivedVirtualRoute(object):
    """A virtual router entry of a keepalived configuration."""

    def __init__(self, destination, nexthop, interface_name):
        self.destination = destination
        self.nexthop = nexthop
        self.interface_name = interface_name

    def build_config(self):
        return "%s via %s dev %s" % (self.destination, self.nexthop,
                                     self.interface_name)

    def __eq__(self, other):
        return ((self.destination == other.destination) and
                (self.nexthop == other.nexthop))


class KeepalivedGlobaldefs(object):
    """global_defs section of a keepalived configuration."""

    def __init__(self):
        self.notification_emails = []
        self.notification_email_from = None
        self.smtp_server = None
        self.smtp_connect_timeout = None

    def add_notification_email(self, email):
        self.notification_emails.append(email)

    def set_email_from(self, email):
        self.notification_email_from = email

    def set_smtp_server(self, host, timeout):
        self.smtp_server = host
        self.smtp_connect_timeout = timeout

    def build_config(self):
        config = ['global_defs {']

        if self.notification_emails:
            config.append('\tnotification_email {')
            for email in self.notification_emails:
                config.append('\t\t' + email)
            config.append('\t}')

        if self.notification_email_from:
            config.append('\tnotification_email_from %s' %
                          self.notification_email_from)

        if self.smtp_server:
            config.extend(['\tsmtp_server %s' % self.smtp_server,
                           '\tsmtp_connect_timeout %s' %
                           self.smtp_connect_timeout])

        config.append('}')

        return config


class KeepalivedGroup(object):
    """Group section of a keepalived configuration."""

    def __init__(self, name):
        self.name = name
        self.instance_names = set()
        self.notifiers = {}

    def add_instance(self, instance):
        self.instance_names.add(instance.name)

    def remove_instance(self, instance):
        self.instance_names.discard(instance.name)

    def set_notify(self, state, path):
        if state not in VALID_NOTIFY_STATES:
            raise InvalidNotifyStateException(state=state)
        self.notifiers[state] = path

    def set_notifiers(self, master=None, backup=None, fault=None):
        if master:
            self.set_notify('master', master)
        if backup:
            self.set_notify('backup', backup)
        if fault:
            self.set_notify('fault', fault)

    def build_config(self):
        return itertools.chain(['vrrp_sync_group %s {' % self.name,
                                '\tgroup {'],
                               ('\t\t' + i for i in self.instance_names),
                               ['\t}'],
                               ('\tnotify_' + state + ' "' + path + '"'
                                for state, path in self.notifiers.items()),
                               ['}'])


class KeepalivedInstance(object):
    """Instance section of a keepalived configuration."""

    def __init__(self, name, state, interface, vrouter_id, priority=50,
                 advert_int=None, mcast_src_ip=None, nopreempt=False,
                 garp_master_delay=None):
        self.name = name

        if state not in VALID_STATES:
            raise InvalidInstanceStateException(state=state)

        self.state = state
        self.interface = interface
        self.vrouter_id = vrouter_id
        self.priority = priority
        self.nopreempt = nopreempt
        self.advert_int = advert_int
        self.mcast_src_ip = mcast_src_ip
        self.track_interfaces = []
        self.vip_addresses = []
        self.vip_addresses_excluded = []
        self.virtual_routes = []
        self.authentication = []
        self.garp_master_delay = garp_master_delay

    def set_authentication(self, type, password):
        if type not in VALID_AUTH_TYPES:
            raise InvalidAuthenticationTypeExecption(type=type)

        self.authentication = [type, password]

    def remove_vips_vroutes_by_interface(self, interface_name):
        for vip in self.vip_addresses:
            if interface_name == vip.interface_name:
                self.vip_addresses.remove(vip)

        for vip in self.vip_addresses_excluded:
            if interface_name == vip.interface_name:
                self.vip_addresses_excluded.remove(vip)

        for vroute in self.virtual_routes:
            if interface_name == vroute.interface_name:
                self.virtual_routes.remove(vroute)

    def remove_vip_by_ip_address(self, ip_address):
        for vip in self.vip_addresses:
            if ip_address == vip.ip_address:
                self.vip_addresses.remove(vip)

        for vip in self.vip_addresses_excluded:
            if ip_address == vip.ip_address:
                self.vip_addresses_excluded.remove(vip)

    def _build_track_interface_config(self):
        return itertools.chain(['\ttrack_interface {'],
                               ('\t\t' + i for i in self.track_interfaces),
                               ['\t}'])

    def _build_vip_addresses_config(self):
        return itertools.chain(['\tvirtual_ipaddress {'],
                               ('\t\t' + vip.build_config()
                                for vip in self.vip_addresses),
                               ['\t}'])

    def _build_vip_addresses_excluded_config(self):
        return itertools.chain(['\tvirtual_ipaddress_excluded {'],
                               ('\t\t' + vip.build_config()
                                for vip in self.vip_addresses_excluded),
                               ['\t}'])

    def _build_virutal_routes_config(self):
        return itertools.chain(['\tvirtual_routes {'],
                               ('\t\t' + route.build_config()
                                for route in self.virtual_routes),
                               ['\t}'])

    def build_config(self):
        config = ['vrrp_instance %s {' % self.name,
                  '\tstate %s' % self.state,
                  '\tinterface %s' % self.interface,
                  '\tvirtual_router_id %s' % self.vrouter_id,
                  '\tpriority %s' % self.priority]

        if self.nopreempt:
            config.append('\tnopreempt')

        if self.garp_master_delay:
            config.append('\tgarp_master_delay %s' % self.garp_master_delay)

        if self.advert_int:
            config.append('\tadvert_int %s' % self.advert_int)

        if self.authentication:
            type, password = self.authentication
            authentication = ['\tauthentication {',
                              '\t\tauth_type %s' % type,
                              '\t\tauth_pass %s' % password,
                              '\t}']
            config.extend(authentication)

        if self.mcast_src_ip:
            config.append('\tmcast_src_ip %s' % self.mcast_src_ip)

        if self.track_interfaces:
            config.extend(self._build_track_interface_config())

        if self.vip_addresses:
            config.extend(self._build_vip_addresses_config())

        if self.vip_addresses_excluded:
            config.extend(self._build_vip_addresses_excluded_config())

        if self.virtual_routes:
            config.extend(self._build_virutal_routes_config())

        config.append('}')

        return config


class KeepalivedConf(object):
    """A keepalived configuration."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.global_defs = None
        self.groups = {}
        self.instances = {}

    def set_global_defs(self, global_defs):
        self.global_defs = global_defs

    def add_group(self, group):
        self.groups[group.name] = group

    def del_group(self, group):
        self.groups.pop(group.name, None)

    def get_group(self, name):
        return self.groups.get(name)

    def add_instance(self, instance):
        self.instances[instance.name] = instance

    def del_instance(self, instance):
        self.instances.pop(instance.name, None)

    def get_instance(self, name):
        return self.instances.get(name)

    def build_config(self):
        config = []

        if self.global_defs:
            config.extend(self.global_defs.build_config())

        for group in self.groups.values():
            config.extend(group.build_config())

        for instance in self.instances.values():
            config.extend(instance.build_config())

        return config


class KeepalivedManagerMixin(object):
    def _get_conf_file_name(self, kind, ensure_conf_dir=True):
        confs_dir = os.path.abspath(os.path.normpath(self.conf_path))
        conf_dir = os.path.join(confs_dir, self.resource_id)
        if ensure_conf_dir:
            if not os.path.isdir(conf_dir):
                os.makedirs(conf_dir, 0o755)

        return os.path.join(conf_dir, kind)


class KeepalivedManager(KeepalivedManagerMixin):
    """Wrapper for keepalived.

    This wrapper permits to write keepalived config files, to start/restart
    keepalived process.

    """

    def __init__(self, resource_id, config, conf_path='/tmp',
                 namespace=None, root_helper=None):
        self.resource_id = resource_id
        self.config = config
        self.namespace = namespace
        self.root_helper = root_helper
        self.conf_path = conf_path
        self.conf = cfg.CONF
        self.pm = None

    def _output_config_file(self):
        config_str = '\n'.join(self.config.build_config())
        config_path = self._get_conf_file_name('keepalived.conf')
        utils.replace_file(config_path, config_str)

        return config_path

    def spawn(self):
        config_path = self._output_config_file()

        self.pm = external_process.ProcessManager(
            self.conf,
            self.resource_id,
            self.root_helper,
            self.namespace,
            self.conf_path)

        def callback(pid_file):
            cmd = ['keepalived', '-P',
                   '-f', config_path,
                   '-p', pid_file,
                   '-r', self.pm.get_pid_file_name(sub_name='vrrp')]
            return cmd

        self.pm.enable(callback)

        LOG.debug(_("Keepalived spawned with config %s"), config_path)

    def spawn_or_restart(self):
        if self.pm:
            self.restart()
        else:
            self.spawn()

    def restart(self):
        if self.pm.active:
            self._output_config_file()
            self.pm.restart()
        else:
            LOG.warn(_("A previous instance of keepalived seems to be dead, "
                       "unable to restart it, a new instance will be "
                       "spawned"))
            self.pm.disable()
            self.spawn()

    def disable(self):
        if self.pm:
            self.pm.disable(kill=False)


class KeepalivedNotifyScriptManager(KeepalivedManagerMixin):

    def __init__(self, resource_id, conf_path='/tmp'):
        self.resource_id = resource_id
        self.conf_path = conf_path

        self.notifiers = None
        self.reset_notifiers()

    def reset_notifiers(self, state=None):
        if state:
            self.notifiers[state] = []
        else:
            self.notifiers = {'master': [],
                              'backup': [],
                              'fault': []}

    def add_notify(self, state, cmd):
        if state not in VALID_NOTIFY_STATES:
            raise InvalidNotifyStateException(state=state)

        self.notifiers[state].append(cmd)

    def _build_notify_script(self, state):
        if not self.notifiers[state]:
            return

        name = self._get_conf_file_name('notify_' + state + '.sh')
        script = '#!/usr/bin/env bash\n' + '\n'.join(self.notifiers[state])
        utils.replace_file(name, script)
        st = os.stat(name)
        os.chmod(name, st.st_mode | stat.S_IEXEC)

        return name

    def get_notifiers_path(self):
        master = self._build_notify_script('master')
        backup = self._build_notify_script('backup')
        fault = self._build_notify_script('fault')

        return master, backup, fault
