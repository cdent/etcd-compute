
import io
import functools
import json
import os
import multiprocessing
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

import cachecontrol
from cachecontrol.caches import file_cache
from cachecontrol import serialize
import etcd3
import libvirt
import msgpack
import psutil
import requests
import yaml

from ecomp import clients
from ecomp import conf


# Locked shared around the children.
LOCK = multiprocessing.Lock()
LOCK_INVENTORY = lambda: sys.exit(1)  # noqa

KEY = '/hosts'
SLEEP = 1
CLIENT = None
COMPUTE_UUID = None

# default config
CONFIG = {
    'uuid': str(uuid.uuid4()),
    'placement': {
        'endpoint': 'http://localhost:8080',
    },
    'etcd': {},
    # Do we resize disk images. Set to False when
    # doing testing and experimenting or because that's
    # just what you want. Means allocations are not accurate.
    'resize': True,
    # By default we only use the 'default' libvirt network.
    # If bridge is defined that will be used too.
    'bridge': None,
}


def _exit(*args):
    global LOCK_INVENTORY
    # Only lock once, from the parent.
    if multiprocessing.active_children():
        LOCK_INVENTORY()
    sys.exit(args[0])


# Have a tidy exit on signt
signal.signal(signal.SIGINT, _exit)


# deal with size limitations in CacheControl
class MySerializer(serialize.Serializer):

    def _loads_v4(self, request, data):
        try:
            cached = msgpack.loads(
                data, encoding="utf-8", max_bin_len=2147483647)
        except ValueError:
            return

        return self.prepare_response(request, cached)


def _print(output):
    print('%s: PID: %s [%s] %s' % (
        time.time(), os.getpid(), COMPUTE_UUID, output))


def main(config):
    """Set up the resource provider for this compute and start
    the main loop.
    """
    global LOCK_INVENTORY, COMPUTE_UUID
    compute_uuid = config['uuid']
    COMPUTE_UUID = compute_uuid
    session = clients.PrefixedSession(
        prefix_url=config['placement']['endpoint'])
    session.headers.update({'x-auth-token': 'admin',
                            'openstack-api-version': 'placement latest',
                            'accept': 'application/json',
                            'content-type': 'application/json'})
    # Inventory is "FOO:1,BAR:2, BAZ:8"
    inventory_dict = _calculate_inventory()
    _print(inventory_dict)

    inventories_dict = {}
    for resource_class, value in inventory_dict.items():
        inventories_dict[resource_class] = {
            'total': int(value)
            # For now use defaults for the rest of the fields
        }

    if not confirm_resource_provider(session, compute_uuid, inventories_dict):
        generation = _create_resource_provider(session, compute_uuid)
        _set_inventory(session, compute_uuid, generation, inventories_dict)

    LOCK_INVENTORY = _create_lock_inventory(
        session, compute_uuid, inventories_dict)

    main_loop(config, compute_uuid)


def confirm_resource_provider(session, rp_uuid, inventories):
    """Check for resource provider and reset inventory."""
    url = '/resource_providers/%s/usages' % rp_uuid
    resp = session.get(url)
    if resp:
        data = resp.json()
        generation = data['resource_provider_generation']
        usage = ', '.join(
            ['%s: %s' % (rc, value) for rc, value in data['usages'].items()])
        _print('Existing resource provider with gen %s '
               'found with usages: %s.' % (generation, usage))
        _set_inventory(session, rp_uuid, generation, inventories)
        return True
    return False


def _create_lock_inventory(session, rp_uuid, inventories):
    """Return a function that will lock inventory for this rp."""
    def _lock_inventory():
        rp_url = '/resource_providers/%s' % rp_uuid
        inv_url = rp_url + '/' + 'inventories'
        resp = session.get(rp_url)
        if resp:
            data = resp.json()
            generation = data['generation']
        else:
            _print('failed to lock inventory, no rp')
            return False
        inventories['VCPU']['reserved'] = inventories['VCPU']['total']
        data = {
            'inventories': inventories,
            'resource_provider_generation': generation,
        }
        resp = session.put(inv_url, json=data)
        if resp:
            _print('locking inventory by reserving VCPU')
            return True
        else:
            _print('failed to lock inventory, no write inv')
            return False
    return _lock_inventory


def _calculate_inventory():
    cpu = psutil.cpu_count()
    # We only measure this disk space of where we store images.
    # Clearly disk in use matters here, but we don't know what's
    # images and what's other stuff, so fake it for now.
    disk = psutil.disk_usage('.').total // 1024 // 1024 // 1024
    memory = psutil.virtual_memory().total // 1024 // 1024
    return {
        'VCPU': cpu,
        'DISK_GB': disk,
        'MEMORY_MB': memory,
    }


def handle_build(instance, response):
    if response is False:
        _print('updating etcd for dead instance: %s' % instance)
        CLIENT.delete('/booted/%s' % instance)
    else:
        _print('updating etcd for instance %s with ip %s' % (
            instance, response))
        CLIENT.put('/booted/%s' % instance, response)


def handle_error(exc):
    _print('child saw %s' % exc)


def main_loop(config, compute_uuid):
    """Listen for changes on the key for this host."""

    our_key = '%s/%s/' % (KEY, compute_uuid)
    events_iterator, cancel = CLIENT.watch_prefix(our_key)

    cpu_count = multiprocessing.cpu_count() // 2
    with multiprocessing.Pool(processes=cpu_count) as pool:
        for event in events_iterator:
            value = str(event.value, 'UTF-8')
            data = json.loads(value)
            instance = data['instance']
            success = functools.partial(handle_build, instance)
            error = handle_error

            _print('PREPPING ASYNC for %s' % instance)
            args = (config, data)
            pool.apply_async(_handle_new, args, {}, success, error)
    # Shouldn't reach here.
    sys.exit(0)


