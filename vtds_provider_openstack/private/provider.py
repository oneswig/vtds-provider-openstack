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
  default     = false
  type        = bool
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
}

variable "lab_subnet" {
  description = "Subnet for lab"
}

variable "lab_fixed_ips" {
  description = "Array of IP addresses for lab hosts"
}

variable "lab_prefix" {
  description = "prefix to add to all hosts created under this deployment"
  default     = "vtds"
}
'''

TF_SSH = '''
resource "tls_private_key" "default" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P384"
}

resource "local_file" "private_key_pem" {

  depends_on = [tls_private_key.default]

  content  = tls_private_key.default.private_key_pem
  filename = "private_key.pem"
}

resource "local_file" "public_key_pem" {

  depends_on = [tls_private_key.default]

  content  = tls_private_key.default.public_key_pem
  filename = "public_key.pem"
}

resource "null_resource" "chmod" {
  depends_on = [local_file.private_key_pem]

  triggers = {
    local_file_private_key_pem = "local_file.private_key_pem"
  }

  provisioner "local-exec" {
    command = "chmod 600 private_key.pem public_key.pem"
  }
}

'''

TF_COMPUTE = '''
resource "openstack_compute_keypair_v2" "vtds_lab_key" {
  name       = "${var.lab_prefix}_lab_key"
  public_key = tls_private_key.default.public_key_openssh
}

data "openstack_networking_network_v2" "lab_network" {
  name = var.lab_net_ipv4
}

resource "openstack_networking_port_v2" "lab_port" {
  count          = var.lab_count
  name           = format("%s-lab-%02d", var.lab_prefix, count.index)
  admin_state_up = true

  network_id = data.openstack_networking_network_v2.lab_network.id

  fixed_ip {
    subnet_id = var.lab_subnet
    ip_address = var.lab_fixed_ips[count.index]
  }
}

resource "openstack_compute_instance_v2" "lab" {

  count           = var.lab_count
  name            = format("%s-%02d", var.lab_prefix, count.index)
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

  depends_on = [openstack_compute_keypair_v2.vtds_lab_key]
}
'''


TF_OUTPUT = '''
locals {
  template = {
    labs     = tomap({ names = openstack_compute_instance_v2.lab.*.name, ips = openstack_compute_instance_v2.lab.*.access_ip_v4 })
  }
}

resource "local_file" "lab_hosts" {
  content  = templatefile("lab_hosts.tpl", local.template)
  filename = "lab_hosts.dat"
}
'''

TF_OUTPUT_TPL = '''%{ for name, ip in zipmap(labs.names, labs.ips) ~}
${name} ${ip}
%{ endfor ~}
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


    def __run(self, operation, tag, timeout=None, auto_approve=False):
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
                command = [ 'tofu', operation ]
                if auto_approve:
                    command += ['-auto-approve']
                with subprocess.Popen(
                    command, stdout=out, stderr=err, cwd=self.tofu_dir
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
        with open( self.tofu_dir + "/output.tf", 'w' ) as fp:
            fp.write( TF_OUTPUT )
        with open( self.tofu_dir + "/lab_hosts.tpl", 'w' ) as fp:
            fp.write( TF_OUTPUT_TPL )
        with open( self.tofu_dir + "/terraform.tfvars", 'w' ) as fp:
            # Simplification: we only process the first kind of 'virtual_blade'
            # vTDS throws our way.
            blade_type = list(self.config['virtual_blades'].keys())[0]
            blade_config = self.config['virtual_blades'][blade_type]
            blade_inter = blade_config['blade_interconnect']

            # Virtual blade stuff
            if( 'lab_flavor' in blade_config ):
                fp.write( f"lab_flavor = \"{blade_config['lab_flavor']}\"\n" )
            if( 'boot_labs_from_volume' in blade_config ):
                fp.write( "boot_labs_from_volume = %s\n" % ('true' if blade_config['boot_labs_from_volume'] else 'false') )
            if( 'image_id' in blade_config ):
                fp.write( f"image_id = \"{blade_config['image_id']}\"\n" )
            if( 'image_name' in blade_config ):
                fp.write( f"image_name = \"{blade_config['image_name']}\"\n" )
            if( 'lab_data_vol' in blade_config ):
                fp.write( f"lab_data_vol = {blade_config['lab_data_vol']}\n" )
            if( 'lab_count' in blade_config ):
                fp.write( f"lab_count = {blade_config['count']}\n" )
            if( 'lab_prefix' in blade_config ):
                fp.write( f"lab_prefix = \"{blade_config['lab_prefix']}\"\n" )

            # Interconnect stuff
            if( 'lab_net_ipv4' in blade_inter ):
                fp.write( f"lab_net_ipv4 = \"{blade_inter['lab_net_ipv4']}\"\n" )
            if( 'lab_subnet' in blade_inter ):
                fp.write( f"lab_subnet = \"{blade_inter['lab_subnet']}\"\n" )
            fp.write( "lab_fixed_ips = [" )
            for ip in blade_inter['ip_addrs']:
                fp.write( f' "{ip}",' )
            fp.write( " ]\n" )

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
        self.__run( "apply", "deploy", 300, True )

        # Read in the hostnames as deployed.
        # They will be written out to a data file by the Tofu resources.
        hostnames = []
        with open( self.tofu_dir + "/lab_hosts.dat", 'r') as fp:
            for line in fp:
                hostname, ip = line.split()
                hostnames += [ip]
                # FIXME: we should be able to use hostnames
                # but vTDS is assuming they are resolvable, which is
                # not always true
                #hostnames += [hostname]

        # Assume we are only interested in the first blade type
        blade_type = list(self.config['virtual_blades'].keys())[0]
        blade_config = self.config['virtual_blades'][blade_type]
        blade_config['hostnames'] = hostnames

    def remove(self):
        if not self.prepared:
            raise ContextualError(
                "cannot deploy an unprepared provider, call prepare() first"
            )
        print("Removing vtds-provider-openstack")
        self.__run( "destroy", "remove", 120, True )

    def get_virtual_blades(self):
        return VirtualBlades(self.common)

    def get_blade_interconnects(self):
        return BladeInterconnects(self.common)

    def get_secrets(self):
        return Secrets(self.secret_manager)

    def get_site_config(self):
        return SiteConfig(self.common)
