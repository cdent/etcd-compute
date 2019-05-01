
> _This is a start at an install document that will, once complete,
> supercede some of the sections of the [README](README.md)._

`etcd-compute` composes several different pieces of functionality to
result in a relatively simple tool for booting virtual machines.
Getting those several pieces installed and functioning can be
somewhat complex. This document describes how to get things working
on a collection of one or more Ubuntu bionic (`18.04`) hosts.

# Host Types

Three different types of services need to be hosted. These can all
be on the same machine, or spread around.

1. One host running docker on which [etcd](http://etcd.io) and
   [placement](https://docs.openstack.org/placement/latest/)
   containers will be run.

2. One or more hosts running `ecompute`. This host needs to have
   working Python 3 and `libvirtd` installations. This host must be
   able to reach the etcd container over the network.

3. At least one host where `eschedule` can run. This does not need
   to be a Linux machine. Anywhere that can run Python 3 and talk
   to the placement and etcd container over the network will work.

Each host that runs `ecompute` becomes one potential hypervisor when
placing workloads.

# Docker Host

> _The installing docker part is left to a later revision and
> author._

Once docker is running, the database used by the placement service
must be set up and configured. You can use MySQL or PostgreSQL.
Whatever you use it needs to be reachable from the docker host.

Create a database named `placement`. If you're not familiar with how
to do this, the
[quick-dev](https://docs.openstack.org/placement/latest/contributor/quick-dev.html#setup-the-database)
docs from the Placement project may help. Choose a username and
password of your own when granting permissions.

Get a copy of the `etcd-compute` code:

```sh
git clone https://github.com/cdent/etcd-compute.git
cd etcd-compute
```

Edit `dockerenv` to change the value of
`OS_PLACEMENT_DATABASE__CONNECTION` to a database URL that matches
the database you configured above. If you're using MySQL it should
look something like:

```
OS_PLACEMENT_DATABASE__CONNECTION=mysql+pymysql://cow:secret@some.rainbow.com/placement?charset=utf8
```

If PostgreSQL, this:

```
OS_PLACEMENT_DATABASE__CONNECTION=postgresql+psycopg2://cdent@192.168.1.76/placement?client_encoding=utf8
```

Start the containers:

```sh
./docker.sh
```

Running `docker ps` will list the resulting containers. Running
`docker logs -f placement` will tail the logs of the placement
container.

# Hypervisor Host

One the docker host is set up and the placement and etcd containers
are running, hypervisors hosts can be configured. If you are using
the same host as the docker host, some of the following steps will
duplicate earlier work.

As root, install the necessary packages using `apt`:

```sh
apt update
apt install git libvirt-dev virtinst libguestfs-tools python3-dev \
    libvirt-daemon libvirt-daemon-system python3-venv
```

`libvirtd` and `dnsmasq` should now be running. A `virbr0` device
should be present in the output of `ip a`.

Create a non-root user. Add them to the `libvirt` group in
`/etc/group`.

As that user, install and run the necessary Python code in a virtual
environment:

```sh
git clone https://github.com/cdent/etcd-compute.git
cd etcd-compute
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt && python setup.py develop
```

_Note_: `python setup.py develop` alone ought to install the
requirements properly, but libvirt-python gets confused so `pip` is
used first to work around that issue.

Edit `compute.yaml`. Set `uuid` to uniquely identify this node in a
persistent way. Change the `host` to point to the host on which the
etcd service is running. Change the `endpoint` to point to the URL
at which the `placement` service can be found. This will be the same
host as etcd, with a port of 8080. For now, comment out the `bridge`
line. Later, read [BRIDGE.md](BRIDGE.md) to use that.

`ecompute` is now ready to run and listen for boot requests:

```sh
ecompute
```

It will print debugging output to the console. When it starts up it
will report resource inventory to the placement service. When it is
interrupted, it will reserve that inventory to prevent workloads
being scheduled to this node.

You may have as many hypervisors hosts as you like.

# Schedule Host

The schedule host is any host which has been configured to run the
`eschedule` command. If you are using the same host as one of your
hypervisor hosts, you can skip forward to the _Edit_ paragraph.

As an unprivileged user install the necessary Python code in virtual
environment:

```sh
git clone https://github.com/cdent/etcd-compute.git
cd etcd-compute
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt && python setup.py develop
```

Edit `schedule.yaml`. Change the `host` to point to the host on
which the etcd service is running. Change the `endpoint` to point to
the URL at which the `placement` service can be found. This will be
the same host as etcd, with a port of 8080.

To ask to schedule a VM run the `eschedule` command:

```sh
eschedule resources=VCPU:1,DISK_GB:1,MEMORY_MB:256
```

If you deactivate the virtual environment you can schedule without
re-activating with:

```sh
.venv/bin/eschedule resources=VCPU:1,DISK_GB:1,MEMORY_MB:256
```

For more information on what to try go back to the
[README](README.md).
