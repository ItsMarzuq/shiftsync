from __future__ import annotations

import io
import os
import re
import json
from datetime import date, datetime, time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber
import pytesseract
from dateutil import parser as dtparser
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from PIL import Image, ImageOps
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, User, init_db
from .security import hash_password, normalize_email, verify_password

load_dotenv()
from starlette.middleware.sessions import SessionMiddleware

APP_URL = os.getenv("APP_URL", "http://localhost:3000")
API_URL = os.getenv("API_URL", "http://localhost:8000")
GOOGLE_CLIENT_SECRETS_FILE = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Dublin")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

app = FastAPI(title="ShiftSync API")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=COOKIE_SECURE,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[APP_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


class Shift(BaseModel):
    date: str
    start_time: str
    end_time: str
    title: str = "Work Shift"
    location: str | None = None


class CreateEventsPayload(BaseModel):
    shifts: list[Shift]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, int(user_id))


def require_user(user: User | None = Depends(current_user)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user


def user_payload(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "employee_name": user.employee_name,
        "default_title": user.default_title,
        "default_location": user.default_location,
        "timezone": user.timezone,
        "google_connected": bool(user.google_credentials),
    }


class RegisterPayload(BaseModel):
    email: EmailStr
    password: str
    display_name: str = ""


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class ProfilePayload(BaseModel):
    display_name: str | None = None
    employee_name: str | None = None
    default_title: str | None = None
    default_location: str | None = None
    timezone: str | None = None


def parse_date(value: Any) -> str:
    if pd.isna(value):
        raise ValueError("Missing date")
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    parsed = dtparser.parse(str(value), dayfirst=True, fuzzy=True)
    return parsed.date().isoformat()


def parse_roster_header_date(value: Any) -> str:
    """Parse roster header dates such as 10/19/2026.

    The sample roster uses month/day/year. For dates where one side is > 12,
    the format is unambiguous. Ambiguous numeric dates default to month-first
    because that matches this roster export.
    """
    if pd.isna(value):
        raise ValueError("Missing date")
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if not match:
        raise ValueError(f"Not a roster date: {text}")

    a, b, y = (int(part) for part in match.groups())
    if y < 100:
        y += 2000

    if a > 12:
        day, month = a, b
    else:
        month, day = a, b

    return date(y, month, day).isoformat()


def normalize_time(value: Any) -> str:
    """Normalize both normal clock times and decimal roster times.

    The roster uses quarter-hour decimals:
      8.25 -> 08:15
      8.50 -> 08:30
      8.75 -> 08:45
      18.25 -> 18:15
    """
    if pd.isna(value):
        raise ValueError("Missing time")

    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, datetime):
        return value.strftime("%H:%M")

    # Excel can store a time as a fraction of a day.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if 0 <= number < 1:
            total_minutes = round(number * 24 * 60)
            return f"{(total_minutes // 60) % 24:02d}:{total_minutes % 60:02d}"
        if 0 <= number < 24:
            hours = int(number)
            minutes = round((number - hours) * 60)
            if minutes == 60:
                hours += 1
                minutes = 0
            return f"{hours % 24:02d}:{minutes:02d}"

    text = str(value).strip().replace(",", ".")

    # Numeric-looking text from OCR / spreadsheet: 18.25 means 18:15,
    # not 18:25.
    if re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", text):
        number = float(text)
        if 0 <= number < 24:
            hours = int(number)
            minutes = round((number - hours) * 60)
            if minutes == 60:
                hours += 1
                minutes = 0
            return f"{hours % 24:02d}:{minutes:02d}"

    parsed = dtparser.parse(text, fuzzy=True)
    return parsed.strftime("%H:%M")


def infer_columns(df: pd.DataFrame) -> tuple[str, str, str]:
    lowered = {str(c).strip().lower(): str(c) for c in df.columns}

    def find(candidates: list[str]) -> str | None:
        for candidate in candidates:
            for low, original in lowered.items():
                if candidate == low or candidate in low:
                    return original
        return None

    date_col = find(["date", "day"])
    start_col = find(["start", "start time", "from"])
    end_col = find(["end", "finish", "end time", "to"])

    if not all([date_col, start_col, end_col]):
        raise ValueError("Could not find date/start/end columns")
    return date_col, start_col, end_col


