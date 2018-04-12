
**Note**: This is a toy.

This repository provides a toy compute workload scheduler using
OpenStack
[placement](https://developer.openstack.org/api-ref/placement/) to
pick targets (via `scheduler.py` selecting and `compute.py`
accepting) and [etcd](https://coreos.com/etcd/) as a transport.

_It is a toy because no actual VMs are created._

One or more `compute.py` processes start up and register themselves
as resource providers with some inventory and then watch for new
data at keys associated with themselves within etcd.

`schedule.py` accepts an input of resource requirements, requests
allocation candidates from placement, attempts to claim the first
one and if successful puts a value to the etcd key associated with
the target compute node. The value is the resource requirements, the
instance uuid and a fake image reference. Networking and keypairs
are left out of the picture for now.

`compute.py` notices the new value on the watched key and fakes
launching a VM and sets a key back on etcd saying so.

That's as far as it goes, so far.

To try it out yourself you need docker and a database, the code
within this repo, and the Python requirements listed in
`requirements.txt`. The docker containers provide etcd and placement
itself. The placement container is an autobuild of
[placedock](/cdent/placedock).

The containers can be started by running `docker.sh`. Placement will
be at `http://localhost:8080/`.

Start one or more `compute.py`. The argument describes the
inventory. Here we start ten of them in the background:

```
for i in {1..10}; do \
    python compute.py 'VCPU:4,DISK_GB:10,MEMORY_MB:512' & \
    sleep 5; done
```

Then we can try schedule a workload:

```
python schedule.py 'resources=VCPU:1,DISK_GB:1,MEMORY_MB:5'
```

The output will look something like this in `shedule.py`:

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

**Note**: The database and etcd data (in `/data/etcd`) are not
cleaned up. You'll want to take care of that yourself.
