import os
from datetime import datetime

import pytest

from energy_home import auth, automation, domain, gateways, reports, services
from energy_home.auth import AccessControl
from energy_home.domain import LOCAL_TZ
from energy_home.reports import ReportService
from energy_home.services import EnergyService
from energy_home.storage import Database

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def clock(monkeypatch):
    current = [datetime(2026, 11, 11, 14, 30, tzinfo=LOCAL_TZ)]
    for module in (auth, automation, domain, gateways, reports, services):
        monkeypatch.setattr(module, "now", lambda: current[0])
    return current


@pytest.fixture
def system(clock):
    db = Database(":memory:")
    access = AccessControl(db)
    energy = EnergyService(db, access)
    reports_service = ReportService(db, access)
    owner = access.register_home("Владелец", "owner", "testpassword", "Мой дом")
    access.add_family(owner, "Семья", "family", "testpassword")
    family = access.login("family", "testpassword")
    room = energy.add_room(owner, "Кухня")
    device = energy.add_device(owner, room, "Чайник", "KETTLE-1", "socket", 2000, 4000, True)
    yield db, access, energy, reports_service, owner, family, room, device
    db.close()
