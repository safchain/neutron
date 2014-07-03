# Copyright (C) 2013 eNovance SAS <licensing@enovance.com>
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

import copy
import time

from oslo.config import cfg

from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.openstack.common import loopingcall
from neutron.openstack.common.notifier import api as notifier_api

Opts = [
    cfg.BoolOpt('enable_port_metering', default=False,
                help=_("Enable the port metering feature.")),
    cfg.IntOpt('measure_interval', default=30,
               help=_("Interval between two metering measures.")),
    cfg.IntOpt('report_interval', default=60,
               help=_("Interval between two metering reports.")),
]
cfg.CONF.register_opts(Opts, "PORT_METERING")

LOG = logging.getLogger(__name__)


class PortMetering(object):
    def __init__(self, context, driver):
        self.host = cfg.CONF.host
        self.context = context

        self._load_driver(driver)

        self.metering_loop = loopingcall.FixedIntervalLoopingCall(
            self._metering_loop
        )

        self.ports = {}
        self.last_report = 0
        self.last_counters = {}
        self.metering_counters = {}

        measure_interval = cfg.CONF.PORT_METERING.measure_interval
        self.metering_loop.start(interval=measure_interval)

    def _load_driver(self, driver):
        """Loads plugin-driver from configuration."""
        if not driver:
            raise SystemExit(_('A port metering driver must be specified'))

        LOG.info(_("Loading Port Metering driver %s"), driver)
        self.driver = importutils.import_object(driver)

    def add_port(self, tenant_id, net_id, device_id, device_owner,
                 port_name, port_id):
        self.ports[port_id] = {'tenant_id': tenant_id,
                               'network_id': net_id,
                               'device_id': device_id,
                               'device_owner': device_owner,
                               'port_name': port_name,
                               'port_id': port_id}

    def del_port(self, port_id):
        counters = self._get_ports_counters(self.ports.values())
        if not counters:
            return

        counter = counter[port_id]
        self._add_metering_counter(port_id, counter)
        self._metering_notification(port_id, counter)

        del self.ports[port_id]

    def _invoke_driver(self, ports, func_name):
        try:
            return getattr(self.driver, func_name)(ports)
        except RuntimeError:
            LOG.exception(_("Driver %(driver)s does not implement %(func)s"),
                          {'driver': cfg.CONF.PORT_METERING.driver,
                           'func': func_name})

    def _get_ports_counters(self, ports):
        LOG.debug(_("Get ports counters"))
        return self._invoke_driver(ports, 'get_ports_counters')

    def _metering_notification(self, port_id, counter):
            data = {'port_id': port_id,
                    'network_id': self.ports[port_id]['network_id'],
                    'device_id': self.ports[port_id]['device_id'],
                    'device_owner': self.ports[port_id]['device_owner'],
                    'tenant_id': self.ports[port_id]['tenant_id'],
                    'bytes_tx': counter['bytes_tx'],
                    'packets_tx': counter['packets_tx'],
                    'bytes_rx': counter['bytes_rx'],
                    'packets_rx': counter['packets_rx'],
                    'time': counter['time'],
                    'first_update': counter['first_update'],
                    'last_update': counter['last_update'],
                    'host': self.host}

            LOG.debug(_("Send metering report: %s"), data)
            notifier_api.notify(self.context,
                                notifier_api.publisher_id('port_metering'),
                                'port.meter',
                                notifier_api.CONF.default_notification_level,
                                data)

            counter['bytes_tx'] = 0
            counter['packets_tx'] = 0
            counter['bytes_rx'] = 0
            counter['packets_rx'] = 0
            counter['time'] = 0

    def _metering_notifications(self):
        for port_id, counter in self.metering_counters.items():
            self._metering_notification(port_id, counter)

    def _add_metering_counter(self, port_id, counter):
        ts = int(time.time())
        info = self.metering_counters.get(port_id, {'bytes_tx': 0,
                                                    'packets_tx': 0,
                                                    'bytes_rx': 0,
                                                    'packets_rx': 0,
                                                    'time': 0,
                                                    'first_update': ts,
                                                    'last_update': ts})

        last_counter = self.last_counters.get(port_id, {'bytes_tx': 0,
                                                        'packets_tx': 0,
                                                        'bytes_rx': 0,
                                                        'packets_rx': 0})

        info['bytes_tx'] += counter['bytes_tx'] - last_counter['bytes_tx']
        info['packets_tx'] += (counter['packets_tx'] -
                               last_counter['packets_tx'])
        info['bytes_rx'] += (counter['bytes_rx'] -
                             last_counter['bytes_rx'])
        info['packets_rx'] += (counter['packets_rx'] -
                               last_counter['packets_rx'])
        info['time'] += ts - info['last_update']
        info['last_update'] = ts

        self.metering_counters[port_id] = info
        self.last_counters[port_id] = copy.copy(counter)

        return info

    def _add_metering_counters(self):
        if not self.ports:
            return

        counters = self._get_ports_counters(self.ports.values())
        if not counters:
            return

        for port_id, counter in counters.items():
            self._add_metering_counter(port_id, counter)

    def _metering_loop(self):
        self._add_metering_counters()

        ts = int(time.time())
        delta = ts - self.last_report

        report_interval = cfg.CONF.PORT_METERING.report_interval
        if delta > report_interval:
            self._metering_notifications()
            self.last_report = ts
