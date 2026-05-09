import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from db import get_conn
from auth import hash_password, verify_password, create_token

router = APIRouter()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest) -> TokenResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (req.email,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Email already registered.")
            user_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO users (user_id, email, hashed_password) VALUES (%s, %s, %s)",
                (user_id, req.email, hash_password(req.password)),
            )
        conn.commit()
    return TokenResponse(access_token=create_token(user_id, req.email))


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest) -> TokenResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, hashed_password FROM users WHERE email = %s AND is_active = TRUE",
                (req.email,),
            )
            row = cur.fetchone()
    if not row or not verify_password(req.password, row["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return TokenResponse(access_token=create_token(row["user_id"], req.email))
