
import copy
import io
import json
import os
import sys
from urllib import parse
import uuid

import etcd3
import requests
import yaml

# Replace with service catalog, but since right now we haven't
# got one, raw.
PREFIX = '/hosts'
PLACEMENT = 'http://localhost:8080'
IMAGE = 'http://download.cirros-cloud.net/0.3.6/cirros-0.3.6-x86_64-disk.img'
CLIENT = None

# default config
CONFIG = {
    'placement': {
        'endpoint': 'http://localhost:8080',
    },
    'etcd': {},
}


# FIXME: duped from compute.py
class PrefixedSession(requests.Session):
    def __init__(self, prefix_url=None, *args, **kwargs):
        self.prefix_url = prefix_url
        super(PrefixedSession, self).__init__(*args, **kwargs)

    def request(self, method, url, *args, **kwargs):
        if self.prefix_url:
            url = parse.urljoin(self.prefix_url, url)
        return super(PrefixedSession, self).request(method, url, *args, **kwargs)


def schedule(session, resources):
    """Given resources, find some hosts."""
    print(resources)
    url = '/allocation_candidates?%s' % resources
    resp = session.get(url)
    data = resp.json()
    if resp:
        success = _schedule(session, data)
        if not success:
            print('FAIL: no allocation available')
            sys.exit(1)
    else:
        print('FAIL: %s' % data)
        sys.exit(1)


def query(instance):
    """Get info about an instance from etcd."""
    info, meta = CLIENT.get('/booted/%s' % instance)
    print(info.decode('utf-8'))
    sys.exit(0)


def main(config, resources):
    """Establish session and call schedule."""
    if 'resources' in resources:
        session = PrefixedSession(prefix_url=config['placement']['endpoint'])
        session.headers.update({'x-auth-token': 'admin',
                                'openstack-api-version': 'placement latest',
                                'accept': 'application/json',
                                'content-type': 'application/json'})
        schedule(session, resources)
    else:
        query(resources)


def _schedule(session, data):
    """Try to schedule to one host.

    We start at the top of the available allocations and try to claim
    each one. If there is a successful claim, then we break the loop
    and are done. Otherwise we try the next allocation, continuing until
    we run out.
    """
    allocation_requests = data['allocation_requests']
    # Not (yet) used.
    # provider_summaries = data['provider_summaries']
    consumer = str(uuid.uuid4())
    image = IMAGE
    target = None
    while True:
        try:
            first_allocation = allocation_requests.pop(0)['allocations']
        except IndexError:
            print('NO ALLOCATIONS LEFT')
            break

        target = list(first_allocation.keys())[0]
        claim = {
            'allocations': first_allocation,
            'user_id': str(uuid.uuid4()),
            'project_id': str(uuid.uuid4()),
            'consumer_generation': None,
        }
        url = '/allocations/%s' % consumer
        resp = session.put(url, json=claim)
        if resp:
            message = copy.deepcopy(claim)
            message['instance'] = consumer
            message['image'] = image
            CLIENT.put('%s/%s' % (PREFIX, target), json.dumps(message))
            break
        else:
            print('CLAIM FAIL: %s' % resp.json())
            target = None
            continue

    if target:
        print('NOTIFIED TARGET, %s, OF INSTANCE %s' % (target, consumer))
        return True

    return False


# FIXME: duped with compute.py
def _configure():
    # let the possible exceptions bubble
    if os.path.exists('schedule.yaml'):
        return yaml.safe_load(io.open('schedule.yaml').read())
    else:
        return {}


if __name__ == '__main__':
    config = {}
    config.update(CONFIG)
    config.update(_configure())
    print(config)
    if config['etcd']:
        CLIENT = etcd3.client(**config['etcd'])
    else:
        CLIENT = etcd3.client()
    main(config, sys.argv[1])
