from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.schemas import UserOut

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    """The logged-in user. Read from the replica unless they just wrote something."""
    return UserOut.model_validate(user)
