"""Authentication endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserOut, Token, LoginRequest
from app.core.security import hash_password, verify_password, create_access_token, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """
    Public registration endpoint.
    Restricted to standard loan officer accounts. Clients cannot self-register as admin.
    Any attempt to request admin or other privileged roles is rejected with HTTP 400.
    """
    requested_role = (payload.role or "loan_officer").strip().lower()
    if requested_role in ("admin", "administrator", "superuser"):
        raise HTTPException(
            status_code=400,
            detail="Public registration cannot assign administrator privileges. Contact an existing administrator.",
        )
    if requested_role != "loan_officer":
        raise HTTPException(
            status_code=400,
            detail="Unsupported role. Public registration only allows 'loan_officer'.",
        )

    clean_email = payload.email.strip().lower()
    if db.query(User).filter(func.lower(User.email) == clean_email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        name=payload.name.strip(),
        email=clean_email,
        password=hash_password(payload.password),
        role=UserRole.loan_officer,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/admin/create-user", response_model=UserOut, status_code=201)
def create_user_by_admin(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Authorized administrator user management.
    Only authenticated administrators can create new accounts with arbitrary roles (including admin).
    """
    role_val = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_val != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator privileges required to create accounts via this workflow.",
        )

    clean_email = payload.email.strip().lower()
    if db.query(User).filter(func.lower(User.email) == clean_email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    raw_role = (payload.role or "loan_officer").strip().lower()
    target_role = UserRole.admin if raw_role in ("admin", "administrator") else UserRole.loan_officer

    user = User(
        name=payload.name.strip(),
        email=clean_email,
        password=hash_password(payload.password),
        role=target_role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Admin %s created user %s with role %s", current_user.email, clean_email, target_role.value)
    return user


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate and return JWT token."""
    clean_email = payload.email.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == clean_email).first()
    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    token = create_access_token({"sub": str(user.id), "role": role_val})
    return Token(access_token=token, user=user)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user
