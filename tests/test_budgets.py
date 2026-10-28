import asyncio
from dataclasses import dataclass
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx2
import pytest

from valinator.app import create_app
from valinator.budgets import RequestBudgets, ResourceLimits, RequestCapacity
from valinator.engine import Engine
from valinator.models import CheckerResult, Policy, Rule


@dataclass
class Clock:
    now: float = 0
    def __call__(self): return self.now


def test_token_bucket_refills_caps_and_rounds_retry():
    clock = Clock()
    budgets = RequestBudgets(ResourceLimits(checks_per_minute=60, burst=2), ['a'], clock)
    for _ in range(2):
        with budgets.check('a'): pass
    clock.now = .2
    with pytest.raises(HTTPException) as exc:
        with budgets.check('a'): pass
    assert exc.value.status_code == 429
    assert exc.value.headers['Retry-After'] == '1'
    clock.now = 1
    with budgets.check('a'): pass
    clock.now = 100
    for _ in range(2):
        with budgets.check('a'): pass
    with pytest.raises(HTTPException):
        with budgets.check('a'): pass


def test_independent_applications_and_concurrency_release():
    budgets = RequestBudgets(ResourceLimits(burst=2, application_concurrency=1, process_concurrency=2), ['a','b'])
    with budgets.check('a'):
        with pytest.raises(HTTPException) as exc:
            with budgets.check('a'): pass
        assert exc.value.status_code == 429
        with budgets.check('b'): pass
    # Concurrency rejection did not consume a rate token.
    with budgets.check('a'): pass


def test_process_capacity_and_exception_cleanup():
    budgets = RequestBudgets(ResourceLimits(process_concurrency=1), ['a','b'])
    with pytest.raises(RuntimeError):
        with budgets.check('a'):
            with pytest.raises(HTTPException) as exc:
                with budgets.check('b'): pass
            assert exc.value.status_code == 503
            raise RuntimeError('synthetic')
    with budgets.check('b'): pass


def test_threads_cannot_race_past_application_limit():
    budgets = RequestBudgets(ResourceLimits(application_concurrency=1), ['a'])
    ready, finish = Barrier(2), Barrier(2)
    def hold():
        with budgets.check('a'):
            ready.wait(timeout=5)
            finish.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold)
        ready.wait(timeout=5)
        try:
            with pytest.raises(HTTPException):
                with budgets.check('a'): pass
        finally:
            finish.wait(timeout=5)
        future.result(timeout=5)
    with budgets.check('a'): pass


def body(app='demo'):
    return {'application_id':app,'policy_version':'v1','content':'Hello'}


def engine(checker=None):
    async def passed(*args):
        return CheckerResult(checker_id='local',checker_version='1',status='passed')
    return Engine([Policy(application_id=app,version='v1',rules=(Rule(checker_id='local'),))
                   for app in ['demo','other']], {'local': checker or passed})


def test_api_keys_and_stages_share_application_rate_without_cross_tenant_state():
    client = TestClient(create_app(engine=engine(), credentials={'one':'demo','two':'demo','three':'other'},
                                  limits=ResourceLimits(burst=1,checks_per_minute=1)))
    headers = lambda key: {'Authorization':'Bearer '+key}
    assert client.post('/v1/checks/input',headers=headers('one'),json=body()).status_code == 200
    result = client.post('/v1/checks/output',headers=headers('two'),json=body())
    assert result.status_code == 429
    assert 1 <= int(result.headers['Retry-After']) <= 60
    assert 'one' not in result.text and 'two' not in result.text and 'Hello' not in result.text
    assert client.post('/v1/checks/output',headers=headers('three'),json=body('other')).status_code == 200
    assert client.get('/healthz').status_code == 200
    metadata = client.get('/v1/applications/current',headers=headers('one'))
    assert metadata.status_code == 200
    assert metadata.json()['resource_limits']['scope'] == 'server_process'


