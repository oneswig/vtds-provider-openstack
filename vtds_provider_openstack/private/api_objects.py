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
"""Private implementations of API objects.

"""
from subprocess import (
    Popen,
    TimeoutExpired
)
from socketserver import TCPServer
from socket import (
    socket,
    AF_INET,
    SOCK_STREAM
)
from time import sleep

from vtds_base import (
    ContextualError,
    log_paths,
    logfile,
    info_msg,
    render_command_string
)
from vtds_base.layers.provider import (
    SiteConfigBase,
    VirtualBladesBase,
    BladeInterconnectsBase,
    BladeConnectionBase,
    BladeConnectionSetBase,
    BladeSSHConnectionBase,
    BladeSSHConnectionSetBase,
    SecretsBase
)


class SiteConfig(SiteConfigBase):
    """Site configuration information composed by the Provider layer
    for public use.

    """
    def __init__(self, common):
        """Constructor

        """
        self.__doc__ = SiteConfigBase.__doc__
        self.common = common

    def system_name(self):
        return self.common.system_name()

    def site_ntp_servers(self, address_family='AF_INET'):
        return self.common.site_ntp_servers(address_family)

    def site_dns_servers(self, address_family='AF_INET'):
        return self.common.site_dns_servers(address_family)


# pylint: disable=invalid-name
class VirtualBlades(VirtualBladesBase):
    """The external representation of a class of Virtual Blades and
    the public operations that can be performed on blades in that
    class. Virtual Blade operations refer to individual blades by
    their instance number which is an integer greater than or equal to
    0 and less that the number of blade instances in the class.

    """
    def __init__(self, common):
        """Constructor

        """
        self.__doc__ = VirtualBladesBase.__doc__
        self.common = common

    def blade_classes(self):
        virtual_blades = self.common.get('virtual_blades', {})
        return [name for name, _ in virtual_blades.items()]

    def application_metadata(self, blade_class):
        return self.common.blade_application_metadata(blade_class)

    def blade_count(self, blade_class):
        return self.common.blade_count(blade_class)

    def blade_interconnects(self, blade_class):
        return self.common.blade_interconnects(blade_class)

    def blade_hostname(self, blade_class, instance):
        return self.common.blade_hostname(blade_class, instance)

    def blade_ip(self, blade_class, instance, interconnect):
        return self.common.blade_ip(blade_class, instance, interconnect)

    def blade_ssh_key_secret(self, blade_class):
        return self.common.blade_ssh_key_secret(blade_class)

    def blade_ssh_key_paths(self, blade_class):
        return self.common.ssh_key_paths()

    def connect_blade(self, blade_class, instance, remote_port):
        return BladeConnection(
            self.common, blade_class, instance, remote_port
        )

    def connect_blades(self, remote_port, blade_classes=None):
        blade_classes = (
            self.blade_classes() if blade_classes is None else blade_classes
        )
        connections = [
            BladeConnection(
                self.common, blade_class, instance, remote_port
            )
            for blade_class in blade_classes
            for instance in range(0, self.blade_count(blade_class))
        ]
        return BladeConnectionSet(self.common, connections)

    def ssh_connect_blade(self, blade_class, instance, remote_port=22):
        return BladeSSHConnection(
            self.common, blade_class, instance,
            self.blade_ssh_key_paths(blade_class)[1],
            remote_port
        )

    def ssh_connect_blades(self, blade_classes=None, remote_port=22):
        blade_classes = (
            self.blade_classes() if blade_classes is None else blade_classes
        )
        connections = [
            BladeSSHConnection(
                self.common, blade_class, instance,
                self.blade_ssh_key_paths(blade_class)[1],
                remote_port
            )
            for blade_class in blade_classes
            for instance in range(0, self.blade_count(blade_class))
        ]
        return BladeSSHConnectionSet(self.common, connections)