def _handle_new(config, data):
    """Note the spawn, by sending the ip address to /booted."""
    # And we would want to fail and unclaim (here or in
    # the scheduler?), sometimes.
    _print('MANAGE INSTANCE %(instance)s WITH IMAGE %(image)s' % data)
    _print('\tALLOCATIONS ARE %(allocations)s' % data)
    if data['allocations']:
        _spawn(config, data)
        ip_address = _get_ip(data['instance'])
        _print('\tIP is %s' % ip_address)
        return ip_address
    elif 'allocations' in data:
        instance = data['instance']
        _destroy(instance)
        del data['instance']
        del data['image']
        # FIXME dupe
        session = clients.PrefixedSession(
            prefix_url=config['placement']['endpoint'])
        session.headers.update({'x-auth-token': 'admin',
                                'openstack-api-version': 'placement latest',
                                'accept': 'application/json',
                                'content-type': 'application/json'})
        resp = session.put('/allocations/%s' % instance, json=data)
        if resp:
            return False
        else:
            _print('\tINCOMPLETE DESTROY %s: %s' % (instance, resp))
            sys.exit(1)
    else:
        _print('\thandle_new with weird data %s: %s' % (instance, data))
        sys.exit(1)


def _spawn(config, data):
    compute_uuid = config['uuid']
    image = data['image']
    instance = data['instance']
    allocations = data['allocations'][compute_uuid]['resources']
    _print(allocations)
    memory = allocations['MEMORY_MB']
    vcpu = allocations['VCPU']
    disk_size = allocations['DISK_GB']
    dest = _copy_image(config, image, instance, disk_size)
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
            '--network', 'network:default',
            ]
    bridge = config['bridge']
    if bridge:
        args.extend(['--network', 'bridge:%s' % bridge])
    _print('spawning %s' % args)
    subprocess.Popen(args)
    _print('spawned %s' % args)


def _destroy(instance):
    conn = libvirt.open('qemu:///system')
    dom = conn.lookupByName(instance)
    if dom:
        dom.destroy()
        dom.undefine()
        img = '%s.img' % instance
        os.unlink(img)


def _get_ip(instance):
    count = 100
    # limit looping
    while count > 0:
        try:
            output = subprocess.check_output(['virsh', 'domifaddr', instance])
            output = str(output)
            output = output.replace('\n', ' ').rstrip()
            output = output.split()[-1].split('/')[0]
            if re.match(r'^\d+\.\d+\.\d+.\d+', output):
                return output
        except subprocess.CalledProcessError:
            pass
        count = count - 1
        time.sleep(1)


# lock this whole thing for now
def _copy_image(config, source, instance, size):
    # we only want this in the child so create it there
    CACHED_SESSION = cachecontrol.CacheControl(
        requests.Session(),
        cache=file_cache.FileCache('.web_cache'),
        serializer=MySerializer())
    _print('%s waiting for image lock' % instance)
    with LOCK:
        _print('%s acquired for lock' % instance)
        # source is expected to be a url
        source_file = source.rsplit('/', 1)[1]
        try:
            os.unlink(source_file)
        except FileNotFoundError:
            pass
        _print('Fetching image from %s' % source)
        source_refresh = CACHED_SESSION.get(source, stream=True)
        _print('Writing source image %s' % source_file)
        with open(source_file, 'wb') as sf:
            shutil.copyfileobj(source_refresh.raw, sf)

        _print('Creating instance image %s' % source_file)
        # Getting the image is separate from resizing.
        dest = '%s.img' % instance
        # FIXME: error handling
        # FIXME: we can't assume the filesystem, but for now we do.
        env = {
            # Needed on some esxi hosts.
            'LIBGUESTFS_BACKEND_SETTINGS': 'force_tcg',
        }
        if config['resize']:
            subprocess.check_call(['truncate', '-r', source_file, dest])
            subprocess.check_call(['truncate', '-s', '%sG' % size, dest])
            subprocess.check_call(['virt-resize', '--expand', '/dev/sda1',
                                   source_file, dest], env=env)
        else:
            # FIXME: this makes too many assumptions about image format
            # subprocess.check_call(['qemu-img', 'create', '-f', 'qcow2',
            #                        '-b', source_file,
            #                        dest])
            # And this is space wasteful.
            shutil.copyfile(source_file, dest)
    return dest


def _set_inventory(session, uuid, generation, inventory):
    """Set the inventory."""
    url = '/resource_providers/%s/inventories' % uuid
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
    url = '/resource_providers'
    data = {'uuid': uuid, 'name': uuid}
    resp = session.post(url, json=data)
    if resp:
        return resp.json()['generation']
    _print('failed to create provider')
    sys.exit(1)


def _configure():
    # let the possible exceptions bubble
    if os.path.exists('compute.yaml'):
        return yaml.safe_load(io.open('compute.yaml').read())
    else:
        return {}


def run():
    global CONFIG, CLIENT
    config = conf.configure(CONFIG, 'compute.yaml')
    _print(config)
    if config['etcd']:
        CLIENT = etcd3.client(**config['etcd'])
    else:
        CLIENT = etcd3.client()
    main(config)
