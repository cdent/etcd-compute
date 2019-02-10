
Now that this system is somewhat working, it would be nice to able
to reach the created guests from somewhere other than their hosts.

This can be done by creating a bridge interface on the host (the
computer where `compute.py` is running), associating the main physical
interface with that bridge, and then launching guests with a network
interface on that bridge.

There's a reasonable overview of how to do this in a [libvirt
networking
handbook](https://jamielinux.com/docs/libvirt-networking-handbook/bridged-network.html).
Read that and then come back here to determine some local
adjustments. The "Initial Steps" should be done, it in the setup of
the bridge itself where I needed to make adjustments.

My test host is an Ubuntu Bionic VM hosted on an ESXi hypervisor
connected (by ethernet) to a local home network where a DHCP server 
runs on the home router.

It is critical that the network interfaces on the hypervisor are set
to promiscuous mode, otherwise DHCP frames will not reach the
guests. In my environment setting the entire vswitch to promiscuous
and letting the vnics inherit from that was the most straightforward
thing. Your environment will be different but "promiscuous" is the
mode you are looking for.

In the handbook (above) the physical interface has a static IP and
IPV6 is supported. For the purpose of my testing, I only wanted to
test IPV4 and my interface used DHCP so I adjusted
`/etc/network/interfaces` as follows:


```
source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

# The primary network interface
#auto ens160
#iface ens160 inet dhcp
iface ens160 inet manual

auto br0
iface br0 inet dhcp
    # Use the MAC address identified in "Initial Steps"
    hwaddress ether 00:0c:29:c6:6f:72

    bridge_ports ens160
    # If you want to turn on Spanning Tree Protocol, ask your hosting
    # provider first as it may conflict with their network.
    bridge_stp off
    # If STP is off, set to 0. If STP is on, set to 2 (or greater).
    bridge_fd 0
```

If you then follow the instructions to bring the bridge up

```
ip address flush eth0 scope global && ifup br0
```

it should be possible to confirm the bridge using the `virt-install`
instructions in the document.

Whatever the name of the bridge you created (in this case `br0`) add
it to `compute.yaml`:

```
etcd:
  host: ds1
placement:
  endpoint: http://ds1:8080
resize: False
bridge: br0
```

This will cause a second interface to be created in VMS. The first
interface will continue to use libvirt's built in DHCP server to
provide a local-only IP.

Depending on the image being booted the second interface may be
automagically configured to use DHCP, or you may need to add some
steps to encourage it.

The metadata server included with `etcd-compute` has been adjusted
to help with this. It still listens on the internal-only network,
but it now easier to include a user-data script. A configuration
setting can point to a shell script that will be given to any VM:

**mdserver.conf**:

```
[user-data]
default = userdata.file
```

**userdata.file**:

```
#!/bin/sh
grep eth1 /etc/network/interfaces || ( \
cat << EOF >> /etc/network/interfaces
auto eth1
iface eth1 inet dhcp
EOF
ifdown eth1 && ifup eth1 )
```

That examples works well on a cirros-0.3.6 image but is likely to
blow up on plenty of other things. You can change the file as needed
or choose to configure the second interface after it boots. My goal
in this case is to be able to remotely boot a VM (using
`schedule.py`) and ssh (with private key) from that remote.

There are many problems with this, of course:

* Like the rest of the system, there is no security anywhere. For
  now that is by design.
* There's no simple way to get the IP given to the second interface
  in a guest. libvirt can tell us the first, but that doesn't help
  us reach it remotely. Making guesses is a reasonable strategy for
  now.
* Being able to declare user-data at boot time (from `scheduler.py`)
  would be better. This could be done at some point, but today
  is not that day.