class BladeInterconnects(BladeInterconnectsBase):
    """The external representation of the set of Blade Interconnects
    and public operations that can be performed on the interconnects.

    """
    def __init__(self, common):
        """Constructor

        """
        self.common = common

    def __interconnects_by_name(self):
        """Return a dictionary of non-pure-base-class interconnects
        indexed by 'network_name'

        """
        blade_interconnects = self.common.get("blade_interconnects", {})
        try:
            return {
                interconnect['network_name']: interconnect
                for _, interconnect in blade_interconnects.items()
                if not interconnect.get('pure_base_class', False)
            }
        except KeyError as err:
            # Since we are going to error out anyway, build a list of
            # interconnects without network names so we can give a
            # more useful error message.
            missing_names = [
                key for key, interconnect in blade_interconnects.items()
                if 'network_name' not in interconnect
            ]
            raise ContextualError(
                "provider config error: 'network_name' not specified in "
                "the following blade interconnects: %s" % str(missing_names)
            ) from err

    def __named_interconnect(self, interconnect_name):
        """Look up a specifically named interconnect and return it.
        """
        blade_interconnects = self.__interconnects_by_name()
        if interconnect_name not in blade_interconnects:
            raise ContextualError(
                "requesting ipv4_cidr of unknown blade interconnect '%s'" %
                interconnect_name
            )
        return blade_interconnects.get(interconnect_name, {})

    def application_metadata(self, interconnect_name):
        interconnect = self.__named_interconnect(interconnect_name)
        return interconnect.get('application_metadata', {})

    def interconnect_names(self):
        """Get a list of blade interconnects by name

        """
        return self.__interconnects_by_name().keys()

    def ipv4_cidr(self, interconnect_name):
        """Return the (string) IPv4 CIDR (<IP>/<length>) for the
        network on the named interconnect.

        """
        blade_interconnects = self.__interconnects_by_name()
        if interconnect_name not in blade_interconnects:
            raise ContextualError(
                "requesting ipv4_cidr of unknown blade interconnect '%s'" %
                interconnect_name
            )
        interconnect = blade_interconnects.get(interconnect_name, {})
        if 'ipv4_cidr' not in interconnect:
            raise ContextualError(
                "provider layer configuration error: no 'ipv4_cidr' found in "
                "blade interconnect named '%s'" % interconnect_name
            )
        return interconnect['ipv4_cidr']


class BladeConnection(BladeConnectionBase):
    """A class containing the relevant information needed to use
    external connections to ports on a specific Virtual Blade.

    """
    def __init__(self, common, blade_class, instance, remote_port):
        """Constructor

        """
        self.__doc__ = BladeConnectionBase.__doc__
        self.common = common
        self.b_class = blade_class
        self.instance = instance
        self.rem_port = remote_port
        self.hostname = self.common.blade_hostname(
            blade_class, instance
        )
        self.loc_ip = "127.0.0.1"
        self.loc_port = None
        self.log_out = None
        self.log_err = None
        self._connect()

    def _connect(self):
        """Layer private operation: establish the connection and learn
        the local IP and port of the connection.

        """
        # Nothing needed, job done
        return

    def __enter__(self):
        return self

    def __exit__(
            self,
            exception_type=None,
            exception_value=None,
            traceback=None
    ):
        # Nothing needed, job done
        return

    def blade_class(self):
        return self.b_class

    def blade_hostname(self):
        return self.hostname

    def remote_port(self):
        return self.rem_port

    # Placeholder
    def local_ip(self):
        return '127.0.0.1'

    # Placeholder
    def local_port(self):
        return 0


