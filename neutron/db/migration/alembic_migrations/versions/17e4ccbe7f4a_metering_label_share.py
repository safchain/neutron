# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2014 OpenStack Foundation
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#

"""metering_label_shared

Revision ID: 17e4ccbe7f4a
Revises: 33c3db036fe4
Create Date: 2014-01-30 16:00:41.507154

"""

# revision identifiers, used by Alembic.
revision = '17e4ccbe7f4a'
down_revision = '33c3db036fe4'

# Change to ['*'] if this migration applies to all plugins

migration_for_plugins = ['neutron.services.metering.metering_plugin.'
                         'MeteringPlugin']

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import expression as expr

from neutron.db import migration


def upgrade(active_plugins=None, options=None):
    if not migration.should_run(active_plugins, migration_for_plugins):
        return

    op.add_column('meteringlabels', sa.Column('shared', sa.Boolean(),
                                              server_default=expr.false(),
                                              nullable=True))


def downgrade(active_plugins=None, options=None):
    if not migration.should_run(active_plugins, migration_for_plugins):
        return

    op.drop_column('meteringlabels', 'shared')
