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

from neutron import manager
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants as plugin_constants

LOG = logging.getLogger(__name__)


class L3_HA_NAT_rpc_mixin(object):

    def update_router_state(self, context, **kwargs):
        l3plugin = manager.NeutronManager.get_service_plugins()[
            plugin_constants.L3_ROUTER_NAT]
        if not l3plugin:
            LOG.error(_('No plugin for L3 routing registered!'))
            return

        router_id = kwargs.get('router_id')
        state = kwargs.get('state')
        host = kwargs.get('host')

        return l3plugin.update_router_state(context, router_id, state,
                                            host=host)
