
**Note**: This is a toy.

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
`virt-resize`.

# Architecture

The overall architecture of the system is as follows. An `etcd`
server is provided (via a docker container). A scheduler command
line tool and multiple compute servers are clients of etcd, using
watchers to notify the computes.

A placement service runs, also in a container, talking to a
database.

One or more `compute.py` processes start up and register themselves
as resource providers with some inventory and then watch for new
data at keys associated with themselves within `etcd`.

`schedule.py` accepts an input of resource requirements, requests
allocation candidates from placement, attempts to claim the first
one and if successful puts a value to the etcd key associated with
the target compute node. The value is the resource requirements, the
instance uuid and an image reference. Networking and ssh keypairs
are left out of the picture for now.

`compute.py` notices the new value on the watched key, retrieves a
copy of the image, launches a VM using `virt-install`, and sets a
key back on `etcd` saying so, and recording the IP of the guest.

A simple metadata server runs to keep booting of cloud images fast.

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
python md_server/mdserver/server.py mdserver.conf &
```

Start one or more `compute.py`. The argument describes the
inventory. Here we start ten of them in the background:

```
for i in {1..10}; do \
    python compute.py 'VCPU:4,DISK_GB:10,MEMORY_MB:512' &> compute.$i.log & \
    sleep 2; done
```

At some point `compute.py` will inspect the system for a real
inventory.

Then we can try to schedule a workload:

```
python schedule.py 'resources=VCPU:1,DISK_GB:1,MEMORY_MB:5'
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
  {'resources': {'VCPU': 1, 'DISK_GB': 1, 'MEMORY_MB': 5}}}
```

If there is no capacity available, either because there's none left
or because the resource requirements are too strict it will look
like this:

```
python schedule.py \
  'resources=VCPU:1,DISK_GB:1,MEMORY_MB:5&required=MISC_SHARES_VIA_AGGREGATE' 
NO ALLOCATIONS LEFT
```

After an instance is booted its IP address is put back in etcd, which you can
query by giving the instance uuid to schedule.py:

```
python schedule.py d578fb7c-7787-4e73-b69a-a7b3ef9bf73a
```

**Note**: The database and etcd data (in `/data/etcd`) are not
cleaned up. You'll want to take care of that yourself.

# Things to Clean Up

* Image URLs should be passed on the schedule.py command line and
  the compute.py should create the VMs image by pulling it and
  writing to an appropriate name. With
  [caching](https://cachecontrol.readthedocs.io/).
* Need some way to destroy an image, including cleaning up
  allocations. Presumably `schedule.py` can write to the appropriate
  key in `etcd` and a compute will see it and do the right thing.
* On startup a compute should check to see if the metadata server is
  there, and if not, fork and start one.
* Resizing disk images is currently done with multiple subprocess calls,
  this is cumbersome and weird.
* Can the experiment be made more robust/interesting by, when using
  more than one `compute.py` on the same physical host, reporting
  disk as a shared resource provider? Or would that be complicating
  things too much?
* Switch all these subprocess calls to using the python libvirt
  package directly.

# Concepts

* Configuration should be limited, because that's hassle.
* Features should be limited, because that's hassle.
* Concurrency in the computes isn't a huge concern, is better to
  spread than pack (in this environment) anyway. If there are
  time consuming operations in a compute, do we want to lock it
  somehow during that time?
* Failure is just failure. Try again yourself, we're not going to do
  it for you.
