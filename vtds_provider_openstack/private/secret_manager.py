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
"""Private layer code to manage secrets and translate between API secret
operations and OpenStack secret operations.

"""
from vtds_base import (
    ContextualError,
)


class SecretManager:
    """Class providing operations for creating and removing secrets as
    needed from a secret manager.

    """
    def __init__(self, config):
        """Constructor

        """
        secrets = config.get('secrets', {})
        try:
            self.secrets = {
                secret['name']: secret for _, secret in secrets.items()
            }
        except KeyError as err:
            # No harm compiling a list since we are going to error out
            # anyway. This will provide a more useful error.
            missing_names = [
                key for key, secret in secrets.items() if 'name' not in secret
            ]
            raise ContextualError(
                "configuration error: the following secrets (by key) in the "
                "config do not define a 'name' field: %s" % str(missing_names)
            ) from err
        # We are going to cache it all in memory for mocking purposes,
        # no actual store behind any of it, so set all of the cached
        # secrets to None initially.
        self.cache = {key: None for key in self.secrets}

    def deploy(self):
        """Deploy all secrets declared by any layer during the
        'prepare' phase to the OpenStack Secret Manager. This creates the
        secrets as place holders for content. It is up to the users of
        the secrets to fill them with data (create versions in OpenStack
        parlance) by calling into the provider API for storing data in
        secrets.

        """

    def remove(self):
        """Remove all secrets declared by any layer during the
        'prepare' phase from the OpenStack Secret Manager.

        """
        self.secrets = {}
        self.cache = {}

    def store(self, name, value):
        """Store a value in the named secret. The value should be a
        UTF-8 encoded string.

        """
        secret = self.secrets.get(name, None)
        if secret is None:
            raise ContextualError(
                "attempt to store value in unknown secret '%s'" % name
            )
        self.cache[name] = value

    def read(self, name):
        """Read the value of the named secret.

        """
        secret = self.secrets.get(name, None)
        if secret is None:
            raise ContextualError(
                "attempt to store value in unknown secret '%s'" % name
            )
        return self.cache[name]
