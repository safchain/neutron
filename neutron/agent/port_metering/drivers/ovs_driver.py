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

import re

from neutron.agent.linux import ovs_lib
from neutron.agent.port_metering.drivers import abstract_driver
from neutron.common import log


class OvsPortMeteringDriver(abstract_driver.PortMeteringAbstractDriver):

    @log.log
    def get_ports_counters(self, ports):
        port_name_id_map = dict((port['port_name'], port['port_id'])
                                for port in ports)

        result = ovs_lib.BaseOVS('sudo').run_dpctl(['-s', 'show'])

        ports_counters = {}

        curr_port_id = None
        for line in result.splitlines():
            if 'port ' in line:
                port_infos = line.strip().split()
                port_name = port_infos[2]
                if port_name in port_name_id_map:
                    curr_port_id = port_name_id_map[port_name]
                else:
                    curr_port_id = None
                continue

            match = re.match(r".*RX\sbytes:(\d+).*TX\sbytes:(\d+).*", line)
            if curr_port_id and match:
                port_counter = ports_counters.get(curr_port_id, {})
                port_counter['bytes_rx'] = int(match.group(1))
                port_counter['bytes_tx'] = int(match.group(2))
                ports_counters[curr_port_id] = port_counter

                continue

            match = re.match(r".*([RT]X)\spackets:(\d+).*", line)
            if curr_port_id and match:
                key = 'packets_rx'
                if match.group(1) == 'TX':
                    key = 'packets_tx'

                port_counter = ports_counters.get(curr_port_id, {})
                port_counter[key] = int(match.group(2))
                ports_counters[curr_port_id] = port_counter

        return ports_counters
