
import json
import shutil
import re
import subprocess
import sys
import time
from threading import Event
import uuid

import etcd3
import requests

KEY = '/hosts'
SLEEP = 1
PLACEMENT = 'http://localhost:8080'
COMPUTE_UUID = str(uuid.uuid4())

client = etcd3.client()


def _print(output):
    print('%s: %s' % (COMPUTE_UUID, output))


def main(inventory):
    """Set up the resource provider for this compute and start
    the main loop.
    """
    session = requests.Session()
    session.headers.update({'x-auth-token': 'admin',
                            'openstack-api-version': 'placement latest',
                            'accept': 'application/json',
                            'content-type': 'application/json'})
    # Inventory is "FOO:1,BAR:2, BAZ:8"
    inventories = [inv.strip() for inv in inventory.split(',')]
    inventory_dict = dict(inv.split(':') for inv in inventories)

    inventories_dict = {}
    for resource_class, value in inventory_dict.items():
        inventories_dict[resource_class] = {
            'total': int(value)
            # For now use defaults for the rest of the fields
        }

    generation = _create_resource_provider(session, COMPUTE_UUID)
    _set_inventory(session, COMPUTE_UUID, generation, inventories_dict)

    main_loop(COMPUTE_UUID)


def main_loop(compute_uuid):
    """Listen for changes on the key for this instance."""

    # This is won't cope well if lots of requests happen on the
    # key at near the same time, I expect etcd can help here, but
    # further research required.

    def watch_handler(event):
        watch_event.set()

    our_key = '%s/%s' % (KEY, compute_uuid)
    watch_id = client.add_watch_callback(our_key, watch_handler)

    while True:
        watch_event = Event()

        try:
            # This is racy, but we'll worry about that some other time.
            while not watch_event.is_set():
                time.sleep(SLEEP)
                _print('sleeping')
            _handle_new(our_key)
        except (Exception, KeyboardInterrupt) as e:
            _print('FAIL: %s, %s' % (type(e), e))
            client.cancel_watch(watch_id)
            return


def _handle_new(key):
    """Note the spawn, by sending True to /booted."""
    # Clearly this is not anywhere near as much as really starting
    # an instance. And we would want to fail and unclaim (here or in
    # the scheduler?), sometimes.
    value, meta = client.get(key)
    # We need to explicitly provide the decoding
    value = str(value, 'UTF-8')
    data = json.loads(value)
    _print('INSTANTIATE INSTANCE %(instance)s WITH IMAGE %(image)s' % data)
    _print('\tALLOCATIONS ARE %(allocations)s' % data)
    _spawn(data)
    ip_address = _get_ip(data['instance'])
    print('\tIP is %s' % ip_address)
    client.put('/booted/%(instance)s' % data, ip_address)


def _spawn(data):
    image = data['image']
    instance = data['instance']
    allocations = data['allocations'][COMPUTE_UUID]['resources']
    _print(allocations)
    memory = allocations['MEMORY_MB']
    vcpu = allocations['VCPU']
    disk_size = allocations['DISK_GB']
    dest = _copy_image(image, instance, disk_size)
    _print(dest)
    args = [
            'virt-install',
            '--name', instance,
            '--memory', str(memory),
            '--vcpus', str(vcpu),
            '--disk', dest,
            '--graphics', 'none',
            '--import',
            '--noautoconsole',
            ]
    _print('spawning %s' % args)
    subprocess.Popen(args)
    _print('spawned %s' % args)


def _get_ip(instance):
    while True:
        try:
            output = subprocess.check_output(['virsh', 'domifaddr', instance])
            output = str(output)
            _print(output)
            output = output.replace('\n', ' ').rstrip()
            output = output.split()[-1].split('/')[0]
            if re.match(r'^\d+\.\d+\.\d+.\d+', output):
                return output
        except subprocess.CalledProcessError:
            pass
        time.sleep(1)


def _copy_image(source, instance, size):
    dest = '%s.img' % instance
    # FIXME: error handling
    # FIXME: we can't assume the filesystem, but for now we do.
    env = {
        'LIBGUESTFS_HV': '/tmp/qemu-wrapper.sh',
    }
    subprocess.check_call(['truncate', '-r', source, dest])
    subprocess.check_call(['truncate', '-s', '%sG' % size, dest])
    subprocess.check_call(['virt-resize', '--expand', '/dev/sda1',
                           source, dest], env=env)
    return dest


def _set_inventory(session, uuid, generation, inventory):
    """Set the inventory."""
    url = '%s/resource_providers/%s/inventories' % (PLACEMENT, uuid)
    data = {
        'inventories': inventory,
        'resource_provider_generation': generation,
    }
    resp = session.put(url, json=data)
    if resp:
        return True
    _print('failed to set inventory')
    sys.exit(1)


def _create_resource_provider(session, uuid):
    """Create the resource provider that this compute is."""
    url = '%s/resource_providers' % PLACEMENT
    data = {'uuid': uuid, 'name': uuid}
    resp = session.post(url, json=data)
    if resp:
        return resp.json()['generation']
    _print('failed to create provider')
    sys.exit(1)


if __name__ == '__main__':
    main(sys.argv[1])
