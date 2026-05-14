from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.common import AuthOut, LoginRequest, RegisterRequest
from app.services.auth_service import authenticate_user, create_access_token, create_user, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthOut)
async def register(data: RegisterRequest) -> dict:
    user = create_user(data.name, data.password, data.role)
    return {"accessToken": create_access_token(user), "tokenType": "bearer", "user": user}


@router.post("/login", response_model=AuthOut)
async def login(data: LoginRequest) -> dict:
    user = authenticate_user(data.name, data.password)
    return {"accessToken": create_access_token(user), "tokenType": "bearer", "user": user}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return user
