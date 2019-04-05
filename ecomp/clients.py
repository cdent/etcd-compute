
from urllib import parse
import requests


class PrefixedSession(requests.Session):
    """A requests Session that optionally has a prefix of a full url."""
    def __init__(self, prefix_url=None, *args, **kwargs):
        self.prefix_url = prefix_url
        super(PrefixedSession, self).__init__(*args, **kwargs)

    def request(self, method, url, *args, **kwargs):
        if self.prefix_url:
            url = parse.urljoin(self.prefix_url, url)
        return super(PrefixedSession, self).request(
            method, url, *args, **kwargs)
