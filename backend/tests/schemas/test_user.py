import pytest
from pydantic import ValidationError

from api.schemas.user import UserCreate, UserLogin, UserResponse
from tests.schemas.helpers import make_user_obj

# ---------------------------------------------------------------------------
# UserCreate
# ---------------------------------------------------------------------------


def test_create_valid():
    user = UserCreate(email="test@example.com", password="SecurePass1")
    assert user.email == "test@example.com"


def test_create_with_display_name():
    user = UserCreate(
        email="test@example.com",
        password="SecurePass1",
        display_name="Test User",
    )
    assert user.display_name == "Test User"


def test_create_email_required():
    with pytest.raises(ValidationError):
        UserCreate(password="SecurePass1")  # type: ignore[call-arg]


def test_create_invalid_email():
    with pytest.raises(ValidationError):
        UserCreate(email="not-an-email", password="SecurePass1")


def test_create_password_min_length():
    with pytest.raises(ValidationError):
        UserCreate(email="test@example.com", password="Short1")


def test_create_display_name_max_length():
    with pytest.raises(ValidationError):
        UserCreate(
            email="test@example.com",
            password="SecurePass1",
            display_name="x" * 101,
        )


def test_create_password_no_uppercase():
    with pytest.raises(ValidationError):
        UserCreate(email="test@example.com", password="securepass1")


def test_create_password_no_digit():
    with pytest.raises(ValidationError):
        UserCreate(email="test@example.com", password="Securepass")


# ---------------------------------------------------------------------------
# UserLogin
# ---------------------------------------------------------------------------


def test_login_valid():
    login = UserLogin(email="test@example.com", password="pass")
    assert login.email == "test@example.com"


def test_login_invalid_email():
    with pytest.raises(ValidationError):
        UserLogin(email="bad", password="pass")


# ---------------------------------------------------------------------------
# UserResponse
# ---------------------------------------------------------------------------


def test_response_from_orm():
    obj = make_user_obj()
    resp = UserResponse.model_validate(obj, from_attributes=True)
    assert resp.id == 1
    assert resp.is_admin is False


def test_response_no_password_hash_exposed():
    assert "password_hash" not in UserResponse.model_fields
