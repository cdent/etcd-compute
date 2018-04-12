
import copy
import json
import sys
import uuid

import etcd3
import requests

# Replace with service catalog, but since right now we haven't
# got one, raw.
PREFIX = '/hosts'
PLACEMENT = 'http://localhost:8080'

client = etcd3.client()


def schedule(session, resources):
    """Given resources, find some hosts."""
    print(resources)
    url = '%s/allocation_candidates?%s' % (PLACEMENT, resources)
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


def main(resources):
    """Establish session and call schedule."""
    session = requests.Session()
    session.headers.update({'x-auth-token': 'admin',
                            'openstack-api-version': 'placement latest',
                            'accept': 'application/json',
                            'content-type': 'application/json'})
    schedule(session, resources)


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
    image = str(uuid.uuid4())
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
        }
        url = '%s/allocations/%s' % (PLACEMENT, consumer)
        resp = session.put(url, json=claim)
        if resp:
            message = copy.deepcopy(claim)
            message['instance'] = consumer
            message['image'] = image
            client.put('%s/%s' % (PREFIX, target), json.dumps(message))
            break
        else:
            print('CLAIM FAIL: %s' % resp.json())
            target = None
            continue

    if target:
        print('NOTIFIED TARGET, %s, OF INSTANCE %s' % (target, consumer))
        return True

    return False


if __name__ == '__main__':
    main(sys.argv[1])
