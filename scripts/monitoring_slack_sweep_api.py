"""Read-only, bounded GitHub transport for an automatic execution recovery batch."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

from scripts.dataset_mutation_authorization import GitHub

GET_LIMIT = 400


class BoundedGitHub(GitHub):
    """Enumerate completely or fail loudly; never accept a truncated proof history."""
    def __init__(self):
        self.remaining = GET_LIMIT

    def get(self, path):
        self.require(self.remaining > 0, "execution recovery GitHub read budget exhausted")
        self.remaining -= 1
        return super().get(path)

    def pages(self, path, *, field=None):
        url = urlsplit(path)
        query = dict(parse_qsl(url.query))
        query['per_page'] = '100'
        rows, total, page = [], None, 1
        while True:
            value = self.get(url.path + '?' + urlencode({**query, 'page': page}))
            if field is not None:
                self.require(isinstance(value, dict) and type(value.get('total_count')) is int,
                             "invalid named API page")
                if total is None:
                    total = value['total_count']
                self.require(value['total_count'] == total, "inconsistent API total_count")
                value = value.get(field)
            self.require(isinstance(value, list) and all(isinstance(row, dict) for row in value),
                         "invalid API page entries")
            rows.extend(value)
            if len(value) < 100:
                self.require(total is None or total == len(rows), "incomplete API enumeration")
                return rows
            page += 1
