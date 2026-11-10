import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolated database for every test, even if app imported earlier.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import db as db_module
    from app import main as main_module
    from app.seed import PRICES
    from app.models import Base, User, Organization, Service
    from app.security import hash_password
    from fastapi.testclient import TestClient
    from decimal import Decimal
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}', connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main_module, 'SessionLocal', factory)
    # Avoid startup creating default data in another DB
    monkeypatch.setattr(main_module, 'initialize', lambda:None)
    with factory() as db:
        db.add_all([
            User(username='director',full_name='Директор',password_hash=hash_password('Admin123!'),role='director'),
            User(username='employee1',full_name='Сотрудник 1',password_hash=hash_password('Employee123!'),role='employee'),
            User(username='employee2',full_name='Сотрудник 2',password_hash=hash_password('Employee123!'),role='employee'),
            Organization(name='Организация А'),
            Service(name='Заправка',price=Decimal('100')),
            Service(name='Профилактика',price=Decimal('300'))
        ]);db.commit()
    with TestClient(main_module.app) as c:
        yield c, factory
    engine.dispose()

def token(client):
    return re.search(r'name="csrf_token" value="([^"]+)"',client.get('/login').text).group(1)

def auth(client, username, password):
    t=token(client)
    r=client.post('/login',data={'username':username,'password':password,'csrf_token':t},follow_redirects=False)
    assert r.status_code==303
    return token_from_journal(client)

def token_from_journal(client):
    return re.search(r'name="csrf_token" value="([^"]+)"',client.get('/journal').text).group(1)

def make_work(client, csrf_token, service_id=1):
    return client.post('/work/new',data={'csrf_token':csrf_token,'work_date':'2026-11-09','organization_id':'1','service_id':str(service_id),'device_type':'Картридж','model':'720','note':'чёрный'},follow_redirects=False)

def test_employee_create_and_report(client):
    c, factory=client
    t=auth(c,'employee1','Employee123!')
    assert make_work(c,t).status_code==303
    assert '720' in c.get('/journal').text
    assert c.get('/reports').status_code==403
    assert c.post('/services',data={'csrf_token':t,'name':'Новая','price':1}).status_code==403
    c.post('/logout',data={'csrf_token':t})
    t=auth(c,'director','Admin123!')
    report=c.get('/reports?month=2026-11')
    assert report.status_code==200
    assert '100.00' in report.text
    xlsx=c.get('/reports/export/xlsx?month=2026-11')
    assert xlsx.status_code==200 and xlsx.content[:2]==b'PK'
    pdf=c.get('/reports/export/pdf?month=2026-11')
    assert pdf.status_code==200 and pdf.content[:4]==b'%PDF'

def test_permissions_and_historical_price(client):
    c, factory=client
    t=auth(c,'employee1','Employee123!')
    assert make_work(c,t).status_code==303
    from app.models import WorkRecord, Service, AuditLog
    with factory() as db:
        r=db.query(WorkRecord).first();rid=r.id
    c.post('/logout',data={'csrf_token':t})
    t=auth(c,'employee2','Employee123!')
    assert c.get(f'/work/{rid}/edit').status_code==403
    assert c.post(f'/work/{rid}/delete',data={'csrf_token':t}).status_code==403
    c.post('/logout',data={'csrf_token':t})
    t=auth(c,'director','Admin123!')
    assert c.post('/services/1/update',data={'csrf_token':t,'price':'170.00'},follow_redirects=False).status_code==303
    with factory() as db:
        assert db.query(WorkRecord).first().price==100
    assert c.post(f'/work/{rid}/delete',data={'csrf_token':t},follow_redirects=False).status_code==303
    assert '720' not in c.get('/journal').text
    with factory() as db:
        assert db.query(WorkRecord).first().deleted_at is not None
        assert db.query(AuditLog).count()==2

def test_csrf_and_invalid_login(client):
    c,_=client
    assert c.post('/login',data={'username':'director','password':'bad','csrf_token':token(c)}).status_code==200
    assert c.post('/login',data={'username':'director','password':'Admin123!','csrf_token':'bad'}).status_code==403
