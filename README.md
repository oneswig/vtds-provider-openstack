# vtds-provider-openstack

An OpenStack Provider layer for the vTDS suite to be used in testing other
layers and in testing the vTDS Core.

## Limitations

It's a very basic prototype right now and has many assumptions:

* OpenTofu is installed on the vTDS admin host
* OpenStack credentials are sourced in the environment of the vTDS session
