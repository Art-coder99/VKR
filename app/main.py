import io
import os
import secrets
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from xml.sax.saxutils import escape

from .db import SessionLocal
from .models import User, Organization, Service, WorkRecord, AuditLog, now_utc
from .security import hash_password, verify_password
from .seed import initialize

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title='ARUM-PRIOR — электронный журнал')
app.add_middleware(SessionMiddleware, secret_key=os.getenv('SECRET_KEY', 'CHANGE_ME_LOCAL_DEMO_SECRET_123'), same_site='lax', https_only=False)
app.mount('/static', StaticFiles(directory=ROOT/'app'/'static'), name='static')
templates = Jinja2Templates(directory=str(ROOT/'app'/'templates'))

@app.on_event('startup')
def on_startup():
    initialize()

def see(request, template, **context):
    return templates.TemplateResponse(request, template, {'request': request, **context})

def goto(path):
    return RedirectResponse(path, status_code=303)

def csrf(request):
    token = request.session.get('csrf')
    if not token:
        token = secrets.token_urlsafe(24)
        request.session['csrf'] = token
    return token

def check_csrf(request, token):
    expected = request.session.get('csrf', '')
    if not expected or not secrets.compare_digest(token, expected):
        raise HTTPException(403, 'Недействительный токен формы')

def current_user(request, db):
    uid = request.session.get('user_id')
    user = db.get(User, uid) if isinstance(uid, int) else None
    return user if user and user.active else None

def must_user(request, db):
    user = current_user(request, db)
    if not user:
        raise HTTPException(401, 'Войдите в систему: /login')
    return user

def must_director(request, db):
    user = must_user(request, db)
    if user.role != 'director':
        raise HTTPException(403, 'Доступ только директору')
    return user

def context(request, db, **kwargs):
    return dict(user=must_user(request, db), csrf=csrf(request), **kwargs)

def choice(db, cls, item_id, only_active=False):
    item = db.get(cls, item_id)
    if not item or (only_active and not item.active):
        raise HTTPException(400, 'Выбранный элемент не найден или неактивен')
    return item

def record_query():
    return select(WorkRecord).where(WorkRecord.deleted_at.is_(None)).options(selectinload(WorkRecord.user), selectinload(WorkRecord.organization), selectinload(WorkRecord.service))

def get_record(db, record_id, user):
    record = db.get(WorkRecord, record_id)
    if not record or record.deleted_at is not None:
        raise HTTPException(404, 'Запись не найдена')
    if user.role != 'director' and record.user_id != user.id:
        raise HTTPException(403, 'Нельзя изменять чужую запись')
    return record

def audit(db, user, record, action, details):
    db.add(AuditLog(user_id=user.id, record_id=record.id, action=action, details=details))

@app.get('/', response_class=HTMLResponse)
def root(request: Request):
    return goto('/journal' if request.session.get('user_id') else '/login')

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    return see(request, 'login.html', csrf=csrf(request), error=None)

@app.post('/login')
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username.strip(), User.active.is_(True)))
        if not user or not verify_password(password, user.password_hash):
            return see(request, 'login.html', csrf=csrf(request), error='Неверный логин или пароль')
        request.session.clear()
        request.session['user_id'] = user.id
        csrf(request)
        return goto('/journal')

