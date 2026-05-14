from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/api/settings", tags=["settings"])

MANAGED_KEYS = [
    "anthropic_api_key",
    "anthropic_model",
    "tavily_api_key",
    "ollama_base_url",
    "ollama_model",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "email_from",
]


@router.get("", response_model=List[schemas.SettingOut])
def list_settings(db: Session = Depends(get_db)):
    settings_list = db.query(models.AppSettings).all()
    # Ascunde valorile pentru campuri sensibile
    result = []
    for s in settings_list:
        val = s.value
        if s.key in ("anthropic_api_key", "tavily_api_key", "smtp_password") and val:
            val = val[:6] + "..." + val[-4:] if len(val) > 10 else "***"
        result.append({"key": s.key, "value": val, "updated_at": s.updated_at})
    return result


@router.put("/{key}", response_model=schemas.SettingOut)
def upsert_setting(key: str, payload: schemas.SettingUpdate, db: Session = Depends(get_db)):
    setting = db.query(models.AppSettings).filter(models.AppSettings.key == key).first()
    if setting:
        setting.value = payload.value
        setting.updated_at = datetime.now()
    else:
        setting = models.AppSettings(key=key, value=payload.value)
        db.add(setting)
    db.commit()
    db.refresh(setting)

    # Actualizeaza si settings-ul in memorie
    from app.config import settings as app_settings
    if hasattr(app_settings, key):
        setattr(app_settings, key, payload.value)

    return {"key": setting.key, "value": setting.value, "updated_at": setting.updated_at}
