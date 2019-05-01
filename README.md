
**Note**: This started out life a toy. Be aware that the code and
the README won't always be in sync, following the instructions will
likely expose something that needs to be tweaked or fixed before
things work. Some blog postings have been written which provide
a bit more guidance but also show how there is much more to be done
before the information here is complete:

* [etcd + placement + virt-install →
  compute](https://anticdent.org/etcd-placement-virt-install-compute.html)
* [etcd-compute
  refresh](https://anticdent.org/etcd-compute-refresh.html)
* [Playing with etcd-compute](https://blog.leafe.com/playing-with-etcd-compute/)
* [More fun with etcd-compute](https://blog.leafe.com/more-fun-with-etcd-compute/)

This repository provides a compute workload scheduler using
OpenStack
[placement](https://developer.openstack.org/api-ref/placement/) to
pick targets (via `eschedule` selecting and `ecompute`
accepting), [etcd](https://coreos.com/etcd/) as a transport, and the
`virt-install` tool from [virt-manager](https://virt-manager.org/)
to run simple VMs.

It has been built to experiment with the idea of using placement and
etcd as the main motors and state maintainers of a compute service
and come to grips with some of the systems and process involved in
creating virtual machines.

It assumes you've got a working libvirt install, with `virt-install` and
`virt-resize`. [INSTALL.md](INSTALL.md) provides instructins for setting up
an environment on one or more Ubuntu severs.

**Note**: If this ever gets to be slightly more than a toy using
the use of those `virt*` command line tools should be changed to in-Python
code.

# Architecture

The overall architecture of the system is as follows. An `etcd`
server is provided (via a docker container). A scheduler command
line tool and multiple compute servers are clients of etcd, using
watchers to notify the computes.

A placement service runs, also in a container, talking to a
database.

One or more `ecompute` processes start up and register themselves
as resource providers with some inventory (calculated via the python
`psutil` package) and then watch for new data at keys associated
with themselves within `etcd`. A pool of workers is also started.
These are responsible for creating or destroying VMs while the main
process is responsible for interacting with `etcd`. This makes it
possible to concurrently launch multiple VMs. More importantly it
means that concurrent requests to launch are not lost (as was
true in earlier versions).

The console-script `eschedule` accepts an input of resource
requirements and an optional image URL, requests allocation
candidates from placement, attempts to claim the first one and if
successful puts a value to the etcd key associated with the target
compute node. The value is the resource requirements, the instance
uuid and an image reference.

`ecompute` notices the new value on the watched key, retrieves a
copy of the image, launches a VM using `virt-install`, and sets a
key back on `etcd` saying so, and recording the IP of the guest.

A simple metadata server on each compute node to keep booting of
cloud images fast, and if the image supports it, let cloud-init
do things like set an authorized ssh key. This should [be
replaced](/cdent/etcd-compute/issues/6).

# Trying It

To try it out yourself you need docker and a database, the code
within this repo, and the Python requirements listed in
`setup.py` (`python setup.py develop` will get them). The docker
containers provide etcd and placement. See [INSTALL.md](INSTALL.md)
for more detailed installation instructions.

The containers can be started by running `docker.sh`. Placement will
be at `http://localhost:8080/`.

Edit `mdserver.conf` as required and start the metadata server with
(this will be improved or [replaced](/cdent/etcd-compute/issues/6])):

```
sudo ip addr add 169.254.169.254 dev virbr0
sudo python md_server/mdserver/server.py mdserver.conf &
```

Configure `ecompute` by creating a `compute.yaml` in the same
directory, looking something like this:

```yaml
etcd:
  host: ds1
placement:
  endpoint: http://ds1:8080
```

If you do not create the file, defaults will be used, pointing to
localhost.

If you do not want to resize disk images (it saves time but makes
the reporting of disk usage inaccurate) add the following to
`compute.yaml`

```yaml
resize: False
```

Start `ecompute` on one or more hosts. Each host must have
the python requirements, the `virt-install` related tools, and
a `compute.yaml` pointing to placement and etcd. You can install
the python requirements and the `ecompute` and `eschedule` console
scripts with `python setup.py develop`. **Note**: In some environments
`libvirt-python` may fail to install. See [INSTALL.md](INSTALL.md)
for a workaround.

```
ecompute
```

Because `ecompute` inspects the system for a real inventory, you
end up multiple-booking inventory if you have more than one
`ecompute` on same host, but it is possible to do so for testing.

By default, each time an `ecompute` is started a new resource
provider is created. This means that there can be orphaned providers
in placement that will be scheduled to, but don't have any
listeners. You can work around this by calling
`delete from resource_providers;` in the database, as required.

**Or (new feature!)**, if `uuid` in `compute.yaml` is set to a specific
value, that value will be used each time `ecompute` is started. If
a resource provider with that uuid already exists, its inventory
will be (re)set to whatever is accurate. When `ecompute` gets a
`SIGINT` or Ctrl-C it will lock its inventory by setting the
`reserved` value on the `VCPU` inventory to equal `total` and then
exit. Next time it is started (with the same uuid), reserved will be
cleared. Unless you have some particular reason for not, you should
use this feature.

Once `ecompute` is running, we can try to schedule a workload.
`eschedule` can run from any host that has network access to the
placement and etcd servers. Modify `schedule.yaml` as required.

```
eschedule 'resources=VCPU:1,DISK_GB:1,MEMORY_MB:256'
```

You can also request an image:

```
eschedule resources=VCPU:1,MEMORY_MB:256,DISK_GB:1 \
    https://cloud-images.ubuntu.com/bionic/current/bionic-server-cloudimg-amd64.img
```

If no image is specified Cirros 0.3.6 is used.

The output from `eschedule` will look something like this:

```
NOTIFIED TARGET, b8756be5-a30d-4311-920c-0ad996367a8e, \
  OF INSTANCE d578fb7c-7787-4e73-b69a-a7b3ef9bf73a
```

And from `ecompute`:

```
INSTANTIATE INSTANCE d578fb7c-7787-4e73-b69a-a7b3ef9bf73a \
  WITH IMAGE e9bedb97-917a-4f4e-9a69-8ad21840267f
ALLOCATIONS ARE {'b8756be5-a30d-4311-920c-0ad996367a8e': \
  {'resources': {'VCPU': 1, 'DISK_GB': 1, 'MEMORY_MB': 256}}}
```

If there is no capacity available, either because there's none left
or because the resource requirements are too expansive it will look
like this:

```
eschedule \
  'resources=VCPU:1,DISK_GB:1,MEMORY_MB:256&required=MISC_SHARES_VIA_AGGREGATE'
NO ALLOCATIONS LEFT
```

After an instance is booted its (local only!) IP address is put
back in etcd, which you can query by giving the instance uuid to
`eschedule`:

```
eschedule d578fb7c-7787-4e73-b69a-a7b3ef9bf73a
```

By default the guest IP is only accessible from the host. If you define
a bridge interface in `compute.yaml` this can be worked around. See
[BRIDGE.md](BRIDGE.md) for more information.

You can destroy an instance by:

```
eschedule destroy d578fb7c-7787-4e73-b69a-a7b3ef9bf73a
```

This will destroy and undefine it on the host, remove the disk,
and clear the allocations in placement. You can also use `virsh` to
destroy VMs, but this will not clean up allocations.

**Note**: The database and etcd data (in `/data/etcd`) are not
cleaned up. You'll want to take care of that yourself.

# Things to Clean Up

* On startup a compute should check to see if the metadata server is
  there, and if not, fork and start one.
* Or, better, instead of a metadata server, let's use a config drive?
* Resizing disk images is currently done with multiple subprocess calls,
  this is cumbersome and weird.
* Switch all the subprocess calls to using the python libvirt
  package directly.
* Asking to destroy a VM while it is being built has not been
  tested and is likely to go poorly.
* The network handling in [BRIDGE.md](BRIDGE.md) is clumsy and does not
  work well for all images. Switching to config drive instead of metadata
  server will help that, somewhat.

# Concepts

* Configuration should be limited, because that's hassle.
* Features should be limited, because that's hassle.
* Failure is just failure. Try again yourself, we're not going to do
  it for you.

# Misc

* If you are running a linux VM on an esxi hypervisor as a host for
  this stuff you might need to
  `export LIBGUESTFS_BACKEND_SETTINGS=force_tcg` to get filesystem
  manipulation to work well.

# Help Wanted

If you think this is interesting, and would like to help out, please
feel to make a pull request. There are a few main areas that need
attention:

* Making networking more useful.
* Error handling.
* Interacting with libvirt and avoiding subprocess calls.
* Properly choose and launching images with user-data.
