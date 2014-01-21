# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2012 New Dream Network, LLC (DreamHost)
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
# @author: Mark McClain, DreamHost

import os

from oslo.config import cfg

from neutron.agent.linux import ip_lib
from neutron.agent.linux import utils
from neutron.openstack.common import log as logging

LOG = logging.getLogger(__name__)

OPTS = [
    cfg.StrOpt('external_pids',
               default='$state_path/external/pids',
               help=_('Location to store child pid files')),
]

cfg.CONF.register_opts(OPTS)


class ProcessManager(object):
    """An external process manager for Neutron spawned processes.

    Note: The manager expects uuid to be in cmdline.
    """
    def __init__(self, conf, uuid, root_helper='sudo', namespace=None,
                 pids_path=None):
        self.conf = conf
        self.uuid = uuid
        self.root_helper = root_helper
        self.namespace = namespace
        self.pids_path = pids_path or self.conf.external_pids

    def enable(self, cmd_callback):
        if not self.active:
            cmd = cmd_callback(self.get_pid_file_name(ensure_pids_dir=True))

            ip_wrapper = ip_lib.IPWrapper(self.root_helper, self.namespace)
            ip_wrapper.netns.execute(cmd)

    def _kill(self, signal):
        pid = self.pid
        if self.active:
            cmd = ['kill', '-' + signal, pid]
            utils.execute(cmd, self.root_helper)
        elif pid:
            LOG.debug(_('Process for %(uuid)s pid %(pid)d is stale, ignoring '
                        'command %(signal)s'), {'uuid': self.uuid, 'pid': pid,
                                                'signal': signal})
        else:
            LOG.debug(_('No process started for %s'), self.uuid)

    def restart(self):
        self._kill('HUP')

    def disable(self, kill=True):
        if kill:
            self._kill('9')
        else:
            self._kill('15')

        pid_file = self.get_pid_file_name()
        if os.path.exists(pid_file):
            os.unlink(self.get_pid_file_name())

    def get_pid_file_name(self, ensure_pids_dir=False, sub_name=None):
        """Returns the file name for a given kind of config file."""
        pids_dir = os.path.abspath(os.path.normpath(self.pids_path))
        if ensure_pids_dir and not os.path.isdir(pids_dir):
            os.makedirs(pids_dir, 0o755)

        pid_file = self.uuid
        if sub_name:
            pid_file += '-' + sub_name
        pid_file += '.pid'

        return os.path.join(pids_dir, pid_file)

    def _pid_from_file(self, file_name):
        msg = _('Error while reading %s')
        try:
            with open(file_name, 'r') as f:
                return int(f.read())
        except IOError:
            msg = _('Unable to access %s')
        except ValueError:
            msg = _('Unable to convert value in %s')

        LOG.debug(msg, file_name)
        return None

    @property
    def pid(self):
        """Last known pid for this external process spawned for this uuid."""
        file_name = self.get_pid_file_name()
        return self._pid_from_file(file_name)

    @property
    def active(self):
        pid = self.pid
        if pid is None:
            return False

        cmdline = '/proc/%s/cmdline' % pid
        try:
            with open(cmdline, "r") as f:
                return self.uuid in f.readline()
        except IOError:
            return False
