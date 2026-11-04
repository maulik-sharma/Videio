"""Admin API routes."""

import json

from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, text
import uuid

from app.api.deps import CurrentAdmin, DbDep, WriteSession
from app.core.errors import AppError
from app.db.models import Video
from app.services.audit import log_action

router = APIRouter(prefix="/api/admin", tags=["admin"])