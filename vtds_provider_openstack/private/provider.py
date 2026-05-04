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

import os
import subprocess

from vtds_base import (
    ContextualError,
    log_paths,
    logfile,
    write_out,
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

# Tofu resource specs
TF_PROVIDER = '''
terraform {
  required_version = ">= 0.14"

  required_providers {
    local = {
      source = "hashicorp/local"
    }
    null = {
      source = "hashicorp/null"
    }
    tls = {
      source = "hashicorp/tls"
    }
    openstack = {
      source = "terraform-provider-openstack/openstack"
      version = "~>3.0.0"
    }
  }
}
'''

TF_VARS = '''
variable "lab_flavor" {
  description = "Lab instance type"
  default     = "baremetal-32"
}

# Note: If using baremetals, this should be set to false.
# Can be set to true to give VMs more storage space.
variable "boot_labs_from_volume" {
  description = "Whether or not to boot labs from volume."
  default     = "false"
}

variable "image_id" {
  description = "Boot from volume requires ID of image"
}

variable "image_name" {
  description = "Lab software image base"
  default     = "CentOS-stream8"
}

variable "lab_count" {
  description = "Number of labs"
  default     = "1"
}

variable "lab_data_vol" {
  description = "Lab data volume in GB"
  default = "200"
}

variable "lab_net_ipv4" {
  description = "Network for lab"
  default     = "aufn-ipv4-vlan"
}

variable "lab_prefix" {
  description = "prefix to add to all hosts created under this deployment"
  default     = "vtds"
}
'''

TF_SSH = '''
resource "openstack_compute_keypair_v2" "vtds_lab_key" {
  name       = "${var.lab_prefix}_lab_key"
  public_key = tls_private_key.default.public_key_openssh
}
'''

TF_COMPUTE = '''
data "openstack_networking_network_v2" "lab_network" {
  name = var.lab_net_ipv4
}

resource "openstack_compute_instance_v2" "lab" {

  count           = var.lab_count
  name            = format("%s-lab-%02d", var.lab_prefix, count.index)
  image_name      = var.image_name
  flavor_name     = var.lab_flavor
  key_pair        = openstack_compute_keypair_v2.vtds_lab_key.name

  dynamic "block_device" {
    for_each = var.boot_labs_from_volume ? [1] : []
    content {
      uuid                  = var.image_id
      source_type           = "image"
      volume_size           = var.lab_data_vol
      boot_index            = 0
      destination_type      = "volume"
      delete_on_termination = true
    }
  }

  network {
    port = openstack_networking_port_v2.lab_port[count.index].id
  }

  timeouts {
    create = "30m"
  }

  depends_on = [openstack_compute_keypair_v2.vtds_lab_key, null_resource.registry]
}
'''


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
        self.tofu_dir = self.common.build_dir() + "/tofu"
        self.secret_manager = SecretManager(self.config)
        self.prepared = False


    def __run(self, operation, tag, timeout=None):
        """Run a tofu operation in the build tree capturing the output in 
        separate output and error logs for later analysis.

        """
        out_path, err_path = log_paths(
            self.common.build_dir(),
            "tofu_%s[%s]" % (operation, tag)
        )
        with logfile(out_path) as out, logfile(err_path) as err:
            try:
                write_out(
                    "running tofu %s[%s] in '%s'" % (operation, tag, self.tofu_dir)
                )
                with subprocess.Popen(
                    [
                        'tofu',
                        operation
                    ],
                    stdout=out, stderr=err, cwd=self.tofu_dir
                ) as sub:
                    time = 0
                    signaled = False
                    while True:
                        try:
                            exitval = sub.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            time += 5
                            if timeout and time > timeout:
                                if not signaled:
                                    # First try to terminate the process
                                    sub.terminate()
                                    continue
                                sub.kill()
                                print()
                                # pylint: disable=raise-missing-from
                                raise ContextualError(
                                    "tofu '%s' operation timed out "
                                    "and did not terminate as expected "
                                    "after %d seconds" % (operation, time),
                                    out_path, err_path
                                )
                            write_out('.')
                            continue
                        # Didn't time out, so the wait is done.
                        break
                    print()
            except FileNotFoundError as err:
                raise ContextualError(
                    "executing tofu '%s' operation failed "
                    "- %s" % (operation, str(err))
                ) from err
            if exitval != 0:
                fmt = (
                    "tofu '%s' operation failed" if not signaled
                    else "tofu '%s' operation timed out and was killed"
                )
                raise ContextualError(
                    fmt % operation,
                    out_path,
                    err_path
                )

    def consolidate(self):
        return

    def prepare(self):
        print("Preparing vtds-provider-openstack")

        # Initialise Tofu working directory
        try:
            os.mkdir( self.tofu_dir )
        except FileExistsError:
            pass
        with open( self.tofu_dir + "/providers.tf", 'w' ) as fp:
            fp.write( TF_PROVIDER )
        with open( self.tofu_dir + "/vars.tf", 'w' ) as fp:
            fp.write( TF_VARS )
        with open( self.tofu_dir + "/ssh.tf", 'w' ) as fp:
            fp.write( TF_SSH )
        with open( self.tofu_dir + "/compute.tf", 'w' ) as fp:
            fp.write( TF_COMPUTE )
        with open( self.tofu_dir + "/terraform.tfvars", 'w' ) as fp:
            if( self.config['lab_flavor'] ):
                fp.write( f"lab_flavor = {self.config['lab_flavor']}" )
            if( self.config['boot_labs_from_volume'] ):
                fp.write( f"boot_labs_from_voluem = {self.config['boot_labs_from_volume']}" )
            if( self.config['image_id'] ):
                fp.write( f"image_id = {self.config['image_id']}" )
            if( self.config['image_name'] ):
                fp.write( f"image_name = {self.config['image_name']}" )
            if( self.config['lab_count'] ):
                fp.write( f"lab_count = {self.config['lab_count']}" )
            if( self.config['lab_data_vol'] ):
                fp.write( f"lab_data_vol = {self.config['lab_data_vol']}" )
            if( self.config['lab_net_ipv4'] ):
                fp.write( f"lab_net_ipv4 = {self.config['lab_net_ipv4']}" )
            if( self.config['lab_prefix'] ):
                fp.write( f"lab_prefix = {self.config['lab_prefix']}" )

        self.__run( "init", "prepare", 30 )
        self.prepared = True

    def validate(self):
        if not self.prepared:
            raise ContextualError(
                "cannot validate an unprepared provider, "
                "call prepare() first"
            )
        print("Validating vtds-provider-openstack")
        self.__run( "plan", "validate", 30 )

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
