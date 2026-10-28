"""Bounded, paced JSON retrieval with same-endpoint pagination and no redirects."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = 'https://dadosabertos.camara.leg.br/api/v2'
MAX_BYTES = 2_000_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Ambiguous duplicate JSON key')
        value[key] = item
    return value


def _invalid_constant(_):
    raise ValueError('Non-finite JSON number')


class RetrievalError(ValueError):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code, self.status = code, status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RetrievalError('redirect_refused', code)


def transport(url: str, timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers={'Accept': 'application/json',
                                   'User-Agent': 'ObservatorioNDQ-development/0.4.2'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise RetrievalError('response_too_large')
            return response.status, body
    except HTTPError as exc:
        # Error bodies and exception text are deliberately not retained.
        status = exc.code
        exc.close()
        return status, b''


def safe_page(url: str, endpoint: str) -> str:
    """Pagination may change only pagina/itens, never source, resource or filters."""
    try:
        parsed, initial = urlsplit(url), urlsplit(endpoint)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if (parsed.scheme != 'https' or parsed.hostname != 'dadosabertos.camara.leg.br'
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.netloc != initial.netloc or parsed.path != initial.path
                or parsed.fragment or set(params) - {'pagina', 'itens'}
                or any(len(v) != 1 or not v[0].isdigit() or not 1 <= int(v[0]) <= 10000
                       for v in params.values())
                or int(params.get('itens', ['100'])[0]) > 100):
            raise RetrievalError('unsafe_pagination_url')
    except (ValueError, TypeError):
        raise RetrievalError('unsafe_pagination_url') from None
    return urlunsplit(('https', parsed.netloc, parsed.path,
                       urlencode(sorted((key, value[0]) for key, value in params.items())), ''))


@dataclass
class FetchResult:
    endpoint: str
    data: object = None
    complete: bool = False
    pages: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    def coverage(self) -> dict:
        return {'complete': self.complete, 'pages': self.pages, 'issues': self.issues,
                'scope': 'returned_endpoint_pages_not_exhaustive_institutional_history'}


class JsonClient:
    def __init__(self, *, timeout=15.0, retries=2, interval=1.0, max_pages=30,
                 max_items=10000, request=transport, sleep=time.sleep,
                 monotonic=time.monotonic, now=utc_now):
        if (not math.isfinite(timeout) or not 0 < timeout <= 120
                or not 0 <= retries <= 4 or not math.isfinite(interval) or interval < 0.5
                or not 1 <= max_pages <= 100 or not 1 <= max_items <= 10000):
            raise ValueError('Invalid bounded HTTP configuration')
        self.timeout, self.retries, self.interval = timeout, retries, interval
        self.max_pages, self.max_items = max_pages, max_items
        self.request, self.sleep, self.monotonic, self.now = request, sleep, monotonic, now
        self.last_request = None

    def _json(self, url):
        for attempt in range(self.retries + 1):
            if self.last_request is not None:
                self.sleep(max(0, self.interval - (self.monotonic() - self.last_request)))
            self.last_request = self.monotonic()
            try:
                status, body = self.request(url, self.timeout)
            except (TimeoutError, OSError, URLError):
                failure = RetrievalError('timeout_or_network_error')
            else:
                if 200 <= status < 300:
                    try:
                        value = json.loads(body, parse_constant=_invalid_constant, object_pairs_hook=_json_object)
                    except (ValueError, UnicodeError, RecursionError):
                        raise RetrievalError('invalid_json') from None
                    if not isinstance(value, dict):
                        raise RetrievalError('invalid_envelope')
                    return value
                failure = RetrievalError('http_error', status)
                if status not in (408, 429, 500, 502, 503, 504):
                    raise failure
            if attempt == self.retries:
                raise failure
            self.sleep(min(4.0, 0.5 * 2 ** attempt))

    def fetch(self, endpoint: str, *, many: bool) -> FetchResult:
        result = FetchResult(endpoint, [] if many else None)
        url, visited = endpoint, set()
        try:
            if not endpoint.startswith(BASE + '/proposicoes/'):
                raise RetrievalError('unsupported_endpoint')
            url = safe_page(endpoint, endpoint)
            while True:
                if url in visited:
                    raise RetrievalError('pagination_cycle')
                if len(visited) >= self.max_pages:
                    raise RetrievalError('page_limit')
                visited.add(url)
                payload = self._json(url)
                data = payload.get('dados')
                if ((many and (not isinstance(data, list) or any(not isinstance(x, dict) for x in data)))
                        or (not many and not isinstance(data, dict))):
                    raise RetrievalError('invalid_dados_shape')
                if many:
                    if len(result.data) + len(data) > self.max_items:
                        raise RetrievalError('item_limit')
                    result.data.extend(data)
                else:
                    result.data = data
                result.pages.append({'url': url, 'retrieved_at': self.now()})
                links = payload.get('links')
                if not isinstance(links, list) or any(not isinstance(x, dict) or not isinstance(x.get('rel'), str)
                                                      or not isinstance(x.get('href'), str) for x in links):
                    raise RetrievalError('pagination_metadata_missing_or_invalid')
                known = {}
                for link in links:
                    if link['rel'] in ('self', 'next', 'last', 'first', 'previous', 'prev'):
                        if link['rel'] in known:
                            raise RetrievalError('ambiguous_pagination_links')
                        known[link['rel']] = safe_page(link['href'], endpoint)
                if 'self' not in known or known['self'] != url:
                    raise RetrievalError('pagination_self_mismatch')
                nxt = known.get('next')
                if not nxt:
                    if 'last' in known and known['last'] != url:
                        raise RetrievalError('pagination_ended_before_last')
                    result.complete = True
                    return result
                if not many:
                    raise RetrievalError('unexpected_object_pagination')
                current_page = int(parse_qs(urlsplit(url).query).get('pagina', ['1'])[0])
                next_page = int(parse_qs(urlsplit(nxt).query).get('pagina', ['1'])[0])
                if next_page != current_page + 1:
                    raise RetrievalError('pagination_gap_or_cycle')
                old_size = parse_qs(urlsplit(url).query).get('itens')
                new_size = parse_qs(urlsplit(nxt).query).get('itens')
                if old_size != new_size:
                    if not (current_page == 1 and old_size is None and new_size
                            and len(data) == int(new_size[0])):
                        raise RetrievalError('pagination_page_size_changed')
                url = nxt
        except RetrievalError as exc:
            issue = {'code': exc.code}
            if exc.status is not None:
                issue['http_status'] = exc.status
            result.issues.append(issue)
            return result
