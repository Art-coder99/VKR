import os
from decimal import Decimal
from sqlalchemy import select
from .db import Base, engine, SessionLocal
from .models import User, Organization, Service
from .security import hash_password

PRICES = {
    'Заправка чёрного картриджа': 100,
    'Заправка цветного картриджа': 150,
    'Заправка двойного картриджа': 180,
    'Восстановление картриджа': 200,
    'Профилактика принтера': 300,
    'Диагностика принтера': 100,
    'Ремонт принтера (базовый)': 350,
    'Установка ОС': 250,
    'Активация ОС': 250,
    'Установка драйверов': 100,
    'Чистка компьютера': 200,
    'Замена термопасты': 150,
    'Настройка сети': 200,
    'Настройка принтера': 150,
    'Удалённая техническая поддержка': 150,
}

def initialize():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.scalar(select(User.id).limit(1)):
            admin_password = os.getenv('ADMIN_PASSWORD', 'Admin123!')
            db.add(User(username='director', full_name='Директор', password_hash=hash_password(admin_password), role='director'))
            for n in range(1, 5):
                db.add(User(username=f'employee{n}', full_name=f'Сотрудник {n}', password_hash=hash_password(os.getenv('EMPLOYEE_PASSWORD', 'Employee123!')), role='employee'))
        if not db.scalar(select(Organization.id).limit(1)):
            db.add(Organization(name='Учебная организация (пример)'))
        if not db.scalar(select(Service.id).limit(1)):
            for name, price in PRICES.items():
                db.add(Service(name=name, price=Decimal(price)))
        db.commit()