class BladeConnectionSet(BladeConnectionSetBase):
    """A class that contains multiple active BladeConnections to
    facilitate operations on multiple simultaneous blades. This class
    is just a wrapper for a list of BladeContainers and should be
    obtained using the VirtualBlades.connect_blades() method not
    directly.

    """
    def __init__(self, common, blade_connections):
        """Constructor

        """
        self.__doc__ = BladeConnectionSetBase.__doc__
        self.common = common
        self.blade_connections = blade_connections

    def __enter__(self):
        return self

    def __exit__(
            self,
            exception_type=None,
            exception_value=None,
            traceback=None
    ):
        for connection in self.blade_connections:
            connection.__exit__(exception_type, exception_value, traceback)

    def list_connections(self, blade_class=None):
        return [
            blade_connection for blade_connection in self.blade_connections
            if blade_class is None or
            blade_connection.blade_class() == blade_class
        ]

    def get_connection(self, hostname):
        for blade_connection in self.blade_connections:
            if blade_connection.blade_hostname() == hostname:
                return blade_connection
        return None



# The following is shared by BladeSSHConnection and
# BladeSSHConnectionSet. This should be treaded as private to
# this file. It is pulled out of both classes for easy sharing.
def wait_for_popen(subprocess, cmd, logpaths, timeout=None, **kwargs):
    """Wait for a Popen() object to reach completion and return
    the exit value.

    If 'check' is either omitted from the keyword arguments or is
    supplied in the keyword aguments and True, raise a
    ContextualError if the command exists with a non-zero exit
    value, otherwise simply return the exit value.

    If 'timeout' is supplied (in seconds) and exceeded kill the
    Popen() object and then raise a ContextualError indicating the
    timeout and reporting where the command logs can be found.

    """
    info_msg(
        "waiting for popen: "
        "subproc='%s', cmd='%s', logpaths='%s', timeout='%s', kwargs='%s'" % (
            str(subprocess), str(cmd), str(logpaths), str(timeout), str(kwargs)
        )
    )
    check = kwargs.get('check', True)
    time = timeout if timeout is not None else 0
    signaled = False
    while True:
        try:
            exitval = subprocess.wait(timeout=5)
            break
        except TimeoutExpired:
            time -= 5 if timeout is not None else 0
            if timeout is not None and time <= 0:
                if not signaled:
                    # First try to terminate the process
                    subprocess.terminate()
                    continue
                subprocess.kill()
                # pylint: disable=raise-missing-from
                raise ContextualError(
                    "SSH command '%s' timed out and did not terminate "
                    "as expected after %d seconds" % (str(cmd), time),
                    *logpaths
                )
            continue
    if check and exitval != 0:
        raise ContextualError(
            "SSH command '%s' terminated with a non-zero "
            "exit status '%d'" % (str(cmd), exitval),
            *logpaths
        )
    return exitval


