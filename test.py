
import sys
import libvirt

conn = libvirt.open('qemu:///system')

dom = conn.lookupByName(sys.argv[1])

print(dom)

ifaces = dom.interfaceAddresses(
    libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE, 0)

print(ifaces)


for name, value in ifaces.items():
    for ipaddr in value['addrs']:
        print(ipaddr['addr'])
