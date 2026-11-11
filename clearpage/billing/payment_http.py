"""Bounded JSON transport shared by the desktop and seller payment service."""
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import URLError, HTTPError


class PaymentUnavailable(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def json_request(url, body=None, headers=None):
    request = Request(url, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        # Never forward authentication to a redirected host.
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except (URLError, HTTPError, TimeoutError, OSError, ValueError) as error:
        raise PaymentUnavailable('Payment service unavailable. Check your connection and use Check payment before paying again.') from error
