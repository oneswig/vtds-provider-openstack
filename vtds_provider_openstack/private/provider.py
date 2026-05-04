#
# MIT License
#
# (C) Copyright [2024] Hewlett Packard Enterprise Development LP
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
"""Private layer implementation module for the OpenStack provider.

"""

from vtds_base import (
    ContextualError,
)
from vtds_base.layers.provider import ProviderAPI
from .api_objects import (
    SiteConfig,
    VirtualBlades,
    BladeInterconnects,
    Secrets
)
from .secret_manager import SecretManager
from .common import Common


class Provider(ProviderAPI):
    """Provider class, implements the OpenStack provider layer
    accessed through the python Provider API.

    """
    def __init__(self, stack, config, build_dir):
        """Constructor, stash the root of the platfform tree and the
        digested and finalized provider configuration provided by the
        caller that will drive all activities at all layers.

        """
        self.__doc__ = ProviderAPI.__doc__
        self.stack = stack
        self.config = config.get('provider', None)
        if self.config is None:
            raise ContextualError(
                "no provider configuration found in top level configuration"
            )
        self.build_dir = build_dir
        self.common = Common(self.config, self.build_dir)
        self.secret_manager = SecretManager(self.config)
        self.prepared = False

    def consolidate(self):
        return

    def prepare(self):
        print("Preparing vtds-provider-openstack")
        self.prepared = True

    def validate(self):
        if not self.prepared:
            raise ContextualError(
                "cannot validate an unprepared provider, "
                "call prepare() first"
            )
        print("Validating vtds-provider-openstack")

    def deploy(self):
        if not self.prepared:
            raise ContextualError(
                "cannot deploy an unprepared provider, call prepare() first"
            )
        print("Deploying vtds-provider-openstack")

    def remove(self):
        if not self.prepared:
            raise ContextualError(
                "cannot deploy an unprepared provider, call prepare() first"
            )
        print("Removing vtds-provider-openstack")

    def get_virtual_blades(self):
        return VirtualBlades(self.common)

    def get_blade_interconnects(self):
        return BladeInterconnects(self.common)

    def get_secrets(self):
        return Secrets(self.secret_manager)

    def get_site_config(self):
        return SiteConfig(self.common)
