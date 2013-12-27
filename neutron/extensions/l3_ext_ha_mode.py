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

from neutron.api import extensions
from neutron.common import constants

HA_INFO = 'ha_vr_id'
EXTENDED_ATTRIBUTES_2_0 = {
    'routers': {HA_INFO:
                {'allow_post': False, 'allow_put': False,
                 'default': None, 'is_visible': True}}}


class L3_ext_ha_mode(extensions.ExtensionDescriptor):
    """Extension class supporting virtual router in HA mode."""

    @classmethod
    def get_name(cls):
        return "HA Router extension"

    @classmethod
    def get_alias(cls):
        return constants.L3_HA_MODE_EXT_ALIAS

    @classmethod
    def get_description(cls):
        return "Add HA capability to the router."

    @classmethod
    def get_namespace(cls):
        return ""

    @classmethod
    def get_updated(cls):
        return "2013-01-29T00:00:00-00:00"

    def get_extended_resources(self, version):
        if version == "2.0":
            return EXTENDED_ATTRIBUTES_2_0
        else:
            return {}
