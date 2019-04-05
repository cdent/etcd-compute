
**Note**: This is a toy. Be aware that the code and the README won't
always be in sync, following the instructions will likely expose
something that needs to be tweaked or fixed before things work.

This repository provides a toy compute workload scheduler using
OpenStack
[placement](https://developer.openstack.org/api-ref/placement/) to
pick targets (via `scheduler.py` selecting and `compute.py`
accepting), [etcd](https://coreos.com/etcd/) as a transport, and the
`virt-install` tool from [virt-manager](https://virt-manager.org/)
to run simple VMs.

_It is a toy because there's very little in the way of error
handling, networking is very limited, and concepts like
authentication, authorization, configuration, migration, consoles
and lots of other stuff that real people use is entirely left out._

It has been built to experiment with the idea of using placement and
etcd as the main motors and state maintainers of a compute service
and come to grips with some of the systems and process involved in
creating virtual machines.

It assumes you've got a working libvirt install, with `virt-install` and
`virt-resize`. On ubuntu that means the `virtinst` and `libguestfs-tools`
packages. **Note**: If this ever gets to be slightly more than a toy using
those command line tools should be changed to in-Python code.

# Architecture

The overall architecture of the system is as follows. An `etcd`
server is provided (via a docker container). A scheduler command
line tool and multiple compute servers are clients of etcd, using
watchers to notify the computes.

A placement service runs, also in a container, talking to a
database.

One or more `compute.py` processes start up and register themselves
as resource providers with some inventory (calculated via the python
`psutil` package) and then watch for new data at keys associated
with themselves within `etcd`. A pool of workers is also started.
These are responsible for creating or destroying VMs while the main
process is responsible for interacting with `etcd`. This makes it
possible to concurrently launch multiple VMs. More importantly it
means that concurent requests to launch are not lost (this was
true in earlier versions).

`schedule.py` accepts an input of resource requirements, requests
allocation candidates from placement, attempts to claim the first
one and if successful puts a value to the etcd key associated with
the target compute node. The value is the resource requirements, the
instance uuid and an image reference. Networking and ssh keypairs
are left out of the picture for now.

`compute.py` notices the new value on the watched key, retrieves a
copy of the image, launches a VM using `virt-install`, and sets a
key back on `etcd` saying so, and recording the IP of the guest.

A simple metadata server runs to keep booting of cloud images fast,
and if the image supports it, let cloud-init do things like set an
authorized ssh key.

# Trying It

To try it out yourself you need docker and a database, the code
within this repo, and the Python requirements listed in
`requirements.txt`. The docker containers provide `etcd` and placement
itself.

The containers can be started by running `docker.sh`. Placement will
be at `http://localhost:8080/`.

Edit `mdserver.conf` as required and start the metadata server with
(this will be improved):

```
sudo ip addr add 169.254.169.254 dev virbr0
sudo python md_server/mdserver/server.py mdserver.conf &
```

Configure `compute.py` by creating a `compute.yaml` in the same
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

Start a `compute.py` on one or more hosts. Each host must have
the python requirements, the `virt-install` related tools, and
a `compute.yaml` pointing to placement and etcd.

```
python compute.py
```

Because `compute.py` inspects the system for a real inventory, you
end up multiple-booking inventory if you have more than one
`compute.py` on same host, but it is possible to do so for testing.

Each time a `compute.py` is started a new resource provider is
created. This means that there can be orphaned providers in
placement that will be scheduled to, but don't have any listeners.
Work around this by `delete from resource_providers;` as required.

Once `compute.py` is running, we can try to schedule a workload.
`schedule.py` can run from any host that has network access to the
placement and etcd servers. Modify `schedule.yaml` as required.

```
python schedule.py 'resources=VCPU:1,DISK_GB:1,MEMORY_MB:256'
```

The output will look something like this in `schedule.py`:

```
NOTIFIED TARGET, b8756be5-a30d-4311-920c-0ad996367a8e, \
  OF INSTANCE d578fb7c-7787-4e73-b69a-a7b3ef9bf73a
```

And in `compute.py`:

```
INSTANTIATE INSTANCE d578fb7c-7787-4e73-b69a-a7b3ef9bf73a \
  WITH IMAGE e9bedb97-917a-4f4e-9a69-8ad21840267f
ALLOCATIONS ARE {'b8756be5-a30d-4311-920c-0ad996367a8e': \
  {'resources': {'VCPU': 1, 'DISK_GB': 1, 'MEMORY_MB': 256}}}
```

If there is no capacity available, either because there's none left
or because the resource requirements are too strict it will look
like this:

```
python schedule.py \
  'resources=VCPU:1,DISK_GB:1,MEMORY_MB:256&required=MISC_SHARES_VIA_AGGREGATE'
NO ALLOCATIONS LEFT
```

After an instance is booted its IP address is put back in etcd, which you can
query by giving the instance uuid to schedule.py:

```
python schedule.py d578fb7c-7787-4e73-b69a-a7b3ef9bf73a
```

By default the guest IP is only accessible from the host. If you define
a bridge interface in `compute.yaml` this can be worked around. See
[BRIDGE.md](BRIDGE.md) for more.

You can destroy an instance by:

```
python schedule.py destroy d578fb7c-7787-4e73-b69a-a7b3ef9bf73a
```

This will destroy and undefine it on the host, and clear the allocations in
placement. You can also use `virsh` to destroy VMs, but this will
not clean up allocations.

**Note**: The database and etcd data (in `/data/etcd`) are not
cleaned up. You'll want to take care of that yourself.

# Things to Clean Up

* On startup a compute should check to see if the metadata server is
  there, and if not, fork and start one.
* Resizing disk images is currently done with multiple subprocess calls,
  this is cumbersome and weird.
* Switch all the subprocess calls to using the python libvirt
  package directly.
* After a VM is destroyed the image is left lying around. That
  should be removed.
* Asking to destroy a VM while it is being built has not been
  tested and is likely to go poorly.
* When a compute.py shuts down, its resource provider should be
  disabled, removed, what?

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