def parse_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    date_col, start_col, end_col = infer_columns(df)
    title_col = next((c for c in df.columns if str(c).strip().lower() in {"title", "role", "shift"}), None)
    location_col = next(
        (c for c in df.columns if "location" in str(c).strip().lower() or "store" in str(c).strip().lower()),
        None,
    )

    shifts: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            shift = {
                "date": parse_date(row[date_col]),
                "start_time": normalize_time(row[start_col]),
                "end_time": normalize_time(row[end_col]),
                "title": str(row[title_col]).strip() if title_col and not pd.isna(row[title_col]) else "Work Shift",
                "location": str(row[location_col]).strip() if location_col and not pd.isna(row[location_col]) else None,
            }
            shifts.append(shift)
        except Exception:
            continue

    if not shifts:
        raise ValueError("No valid shifts found")
    return shifts


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold().strip(), b.casefold().strip()).ratio()


def find_employee_row(df: pd.DataFrame, employee_name: str) -> int:
    best_row = -1
    best_score = 0.0

    # Names are normally in the first few columns; scanning the whole sheet
    # also handles a leading blank column.
    for row_idx in range(len(df)):
        for value in df.iloc[row_idx].tolist():
            if pd.isna(value):
                continue
            text = str(value).strip()
            if not text or len(text) > 80:
                continue
            score = similarity(employee_name, text)
            if score > best_score:
                best_score = score
                best_row = row_idx

    if best_row < 0 or best_score < 0.72:
        raise ValueError(f'Could not find employee "{employee_name}" in the roster')
    return best_row


def find_date_header(df: pd.DataFrame, employee_row: int) -> tuple[int, list[tuple[int, str]]]:
    best_header_row = -1
    best_dates: list[tuple[int, str]] = []

    for row_idx in range(min(employee_row, 10)):
        dates: list[tuple[int, str]] = []
        for col_idx, value in enumerate(df.iloc[row_idx].tolist()):
            try:
                parsed = parse_roster_header_date(value)
                dates.append((col_idx, parsed))
            except Exception:
                continue
        if len(dates) > len(best_dates):
            best_header_row = row_idx
            best_dates = dates

    if best_header_row < 0 or len(best_dates) < 2:
        raise ValueError("Could not find the roster's date header row")
    return best_header_row, best_dates


def find_start_end_columns(
    df: pd.DataFrame,
    header_row: int,
    employee_row: int,
    start_col: int,
    end_col_exclusive: int,
) -> tuple[int, int]:
    start_candidate: int | None = None
    end_candidate: int | None = None

    for row_idx in range(header_row + 1, min(employee_row, header_row + 5)):
        for col_idx in range(start_col, end_col_exclusive):
            value = df.iat[row_idx, col_idx]
            if pd.isna(value):
                continue
            label = str(value).strip().casefold()
            if label == "start" and start_candidate is None:
                start_candidate = col_idx
            elif label in {"end", "finish"} and end_candidate is None:
                end_candidate = col_idx

    if start_candidate is not None and end_candidate is not None:
        return start_candidate, end_candidate

    # Fallback for the sample format: date spans start/end/break columns.
    if end_col_exclusive - start_col >= 2:
        return start_col, start_col + 1
    raise ValueError("Could not identify start/end columns")