class BladeSSHConnection(BladeSSHConnectionBase, BladeConnection):
    """Specifically a connection to the SSH server on a blade (remote
    port 22 unless otherwise specified) with methods to copy files to
    and from the blade using SCP and to run commands on the blade
    using SSH.

    """
    def __init__(
        self,
        common, blade_class, instance,  private_key_path, remote_port=22,
        **kwargs
    ):
        BladeConnection.__init__(
            self,
            common, blade_class, instance, remote_port
        )
        self.__doc__ = BladeSSHConnectionBase.__doc__
        default_opts = [
            '-o', 'BatchMode=yes',
            '-o', 'NoHostAuthenticationForLocalhost=yes',
            '-o', 'StrictHostKeyChecking=no',
        ]
        self.options = kwargs.get('options', default_opts)
        self.private_key_path = private_key_path

    def __enter__(self):
        return self

    def __exit__(
            self,
            exception_type=None,
            exception_value=None,
            traceback=None
    ):
        BladeConnection.__exit__(
            self, exception_type, exception_value, traceback
        )

    def __run(
        self, cmd, blocking=True, out_path=None, err_path=None,  **kwargs
    ):
        """Run an arbitrary command under Popen() either synchronously
        or asynchronously letting exceptions bubble up to the caller.

        """
        with logfile(out_path) as out_file, logfile(err_path) as err_file:
            if blocking:
                with Popen(
                        cmd,
                        stdout=out_file, stderr=err_file,
                        **kwargs
                ) as subprocess:
                    return wait_for_popen(
                        subprocess, cmd, (out_path, err_path), None, **kwargs
                    )
            else:
                return Popen(
                    cmd,
                    stdout=out_file, stderr=err_file,
                    **kwargs
                )

    def _render_cmd(self, cmd):
        """Layer private: render the specified command string with
        Jinja to fill in the BladeSSHConnection specific data in a
        templated command.

        """
        jinja_values = {
            'blade_class': self.b_class,
            'instance': self.instance,
            'blade_hostname': self.hostname,
            'remote_port': self.rem_port,
            'local_ip': self.loc_ip,
            'local_port': self.loc_port
        }
        return render_command_string(cmd, jinja_values)

    def copy_to(
            self, source, destination,
            recurse=False, blocking=True, logname=None, **kwargs
    ):
        logname = (
            logname if logname is not None else
            "copy-to-%s-%s" % (source, destination)
        )
        logfiles = log_paths(
            self.common.build_dir(),
            "%s-%s" % (logname, self.blade_hostname())
        )
        recurse_option = ['-r'] if recurse else []
        cmd = [
            'scp', '-i', self.private_key_path, *recurse_option, *self.options,
            source,
            'root@%s:%s' % (self.hostname, destination)
        ]
        try:
            return self.__run(cmd, blocking, *logfiles, **kwargs)
        except ContextualError:
            # If it is one of ours just send it on its way to be handled
            raise
        except Exception as err:
            # Not one of ours, turn it into one of ours
            raise ContextualError(
                "failed to copy file '%s' to 'root@%s:%s' "
                "using command: %s - %s" % (
                    source, self.hostname, destination, str(cmd), str(err)
                ),
                *logfiles
            ) from err

    def copy_from(
        self, source, destination,
            recurse=False, blocking=True, logname=None, **kwargs
    ):
        logname = (
            logname if logname is not None else
            "copy-from-%s-%s" % (source, destination)
        )
        logfiles = log_paths(
            self.common.build_dir(),
            "%s-%s" % (logname, self.blade_hostname())
        )
        recurse_option = ['-r'] if recurse else []
        cmd = [
            'scp', '-i', self.private_key_path, *recurse_option, *self.options,
            'root@%s:%s' % (self.hostname, destination),
            source
        ]
        try:
            return self.__run(cmd, blocking, *logfiles, **kwargs)
        except ContextualError:
            # If it is one of ours just send it on its way to be handled
            raise
        except Exception as err:
            # Not one of ours, turn it into one of ours
            raise ContextualError(
                "failed to copy file '%s' from 'root@%s:%s' "
                "using command: %s - %s" % (
                    destination, self.hostname, source, str(cmd), str(err)
                ),
                *logfiles,
            ) from err

    def run_command(self, cmd, blocking=True, logfiles=None, **kwargs):
        cmd = self._render_cmd(cmd)
        logfiles = logfiles if logfiles is not None else (None, None)
        ssh_cmd = [
            'ssh', '-i', self.private_key_path, *self.options,
            'root@%s' % (self.hostname), cmd
        ]
        try:
            return self.__run(ssh_cmd, blocking, *logfiles, **kwargs)
        except ContextualError:
            # If it is one of ours just send it on its way to be handled
            raise
        except Exception as err:
            # Not one of ours, turn it into one of ours
            raise ContextualError(
                "failed to run command '%s' on '%s' - %s" % (
                    cmd, self.hostname, str(err)
                ),
                *logfiles
            ) from err


