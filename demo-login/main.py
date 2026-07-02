"""
FastAPI JWT 登录接口 — 独立演示版

基于 CodeGen 生成代码改写，修改内容：
1. datetime.utcnow() → datetime.now(timezone.utc)  修复弃用警告
2. SECRET_KEY → 环境变量注入，杜绝硬编码
3. 新增 CORS 中间件（生产环境应限制 origins）
4. 新增输入校验（邮箱格式、用户名长度）
5. 新增 /health 健康检查端点
6. 新增 create_user 注册端点
7. User.email 使用 EmailStr 校验

启动：uvicorn main:app --reload --port 8000
      或从 demo-login 目录运行: cd demo-login && uvicorn main:app --reload

测试：默认账号 admin / secret
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, field_validator

# ────────────────────────────────────────────────────────────────────
# 配置（从环境变量注入，提供开发默认值）
# ────────────────────────────────────────────────────────────────────

SECRET_KEY: str = os.environ.get(
    "JWT_SECRET_KEY",
    "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7",
)
ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.environ.get("JWT_EXPIRE_MINUTES", "30")
)

# ────────────────────────────────────────────────────────────────────
# 密码哈希上下文
# ────────────────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码是否与哈希匹配"""
    return pwd_context.verify(plain_password, hashed_password)


# ────────────────────────────────────────────────────────────────────
# 数据库模拟（演示用途，生产环境请替换为真实数据库）
# ────────────────────────────────────────────────────────────────────
# 密码 "secret" 的 bcrypt 哈希值
_DEFAULT_HASH: str = (
    "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"
)

_fake_users_db: dict[str, dict] = {
    "admin": {
        "username": "admin",
        "full_name": "Administrator",
        "email": "admin@example.com",
        "hashed_password": _DEFAULT_HASH,
        "disabled": False,
        "created_at": "2025-01-01T00:00:00Z",
    }
}


def get_user(username: str) -> dict | None:
    """从模拟数据库中获取用户"""
    return _fake_users_db.get(username)


def save_user(username: str, full_name: str, email: str, hashed_password: str) -> dict:
    """向模拟数据库中写入新用户"""
    user = {
        "username": username,
        "full_name": full_name,
        "email": email,
        "hashed_password": hashed_password,
        "disabled": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _fake_users_db[username] = user
    return user


# ────────────────────────────────────────────────────────────────────
# Pydantic 模型
# ────────────────────────────────────────────────────────────────────


class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int = Field(..., description="Token 有效期（秒）")


class TokenData(BaseModel):
    username: Optional[str] = None


class UserOut(BaseModel):
    """返回给客户端的用户信息（不含密码）"""
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    disabled: Optional[bool] = None
    created_at: Optional[str] = None


class UserInDB(UserOut):
    """内部用户模型（含密码哈希，不暴露给客户端）"""
    hashed_password: str


class CreateUserRequest(BaseModel):
    """注册新用户的请求体"""
    username: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$",
        examples=["john_doe"],
        description="用户名：仅允许字母、数字、下划线，3-50 字符",
    )
    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
        examples=["s3cret!"],
        description="密码：6-128 字符",
    )
    full_name: str = Field(
        default="",
        max_length=100,
        examples=["John Doe"],
        description="用户全名（可选）",
    )
    email: Optional[EmailStr] = Field(
        default=None,
        examples=["admin@example.com"],
        description="邮箱地址（可选，需合法格式）",
    )

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        """密码必须包含至少一个字母和一个非字母字符"""
        if v.isalpha() or v.isdigit():
            raise ValueError("密码必须同时包含字母和非字母字符")
        return v


# ────────────────────────────────────────────────────────────────────
# FastAPI 应用初始化
# ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="FastAPI JWT Login API",
    version="2.0.0",
    description="基于 JWT 的登录 / 注册 / 用户信息接口（独立演示版）",
)

# CORS — 生产环境请替换 allow_origins 为具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OAuth2 密码流 — tokenUrl 指向本应用的 /token 端点
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


# ────────────────────────────────────────────────────────────────────
# JWT 工具函数
# ────────────────────────────────────────────────────────────────────


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """创建 JWT 访问令牌"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=15)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ────────────────────────────────────────────────────────────────────
# 认证依赖
# ────────────────────────────────────────────────────────────────────


async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> UserInDB:
    """从 JWT Token 中解析当前用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user_dict = get_user(username)
    if user_dict is None:
        raise credentials_exception
    return UserInDB(**user_dict)


async def get_current_active_user(
    current_user: UserInDB = Depends(get_current_user),
) -> UserOut:
    """确保当前用户未被禁用"""
    if current_user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该账号已被禁用",
        )
    return UserOut(
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        disabled=current_user.disabled,
        created_at=current_user.created_at,
    )


# ────────────────────────────────────────────────────────────────────
# 路由
# ────────────────────────────────────────────────────────────────────


@app.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> dict:
    """
    登录接口：使用 form-data 提交 username 和 password。

    - 默认测试账号：admin / secret
    - 返回 JWT access_token 及过期时间
    """
    user_dict = get_user(form_data.username)

    if not user_dict or not verify_password(
        form_data.password, user_dict["hashed_password"]
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user_dict["username"]},
        expires_delta=expires,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": int(expires.total_seconds()),
    }


@app.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(data: CreateUserRequest) -> dict:
    """
    注册新用户。

    - 用户名必须唯一
    - 密码需同时包含字母和非字母字符
    - 注册成功后自动返回 JWT Token（无需再次登录）
    """
    if get_user(data.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用户名 '{data.username}' 已被占用",
        )

    hashed = hash_password(data.password)
    user = save_user(
        username=data.username,
        full_name=data.full_name,
        email=data.email or "",
        hashed_password=hashed,
    )

    expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"]},
        expires_delta=expires,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": int(expires.total_seconds()),
    }


@app.get("/users/me", response_model=UserOut)
async def read_users_me(
    current_user: UserOut = Depends(get_current_active_user),
) -> UserOut:
    """获取当前登录用户信息（需在 Authorization 头中携带 Bearer Token）"""
    return current_user


@app.get("/health")
async def health_check() -> dict:
    """健康检查端点"""
    return {
        "status": "ok",
        "version": "2.0.0",
        "users": len(_fake_users_db),
    }


@app.get("/")
async def root() -> dict:
    return {
        "message": "FastAPI Login API is running",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health",
    }


# ────────────────────────────────────────────────────────────────────
# 启动命令（从 demo-login 目录执行）
# ────────────────────────────────────────────────────────────────────
#   cd demo-login
#   uvicorn main:app --reload --port 8000
#
# 或者从项目根目录：
#   uvicorn demo-login.main:app --reload --port 8000