def parse_matrix_excel(content: bytes, employee_name: str) -> list[dict[str, Any]]:
    df = pd.read_excel(io.BytesIO(content), header=None)
    employee_row = find_employee_row(df, employee_name)
    header_row, date_anchors = find_date_header(df, employee_row)

    shifts: list[dict[str, Any]] = []
    for index, (group_start, iso_date) in enumerate(date_anchors):
        group_end = date_anchors[index + 1][0] if index + 1 < len(date_anchors) else df.shape[1]
        try:
            start_col, end_col = find_start_end_columns(
                df, header_row, employee_row, group_start, group_end
            )
            start_value = df.iat[employee_row, start_col]
            end_value = df.iat[employee_row, end_col]
            if pd.isna(start_value) or pd.isna(end_value):
                continue
            shifts.append(
                {
                    "date": iso_date,
                    "start_time": normalize_time(start_value),
                    "end_time": normalize_time(end_value),
                    "title": "Work Shift",
                    "location": None,
                }
            )
        except Exception:
            continue

    if not shifts:
        raise ValueError(f'Found "{employee_name}", but no shifts were detected on that row')
    return shifts


STRICT_DATE_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
OCR_TIME_RE = re.compile(r"^\d{1,2}(?:[.,]\d{1,2})$")


def parse_image_roster(content: bytes, employee_name: str) -> list[dict[str, Any]]:
    """Parse the table layout used by the supplied roster screenshot.

    Tesseract gives us text plus x/y coordinates. We find the employee's OCR
    line, then map the numeric values on that line to the date columns above.
    """
    image = Image.open(io.BytesIO(content)).convert("L")
    image = ImageOps.autocontrast(image)

    # Upscaling makes the small spreadsheet font much easier for Tesseract.
    scale = 2
    image = image.resize((image.width * scale, image.height * scale))

    data = pytesseract.image_to_data(
        image,
        config="--psm 3",
        output_type=pytesseract.Output.DATAFRAME,
    )
    data = data.dropna(subset=["text"])
    data["text"] = data["text"].astype(str).str.strip()
    data = data[(data["text"] != "") & (data["conf"] > 0)].copy()
    data["center_x"] = data["left"] + data["width"] / 2
    data["center_y"] = data["top"] + data["height"] / 2

    # Find the employee name, allowing OCR mistakes such as Marzuq -> Marzug.
    name_candidates = data[data["left"] < image.width * 0.18].copy()
    if name_candidates.empty:
        raise ValueError("Could not find the staff-name column in the image")
    name_candidates["name_score"] = name_candidates["text"].map(
        lambda text: similarity(employee_name, text)
    )
    name_token = name_candidates.sort_values("name_score", ascending=False).iloc[0]
    if float(name_token["name_score"]) < 0.72:
        raise ValueError(f'Could not find employee "{employee_name}" in the image')

    row_mask = (
        (data["block_num"] == name_token["block_num"])
        & (data["par_num"] == name_token["par_num"])
        & (data["line_num"] == name_token["line_num"])
    )
    row_tokens = data[row_mask].copy()
    row_times = row_tokens[row_tokens["text"].str.match(OCR_TIME_RE)].copy()

    if row_times.empty:
        raise ValueError(f'Found "{employee_name}", but OCR could not read any times on that row')

    date_tokens = data[data["text"].str.match(STRICT_DATE_RE)].copy()
    # Use only dates above the employee row to avoid accidental date-like text elsewhere.
    date_tokens = date_tokens[date_tokens["center_y"] < float(name_token["center_y"])]

    parsed_dates: list[dict[str, Any]] = []
    for _, token in date_tokens.iterrows():
        try:
            parsed_dates.append(
                {
                    "date": parse_roster_header_date(token["text"]),
                    "center_x": float(token["center_x"]),
                }
            )
        except Exception:
            continue

    # Remove duplicate OCR hits and order columns left -> right.
    unique_dates: dict[str, dict[str, Any]] = {}
    for item in parsed_dates:
        unique_dates.setdefault(item["date"], item)
    dates = sorted(unique_dates.values(), key=lambda item: item["center_x"])

    if len(dates) < 2:
        raise ValueError("Could not detect the date columns in the roster image")

    centers = [item["center_x"] for item in dates]
    left_spacing = centers[1] - centers[0]
    right_spacing = centers[-1] - centers[-2]
    boundaries = [centers[0] - left_spacing / 2]
    boundaries.extend((centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1))
    boundaries.append(centers[-1] + right_spacing / 2)

    shifts: list[dict[str, Any]] = []
    for idx, date_item in enumerate(dates):
        left_bound = boundaries[idx]
        right_bound = boundaries[idx + 1]
        tokens = row_times[
            (row_times["center_x"] >= left_bound)
            & (row_times["center_x"] < right_bound)
        ].sort_values("center_x")

        # In each day group the first two numeric values are start and end.
        # A third value, when present, is the break and is intentionally ignored.
        if len(tokens) < 2:
            continue

        start_text = str(tokens.iloc[0]["text"])
        end_text = str(tokens.iloc[1]["text"])
        try:
            shifts.append(
                {
                    "date": date_item["date"],
                    "start_time": normalize_time(start_text),
                    "end_time": normalize_time(end_text),
                    "title": "Work Shift",
                    "location": None,
                }
            )
        except Exception:
            continue

    if not shifts:
        raise ValueError(f'Found "{employee_name}", but no complete shifts were detected')
    return shifts