@app.post('/logout')
def logout(request: Request, csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    request.session.clear()
    return goto('/login')

@app.get('/journal', response_class=HTMLResponse)
def journal(request: Request, month: str = '', organization_id: int = 0):
    with SessionLocal() as db:
        ctx = context(request, db)
        query = record_query().order_by(WorkRecord.work_date.desc(), WorkRecord.id.desc())
        if month:
            try:
                year, mon = map(int, month.split('-'))
                if len(month) != 7: raise ValueError()
                from calendar import monthrange
                first, last = date(year, mon, 1), date(year, mon, monthrange(year, mon)[1])
                query = query.where(WorkRecord.work_date >= first, WorkRecord.work_date <= last)
            except ValueError:
                raise HTTPException(400, 'Некорректный месяц')
        if organization_id:
            query = query.where(WorkRecord.organization_id == organization_id)
        records = db.scalars(query.limit(500)).all()
        total = sum((r.price for r in records), Decimal('0'))
        orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
        return see(request, 'journal.html', records=records, total=total, orgs=orgs, month=month, org_id=organization_id, **ctx)

@app.get('/work/new', response_class=HTMLResponse)
def new_work(request: Request):
    with SessionLocal() as db:
        ctx = context(request, db)
        return see(request, 'work_form.html', record=None, today=date.today().isoformat(),
                   organizations=db.scalars(select(Organization).where(Organization.active.is_(True)).order_by(Organization.name)).all(),
                   services=db.scalars(select(Service).where(Service.active.is_(True)).order_by(Service.name)).all(), **ctx)

@app.post('/work/new')
def create_work(request: Request, work_date: date = Form(...), organization_id: int = Form(...), service_id: int = Form(...), device_type: str = Form(...), model: str = Form(''), note: str = Form(''), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        user = must_user(request, db)
        choice(db, Organization, organization_id, True)
        service = choice(db, Service, service_id, True)
        if not device_type.strip() or len(device_type) > 70 or len(model) > 120 or len(note) > 1000:
            raise HTTPException(400, 'Проверьте заполнение полей')
        record = WorkRecord(work_date=work_date, organization_id=organization_id, service_id=service_id,
                            user_id=user.id, device_type=device_type.strip(), model=model.strip(), note=note.strip(), price=service.price)
        db.add(record)
        db.flush()
        audit(db, user, record, 'create', f'Создана запись; стоимость {record.price} MDL')
        db.commit()
    return goto('/journal')

@app.get('/work/{record_id}/edit', response_class=HTMLResponse)
def edit_work(request: Request, record_id: int):
    with SessionLocal() as db:
        user = must_user(request, db)
        record = get_record(db, record_id, user)
        ctx = context(request, db)
        return see(request, 'work_form.html', record=record, today=date.today().isoformat(),
                   organizations=db.scalars(select(Organization).order_by(Organization.name)).all(),
                   services=db.scalars(select(Service).order_by(Service.name)).all(), **ctx)

@app.post('/work/{record_id}/edit')
def update_work(request: Request, record_id: int, work_date: date = Form(...), organization_id: int = Form(...), service_id: int = Form(...), device_type: str = Form(...), model: str = Form(''), note: str = Form(''), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        user = must_user(request, db)
        record = get_record(db, record_id, user)
        choice(db, Organization, organization_id)
        service = choice(db, Service, service_id)
        if not device_type.strip() or len(device_type) > 70 or len(model) > 120 or len(note) > 1000:
            raise HTTPException(400, 'Проверьте заполнение полей')
        previous = f'{record.work_date} / {record.service.name} / {record.price} MDL / {record.model}'
        record.work_date, record.organization_id, record.device_type = work_date, organization_id, device_type.strip()
        record.model, record.note = model.strip(), note.strip()
        # Keep historical price if service did not change; changing service chooses its current price.
        if record.service_id != service_id:
            record.service_id, record.price = service_id, service.price
        audit(db, user, record, 'edit', f'До изменения: {previous}')
        db.commit()
    return goto('/journal')

@app.post('/work/{record_id}/delete')
def delete_work(request: Request, record_id: int, csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        user = must_user(request, db)
        record = get_record(db, record_id, user)
        record.deleted_at = now_utc()
        audit(db, user, record, 'delete', f'Удалено: {record.work_date} / {record.service.name} / {record.price} MDL')
        db.commit()
    return goto('/journal')

@app.get('/organizations', response_class=HTMLResponse)
def organizations(request: Request):
    with SessionLocal() as db:
        ctx = context(request, db)
        return see(request, 'organizations.html', items=db.scalars(select(Organization).order_by(Organization.name)).all(), **ctx)

@app.post('/organizations')
def add_organization(request: Request, name: str = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        must_director(request, db)
        name = name.strip()
        if not name or len(name)>160 or db.scalar(select(Organization).where(Organization.name==name)):
            raise HTTPException(400, 'Название пустое, слишком длинное или уже существует')
        db.add(Organization(name=name))
        db.commit()
    return goto('/organizations')

@app.post('/organizations/{item_id}/toggle')
def toggle_organization(request: Request, item_id: int, csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        must_director(request, db)
        item = choice(db, Organization, item_id)
        item.active = not item.active
        db.commit()
    return goto('/organizations')

@app.get('/services', response_class=HTMLResponse)
def services(request: Request):
    with SessionLocal() as db:
        ctx = context(request, db)
        return see(request, 'services.html', items=db.scalars(select(Service).order_by(Service.name)).all(), **ctx)

@app.post('/services')
def add_service(request: Request, name: str = Form(...), price: Decimal = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        must_director(request, db)
        name = name.strip()
        if not name or len(name)>150 or not price.is_finite() or price < 0 or price > 999999 or db.scalar(select(Service).where(Service.name == name)):
            raise HTTPException(400, 'Некорректное название или цена')
        db.add(Service(name=name, price=price.quantize(Decimal('0.01'))))
        db.commit()
    return goto('/services')

@app.post('/services/{item_id}/update')
def update_service(request: Request, item_id: int, price: Decimal = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        must_director(request, db)
        item = choice(db, Service, item_id)
        if not price.is_finite() or price < 0 or price > 999999:
            raise HTTPException(400, 'Некорректная цена')
        item.price = price.quantize(Decimal('0.01'))
        db.commit()
    return goto('/services')

@app.post('/services/{item_id}/toggle')
def toggle_service(request: Request, item_id: int, csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        must_director(request, db)
        item = choice(db, Service, item_id)
        item.active = not item.active
        db.commit()
    return goto('/services')

@app.get('/users', response_class=HTMLResponse)
def users(request: Request):
    with SessionLocal() as db:
        must_director(request, db)
        ctx = context(request, db)
        return see(request, 'users.html', items=db.scalars(select(User).order_by(User.id)).all(), **ctx)

@app.post('/users/{item_id}/password')
def change_password(request: Request, item_id: int, password: str = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    with SessionLocal() as db:
        must_director(request, db)
        user = db.get(User, item_id)
        if not user: raise HTTPException(404, 'Пользователь не найден')
        if len(password) < 10: raise HTTPException(400, 'Минимум 10 символов в пароле')
        user.password_hash = hash_password(password)
        db.commit()
    return goto('/users')

@app.get('/reports', response_class=HTMLResponse)
def reports(request: Request, month: str = '', organization_id: int = 0):
    with SessionLocal() as db:
        ctx = context(request, db)
        if ctx['user'].role != 'director': raise HTTPException(403, 'Только директор')
        month = month or date.today().strftime('%Y-%m')
        records, total = report_data(db, month, organization_id)
        orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
        return see(request, 'reports.html', records=records, total=total, orgs=orgs, month=month, org_id=organization_id, **ctx)

def report_data(db, month, organization_id):
    from calendar import monthrange
    try:
        if len(month) != 7: raise ValueError()
        year, mon = map(int, month.split('-'))
        start, end = date(year, mon, 1), date(year, mon, monthrange(year, mon)[1])
    except ValueError:
        raise HTTPException(400, 'Некорректный месяц отчёта')
    query = record_query().where(WorkRecord.work_date.between(start, end)).order_by(WorkRecord.work_date, WorkRecord.id)
    if organization_id:
        query = query.where(WorkRecord.organization_id == organization_id)
    records = db.scalars(query).all()
    return records, sum((r.price for r in records), Decimal('0'))

@app.get('/reports/export/{kind}')
def export(request: Request, kind: str, month: str, organization_id: int = 0):
    with SessionLocal() as db:
        must_director(request, db)
        records, total = report_data(db, month, organization_id)
        rows = [[r.work_date.strftime('%d.%m.%Y'), r.organization.name, r.service.name, r.device_type, r.model, r.user.full_name, float(r.price)] for r in records]
        if kind == 'xlsx':
            wb = Workbook(); ws = wb.active; ws.title = 'Работы'
            ws.append(['Дата', 'Организация', 'Услуга', 'Устройство', 'Модель', 'Сотрудник', 'Стоимость (MDL)'])
            for row in rows: ws.append(row)
            ws.append(['', '', '', '', '', 'ИТОГО:', float(total)])
            for cell in ws[1]:
                cell.font=Font(bold=True, color='FFFFFF'); cell.fill=PatternFill('solid', fgColor='1F4671')
            for col, width in {'A':15,'B':30,'C':34,'D':18,'E':20,'F':22,'G':19}.items(): ws.column_dimensions[col].width=width
            for i in range(2,ws.max_row+1): ws.cell(i,7).number_format='#,##0.00'
            out=io.BytesIO(); wb.save(out); out.seek(0)
            return StreamingResponse(out, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition':f'attachment; filename="arum_report_{month}.xlsx"'})
        if kind == 'pdf':
            out=io.BytesIO()
            doc=SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=30)
            font_path=next((p for p in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','C:/Windows/Fonts/arial.ttf','C:/Windows/Fonts/DejaVuSans.ttf'] if Path(p).exists()), None)
            if not font_path:
                raise HTTPException(500, 'Для кириллического PDF нужен шрифт DejaVuSans или Arial')
            if 'ARUM_CYR' not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont('ARUM_CYR',font_path))
            style=ParagraphStyle('ru',fontName='ARUM_CYR',fontSize=8,leading=11)
            title=ParagraphStyle('title_ru',parent=style,fontSize=14,leading=18,spaceAfter=10)
            data=[[Paragraph(escape(str(s)),style) for s in row] for row in [['Дата','Организация','Услуга','Устройство','Модель','Сотрудник','MDL']]]
            for row in rows:
                data.append([Paragraph(escape(str(v)),style) for v in row])
            data.append([Paragraph(escape(str(v)),style) for v in ['', '', '', '', '', 'ИТОГО', str(total)]])
            table=Table(data, colWidths=[65,125,155,98,98,120,80], repeatRows=1)
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DDEAF6')),('GRID',(0,0),(-1,-1),.3,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
            doc.build([Paragraph(f'ARUM-PRIOR — отчёт за {escape(month)}',title),Paragraph('Учебный отчёт о выполненных работах',style),Spacer(1,12),table])
            out.seek(0)
            return StreamingResponse(out,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="arum_report_{month}.pdf"'})
        raise HTTPException(404, 'Неизвестный формат')

@app.get('/audit', response_class=HTMLResponse)
def audit_page(request: Request):
    with SessionLocal() as db:
        must_director(request, db)
        ctx=context(request, db)
        items=db.scalars(select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.created_at.desc(),AuditLog.id.desc()).limit(200)).all()
        return see(request, 'audit.html', items=items, **ctx)
