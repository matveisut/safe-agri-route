"""
FastAPI dependency functions for authentication and authorisation.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.database import get_db
from app.models.user import User
from app.repositories.user import user_repo

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Validate Bearer token and return the corresponding User row.
    Raises HTTP 401 if the token is missing, invalid, or the user is inactive.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        email: str = payload.get("sub")
        if not email:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = await user_repo.get_by_email(db, email)
    if user is None or not user.is_active:
        raise credentials_exc
    return user


async def require_operator(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Allow only users with role='operator'.
    Raises HTTP 403 for any other role.
    """
    if current_user.role != "operator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator role required",
        )
    return current_user


# Alias: any authenticated user is a valid viewer
require_viewer = get_current_user