DATE_RE = re.compile(r"(?P<date>\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b)")
TIME_RANGE_RE = re.compile(
    r"(?P<start>\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b)\s*(?:-|–|—|to)\s*(?P<end>\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b)",
    re.IGNORECASE,
)


def parse_pdf_text(text: str) -> list[dict[str, Any]]:
    shifts: list[dict[str, Any]] = []
    current_year = datetime.now().year

    for line in text.splitlines():
        dm = DATE_RE.search(line)
        tm = TIME_RANGE_RE.search(line)
        if not (dm and tm):
            continue
        date_text = dm.group("date")
        if len(re.split(r"[/-]", date_text)) == 2:
            date_text = f"{date_text}/{current_year}"
        try:
            shifts.append(
                {
                    "date": parse_date(date_text),
                    "start_time": normalize_time(tm.group("start")),
                    "end_time": normalize_time(tm.group("end")),
                    "title": "Work Shift",
                    "location": None,
                }
            )
        except Exception:
            continue

    if not shifts:
        raise ValueError("No shifts could be detected in PDF text")
    return shifts


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/parse-roster")
async def parse_roster(
    file: UploadFile = File(...),
    employee_name: str | None = Form(None),
    user: User = Depends(require_user),
):
    filename = file.filename or "roster"
    suffix = Path(filename).suffix.lower()
    content = await file.read()
    employee_name = (employee_name or user.employee_name or "").strip()

    try:
        if suffix == ".csv":
            df = pd.read_csv(io.BytesIO(content))
            shifts = parse_dataframe(df)
        elif suffix in {".xlsx", ".xls"}:
            if employee_name:
                shifts = parse_matrix_excel(content, employee_name)
            else:
                df = pd.read_excel(io.BytesIO(content))
                shifts = parse_dataframe(df)
        elif suffix == ".pdf":
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
            shifts = parse_pdf_text(text)
        elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            if not employee_name:
                raise HTTPException(
                    status_code=400,
                    detail="Enter your name before parsing an image roster.",
                )
            shifts = parse_image_roster(content, employee_name)
        else:
            raise HTTPException(
                status_code=400,
                detail="Supported files: CSV, XLSX, XLS, PDF, PNG, JPG, JPEG, WEBP",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse roster: {exc}") from exc

    return {
        "filename": filename,
        "employee_name": employee_name or None,
        "shifts": shifts,
    }


@app.post("/auth/register")
def register(payload: RegisterPayload, request: Request, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = User(
        email=email,
        password_hash=password_hash,
        display_name=payload.display_name.strip(),
        employee_name=payload.display_name.strip(),
        default_title="Work Shift",
        default_location="",
        timezone=TIMEZONE,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    return user_payload(user)


@app.post("/auth/login")
def login(payload: LoginPayload, request: Request, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    request.session.clear()
    request.session["user_id"] = user.id
    return user_payload(user)


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/me")
def me(user: User = Depends(require_user)):
    return user_payload(user)


@app.patch("/me")
def update_me(
    payload: ProfilePayload,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(user, field, value)
    if not user.default_title:
        user.default_title = "Work Shift"
    if not user.timezone:
        user.timezone = TIMEZONE
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_payload(user)


@app.get("/google/status")
def google_status(user: User = Depends(require_user)):
    return {"connected": bool(user.google_credentials)}


@app.get("/google/login")
def google_login(
    request: Request,
    user: User = Depends(require_user),
):
    if not Path(GOOGLE_CLIENT_SECRETS_FILE).exists():
        raise HTTPException(
            status_code=500,
            detail=f"Missing {GOOGLE_CLIENT_SECRETS_FILE}. Download an OAuth Web client JSON from Google Cloud.",
        )

    flow = Flow.from_client_secrets_file(GOOGLE_CLIENT_SECRETS_FILE, scopes=SCOPES)
    flow.redirect_uri = f"{API_URL}/google/callback"
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    request.session["oauth_state"] = state
    request.session["oauth_user_id"] = user.id
    return RedirectResponse(authorization_url)


@app.get("/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)):
    expected_state = request.session.get("oauth_state")
    received_state = request.query_params.get("state")
    oauth_user_id = request.session.get("oauth_user_id")
    if not expected_state or expected_state != received_state or not oauth_user_id:
        raise HTTPException(status_code=400, detail="OAuth state mismatch")

    user = db.get(User, int(oauth_user_id))
    if not user:
        raise HTTPException(status_code=401, detail="Sign in again before connecting Google Calendar")

    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=expected_state,
    )
    flow.redirect_uri = f"{API_URL}/google/callback"
    flow.fetch_token(authorization_response=str(request.url))

    creds = flow.credentials
    new_data = json.loads(creds.to_json())
    if not new_data.get("refresh_token") and user.google_credentials:
        try:
            previous = json.loads(user.google_credentials)
            new_data["refresh_token"] = previous.get("refresh_token")
        except Exception:
            pass

    user.google_credentials = json.dumps(new_data)
    db.add(user)
    db.commit()
    request.session.pop("oauth_state", None)
    request.session.pop("oauth_user_id", None)
    return RedirectResponse(f"{APP_URL}/?google=connected")


def build_calendar_service(user: User):
    if not user.google_credentials:
        raise HTTPException(status_code=401, detail="Google Calendar is not connected")
    try:
        data = json.loads(user.google_credentials)
        creds = Credentials.from_authorized_user_info(data, SCOPES)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Reconnect Google Calendar") from exc
    return build("calendar", "v3", credentials=creds), creds


@app.post("/calendar/events")
def create_events(
    payload: CreateEventsPayload,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    service, creds = build_calendar_service(user)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    timezone_name = user.timezone or TIMEZONE

    for shift in payload.shifts:
        key = f"{shift.date}|{shift.start_time}|{shift.end_time}|{shift.title}|{shift.location or ''}"
        existing = service.events().list(
            calendarId="primary",
            privateExtendedProperty=f"shiftsync_key={key}",
            maxResults=1,
        ).execute().get("items", [])
        if existing:
            skipped.append({"key": key, "reason": "duplicate"})
            continue

        start = f"{shift.date}T{shift.start_time}:00"
        end = f"{shift.date}T{shift.end_time}:00"
        event = {
            "summary": shift.title,
            "location": shift.location,
            "description": "Imported by ShiftSync",
            "start": {"dateTime": start, "timeZone": timezone_name},
            "end": {"dateTime": end, "timeZone": timezone_name},
            "extendedProperties": {"private": {"shiftsync_key": key}},
        }
        result = service.events().insert(calendarId="primary", body=event).execute()
        created.append(
            {
                "id": result.get("id"),
                "htmlLink": result.get("htmlLink"),
                "summary": result.get("summary"),
            }
        )

    # Save a refreshed access token after Calendar calls when the library updated it.
    try:
        user.google_credentials = creds.to_json()
        db.add(user)
        db.commit()
    except Exception:
        pass

    return {"created": created, "skipped": skipped}