class BladeSSHConnectionSet(BladeSSHConnectionSetBase, BladeConnectionSet):
    """A class to wrap multiple BladeSSHConnections and provide
    operations that run in parallel across multiple connections.

    """
    def __init__(self, common, connections):
        """Constructor
        """
        BladeConnectionSet.__init__(self, common, connections)

    def __enter__(self):
        return self

    def __exit__(
            self,
            exception_type=None,
            exception_value=None,
            traceback=None
    ):
        BladeConnectionSet.__exit__(
            self, exception_type, exception_value, traceback
        )

    def copy_to(
        self, source, destination,
        recurse=False, logname=None, blade_class=None
    ):
        wait_args_list = [
            (
                blade_connection.copy_to(
                    source, destination, recurse=recurse, blocking=False,
                    logname=logname
                ),
                "scp %s to root@%s:%s" % (
                    source,
                    blade_connection.blade_hostname(),
                    destination
                ),
                log_paths(
                    self.common.build_dir(),
                    "%s-%s" % (logname, blade_connection.blade_hostname())
                )
            )
            for blade_connection in self.blade_connections
            if blade_class is None or
            blade_connection.blade_class() == blade_class
        ]
        # Go through all of the copy operations and collect (if
        # needed) any errors that are raised by
        # wait_for_popen(). This acts as a barrier, so when we are
        # done, we know all of the copies have completed.
        errors = []
        for wait_args in wait_args_list:
            try:
                wait_for_popen(*wait_args)
            # pylint: disable=broad-exception-caught
            except Exception as err:
                errors.append(str(err))
        if errors:
            raise ContextualError(
                "errors reported while copying '%s' to '%s' on %s\n"
                "    %s" % (
                    source,
                    destination,
                    "all Virtual Blades" if blade_class is None else
                    "Virtual Blades of class %s" % blade_class,
                    "\n\n    ".join(errors)
                )
            )

    def run_command(self, cmd, logname=None, blade_class=None):
        # Okay, this is big and weird. It composes the arguments to
        # pass to wait_for_popen() for each copy operation. Note
        # that, normally, the 'cmd' argument in wait_for_popen() is
        # the Popen() 'cmd' argument. Here is is simply the shell
        # command being run under SSH. This is okay because
        # wait_for_popen() only uses that information for error
        # generation.
        wait_args_list = [
            (
                blade_connection.run_command(
                    cmd, False,
                    log_paths(
                        self.common.build_dir(),
                        "%s-%s" % (logname, blade_connection.blade_hostname())
                    )
                ),
                cmd,
                log_paths(
                    self.common.build_dir(),
                    "%s-%s" % (logname, blade_connection.blade_hostname())
                )
            )
            for blade_connection in self.blade_connections
            if blade_class is None or
            blade_connection.blade_class() == blade_class
        ]
        # Go through all of the sub-processes and collect (if needed)
        # any errors that are raised by wait_for_popen(). This acts as
        # a barrier, so when we are done, we know all of the copies
        # have completed.
        errors = []
        for wait_args in wait_args_list:
            try:
                wait_for_popen(*wait_args)
            # pylint: disable=broad-exception-caught
            except Exception as err:
                errors.append(str(err))
        if errors:
            raise ContextualError(
                "errors reported running command '%s' on %s\n"
                "    %s" % (
                    cmd,
                    "all Virtual Blades" if blade_class is None else
                    "Virtual Blades of class %s" % blade_class,
                    "\n\n    ".join(errors)
                )
            )


class Secrets(SecretsBase):
    """Provider Layers Secrets API object. Provides ways to populate
    and retrieve secrets through the Provider layer. Secrets are
    created by the provider layer by declaring them in the Provider
    configuration for your vTDS system, and should be known by their
    names as filled out in various places and verious layers in your
    vTDS system. For example the SSH key pair used to talk to a
    particular set of Virtual Blades through a blade connection is
    stored in a secret configured in the Provider layer and the name
    of that secret can be obtained from a VirtualBlades API object
    using the blade_ssh_key_secret() method.

    """
    def __init__(self, secret_manager):
        """Construtor

        """
        self.__doc__ = SecretsBase.__doc__
        self.secret_manager = secret_manager

    def store(self, name, value):
        self.secret_manager.store(name, value)

    def read(self, name):
        return self.secret_manager.read(name)

    def application_metadata(self, name):
        return self.secret_manager.application_metadata(name)