def test_invalid_or_unauthorized_checks_do_not_spend_allowance():
    client = TestClient(create_app(engine=engine(),credentials={'one':'demo'},limits=ResourceLimits(burst=1)))
    headers = {'Authorization':'Bearer one'}
    assert client.post('/v1/checks/input',json=body()).status_code == 401
    assert client.post('/v1/checks/input',headers=headers,json=body('other')).status_code == 403
    assert client.post('/v1/checks/input',headers=headers,json={**body(),'policy_version':'unknown'}).status_code == 404
    assert client.post('/v1/checks/input',headers=headers,json={**body(),'content':''}).status_code == 422
    assert client.post('/v1/checks/input',headers=headers,json=body()).status_code == 200


def test_real_api_concurrency_limits_and_cancellation_cleanup():
    async def scenario():
        started = asyncio.Event()
        async def hold(*args):
            started.set()
            await asyncio.Event().wait()
        app = create_app(engine=engine(hold),credentials={'one':'demo','two':'other'},
                         limits=ResourceLimits(application_concurrency=1,process_concurrency=1))
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
            first = asyncio.create_task(client.post('/v1/checks/input',headers={'Authorization':'Bearer one'},json=body()))
            await asyncio.wait_for(started.wait(),1)
            same = await client.post('/v1/checks/output',headers={'Authorization':'Bearer one'},json=body())
            other = await client.post('/v1/checks/input',headers={'Authorization':'Bearer two'},json=body('other'))
            assert same.status_code == 429
            assert other.status_code == 503
            assert other.headers['Retry-After'] == '1'
            first.cancel()
            with pytest.raises(asyncio.CancelledError): await first
            # A new request reaches the checker after cancellation releases slots.
            started.clear()
            again = asyncio.create_task(client.post('/v1/checks/input',headers={'Authorization':'Bearer two'},json=body('other')))
            await asyncio.wait_for(started.wait(),1)
            again.cancel()
            with pytest.raises(asyncio.CancelledError): await again
    asyncio.run(scenario())


def test_outer_capacity_rejects_before_reading_body_and_releases_on_disconnect():
    async def scenario():
        budgets = RequestBudgets(ResourceLimits(ingress_concurrency=1), [])
        received = 0
        async def receive():
            nonlocal received
            received += 1
            return {'type':'http.disconnect'}
        async def downstream(scope, receive, send):
            await receive()
        app = RequestCapacity(downstream, budgets)
        scope = {'type':'http','method':'POST','path':'/v1/checks/input'}
        messages = []
        async def send(message): messages.append(message)
        with budgets.ingress():
            await app(scope,receive,send)
        assert messages[0]['status'] == 503
        assert received == 0
        await app(scope,receive,send)
        assert received == 1
        await app(scope,receive,send)
        assert received == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('value', ['0','-1','x','1.0',' 2','100001','1_000','１２','999999999'])
def test_invalid_environment_rejected(monkeypatch,value):
    monkeypatch.setenv('VALINATOR_CHECKS_PER_MINUTE',value)
    with pytest.raises(ValueError): ResourceLimits.from_environment()


def test_environment_limits_are_loaded(monkeypatch):
    monkeypatch.setenv('VALINATOR_BURST','3')
    client = TestClient(create_app(engine=engine(),credentials={'one':'demo'}))
    result = client.get('/v1/applications/current',headers={'Authorization':'Bearer one'})
    assert result.json()['resource_limits']['burst'] == 3


def test_api_ingress_gate_precedes_body_read_and_recovers():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        app = create_app(engine=engine(), credentials={'one':'demo'}, limits=ResourceLimits(ingress_concurrency=1))
        async def slow_body():
            started.set()
            await release.wait()
            yield b'{}'
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
            first = asyncio.create_task(client.post('/v1/checks/input',content=slow_body(),headers={'Content-Type':'application/json'}))
            try:
                await asyncio.wait_for(started.wait(),1)
                # No content type: if the JSON middleware ran first this would be 415.
                rejected = await client.post('/v1/checks/output',content=b'not-json')
                assert rejected.status_code == 503
                assert (await client.get('/healthz')).status_code == 200
            finally:
                release.set()
                await first
            assert (await client.post('/v1/checks/output',content=b'not-json')).status_code == 415
    asyncio.run(scenario())
