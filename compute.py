
import io
import json
import os
import multiprocessing
import re
import shutil
import signal
import subprocess
import sys
import time
import threading
from urllib import parse
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

# Ignore SIGCHLD to see if we can avoid needing to reap.
signal.signal(signal.SIGCHLD, signal.SIG_IGN)

# Locked shared around the children.
LOCK = multiprocessing.Lock()

# deal with size limitations in CacheControl
class MySerializer(serialize.Serializer):

    def _loads_v4(self, request, data):
        try:
            cached = msgpack.loads(
                data, encoding="utf-8", max_bin_len=2147483647)
        except ValueError:
            return

        return self.prepare_response(request, cached)


KEY = '/hosts'
SLEEP = 1
COMPUTE_UUID = str(uuid.uuid4())
CLIENT = None


# default config
CONFIG = {
    'placement': {
        'endpoint': 'http://localhost:8080',
    },
    'etcd': {},
    # Do we resize disk images. Set to False when
    # doing testing and experimenting or because that's
    # just what you want. Means allocations are not accurate.
    'resize': True,
}


def _print(output):
    print('%s: PID: %s [%s] %s' % (time.time(), os.getpid(), COMPUTE_UUID, output))


def main(config):
    """Set up the resource provider for this compute and start
    the main loop.
    """
    session = clients.PrefixedSession(prefix_url=config['placement']['endpoint'])
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

    generation = _create_resource_provider(session, COMPUTE_UUID)
    _set_inventory(session, COMPUTE_UUID, generation, inventories_dict)

    main_loop(config, session, COMPUTE_UUID)


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


def main_loop(config, session, compute_uuid):
    """Listen for changes on the key for this host."""
    global CLIENT

    our_key = '%s/%s/' % (KEY, compute_uuid)
    events_iterator, cancel = CLIENT.watch_prefix(our_key)

    while True:
        try:
            for event in events_iterator:
                p = multiprocessing.Process(target=_handle_new, args=(config, session, event.value, cancel))
                p.start()
        except etcd3.exceptions.Etcd3Exception as exc:
            _print('\tRETRY main loop: %s' % exc)
            CLIENT = etcd3.client(**config['etcd'])
            events_iterator, cancel = CLIENT.watch_prefix(our_key)


def _handle_new(config, session, instance_data, cancel):
    """Note the spawn, by sending the ip address to /booted."""
    global CLIENT
    # And we would want to fail and unclaim (here or in
    # the scheduler?), sometimes.
    # maybe shutdown the etcd3 watcher in the subprocess?
    cancel()
    value = str(instance_data, 'UTF-8')
    data = json.loads(value)
    _print('MANAGE INSTANCE %(instance)s WITH IMAGE %(image)s' % data)
    _print('\tALLOCATIONS ARE %(allocations)s' % data)
    if data['allocations']:
        _spawn(config, data)
        ip_address = _get_ip(data['instance'])
        _print('\tIP is %s' % ip_address)
        while True:
            try: 
                #etcd3.client(**config['etcd']).put('/booted/%(instance)s' % data, ip_address)
                CLIENT.put('/booted/%(instance)s' % data, ip_address)
                break
            except etcd3.exceptions.Etcd3Exception as exc:
                _print('\tRETRY update ip: %s' % exc)
                time.sleep(1)
                CLIENT = etcd3.client(**config['etcd'])
        sys.exit(0)
    elif 'allocations' in data:
        instance = data['instance']
        _destroy(instance)
        del data['instance']
        del data['image']
        resp = session.put('/allocations/%s' % instance, json=data)
        if resp:
            # new connection to etcd
            while True:
                try:
                    #etcd3.client(**config['etcd']).delete('/booted/%s' % instance)
                    CLIENT.delete('/booted/%s' % instance)
                    break
                except etcd3.exceptions.Etcd3Exception as exc:
                    _print('\tRETRY clear instance: %s' % exc)
                    time.sleep(1)
                    CLIENT = etcd3.client(**config['etcd'])
            _print('\tDESTROYED %s' % instance)
            sys.exit(0)
        else:
            _print('\tINCOMPLETE DESTROY %s: %s' % (instance, resp))
            sys.exit(1)
    else:
        _print('\thandle_new with weird data %s: %s' % (instance, data))
        sys.exit(1)


def _spawn(config, data):
    image = data['image']
    instance = data['instance']
    allocations = data['allocations'][COMPUTE_UUID]['resources']
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
            ]
    _print('spawning %s' % args)
    subprocess.Popen(args)
    _print('spawned %s' % args)


def _destroy(instance):
    conn = libvirt.open('qemu:///system')
    dom = conn.lookupByName(instance)
    if dom:
        dom.destroy()
        dom.undefine()


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
            #subprocess.check_call(['qemu-img', 'create', '-f', 'qcow2',
            #                       '-b', source_file,
            #                       dest])
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


if __name__ == '__main__':
    config = conf.configure(CONFIG, 'compute.yaml')
    print(config)
    if config['etcd']:
        CLIENT = etcd3.client(**config['etcd'])
    else:
        CLIENT = etcd3.client()
    main(config)
