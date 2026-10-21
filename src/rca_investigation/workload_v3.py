"""Prepared checkout, fresh auth and bounded shipping availability measurements."""
from datetime import datetime, timezone
from time import perf_counter
import json
from math import isfinite
import requests
from .workload_v2 import CheckoutWorkload
from .workload import SockShopWorkload


class PreparationFailed(RuntimeError):
    pass


def operation_succeeded(row):
    expected = {'auth': 200, 'checkout': 201, 'shipping_health': 200}
    return row.get('operation') in expected and row.get('outcome') == 'completed' and row.get('http_status') == expected[row['operation']]


def affordable_product(payload):
    if not isinstance(payload, list) or not payload:
        raise PreparationFailed('catalogue preparation requires a nonempty product list')
    affordable = []
    for product in payload:
        if not isinstance(product, dict) or not isinstance(product.get('id'), str) or not product['id'].strip():
            raise PreparationFailed('catalogue product has no valid ID')
        price = product.get('price')
        if isinstance(price, bool) or not isinstance(price, (str, int, float)):
            raise PreparationFailed('catalogue product has no valid price')
        try:
            price = float(price)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PreparationFailed('catalogue product has no valid price') from exc
        if not isfinite(price):
            raise PreparationFailed('catalogue product price must be finite')
        if 0 < price < 80: affordable.append((price, product['id']))
    if not affordable:
        raise PreparationFailed('no product below demo payment limit')
    return min(affordable, key=lambda item: item[0])[1]


class WorkloadV3(CheckoutWorkload):
    def __init__(self, *args, auth_session_factory=requests.Session, shipping_probe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_session_factory = auth_session_factory
        self.shipping_probe = shipping_probe
        self.basket_prepared = False

    def prepare(self):
        self.basket_prepared = self.authenticated = False
        self.item_id = None
        try:
            # Keep v1/v2 preparation unchanged; v3 validates recovery payloads.
            result = SockShopWorkload.prepare(self)
            catalogue = self.session.get(self.base_url + '/catalogue', timeout=5)
            catalogue.raise_for_status()
            self.item_id = affordable_product(catalogue.json())
            self.reset()
            added = self.session.post(self.base_url + '/cart', json={'id': self.item_id}, timeout=5)
            if added.status_code != 201:
                raise PreparationFailed('healthy basket creation failed')
            verified = self.session.get(self.base_url + '/cart', timeout=5)
            verified.raise_for_status()
            basket = verified.json()
            if not isinstance(basket, list) or any(not isinstance(item, dict) for item in basket):
                raise PreparationFailed('healthy basket response must be an item list')
            if not any(item.get('itemId', item.get('id')) == self.item_id for item in basket):
                raise PreparationFailed('healthy basket verification failed')
            self.basket_prepared = True
            return {**result, 'basket_prepared': True, 'shipping_scope': 'service_availability_only'}
        except PreparationFailed:
            self.authenticated = False
            raise
        except (RuntimeError, requests.RequestException, ValueError, KeyError, TypeError) as exc:
            self.authenticated = False
            raise PreparationFailed('healthy workload preparation failed') from exc

    def checkout(self):
        if not self.authenticated or not self.basket_prepared:
            raise PreparationFailed('checkout needs a healthy prepared session and basket')
        return self._observe(self.session, 'POST', '/orders', 'checkout', json={})

    def _observe(self, session, method, route, operation, **kwargs):
        started = perf_counter()
        row = {'resource_id': 'front-end', 'operation': operation}
        try:
            response = session.request(method, self.base_url + route, timeout=5, **kwargs)
            successful = response.status_code == (200 if operation == 'auth' else 201)
            if successful and operation == 'auth':
                successful = 'logged_in' in session.cookies
            elif successful:
                try:
                    payload = response.json()
                    successful = isinstance(payload, dict) and bool(payload.get('id'))
                except ValueError:
                    successful = False
            row.update(http_status=response.status_code,
                       outcome='completed' if successful or response.status_code >= 500 else 'business_failure')
            if operation == 'auth':
                row['auth_refused'] = response.status_code in (401, 403)
        except requests.RequestException as exc:
            row.update(http_status=None, outcome='transport_failure',
                       transport_reason='timeout' if isinstance(exc, requests.Timeout) else 'connection_error')
        row.update(duration_ms=(perf_counter()-started)*1000, sample_time=datetime.now(timezone.utc).isoformat())
        return row

    def fresh_auth(self):
        with self.auth_session_factory() as session:
            if session.cookies:
                raise PreparationFailed('auth measurement requires a fresh unauthenticated session')
            return self._observe(session, 'GET', '/login', 'auth', auth=('user', 'password'))

    def shipping_health(self):
        if self.shipping_probe is None:
            raise PreparationFailed('shipping availability probe has not been registered')
        row = self.shipping_probe()
        if row.get('resource_id') != 'shipping' or row.get('operation') != 'shipping_health':
            raise ValueError('shipping operation identity mismatch')
        return row


def shipping_health_probe(driver):
    """Use the existing front-end's Node runtime to reach the lab network.

    A healthy application /health result establishes service availability only.
    The shipping POST handler accepts even a failed queue send, so it cannot
    establish completed fulfilment.
    """
    driver.state('front-end')
    code = """var http=require('http'); var start=Date.now();
var done=false; function finish(row){if(done)return;done=true;
row.resource_id='shipping';row.operation='shipping_health';
row.duration_ms=Date.now()-start;row.sample_time=new Date().toISOString();
process.stdout.write(JSON.stringify(row));}
var req=http.get('http://shipping:80/health',function(res){var body='';
res.on('data',function(chunk){body+=chunk;if(body.length>8192)req.destroy(new Error('oversize'));});
res.on('end',function(){var ok=false;try{var p=JSON.parse(body);ok=res.statusCode===200 &&
Array.isArray(p.health)&&p.health.some(function(x){return x.service==='shipping'&&x.status==='OK';});}catch(e){}
finish({http_status:res.statusCode,outcome:ok||res.statusCode>=500?'completed':'business_failure'});});});
req.setTimeout(5000,function(){finish({http_status:null,outcome:'transport_failure',transport_reason:'timeout'});req.abort();});
req.on('error',function(e){finish({http_status:null,outcome:'transport_failure',
transport_reason:e.message==='timeout'?'timeout':'connection_error'});});"""
    output, _ = driver.command(['exec', driver.ids['front-end'], 'node', '-e', code], timeout=10)
    return json.loads(output)
