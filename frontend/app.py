"""Streamlit frontend for the room booking service.

The interface talks to the project's own FastAPI endpoints only - it never
imports SQLAlchemy, never opens a database connection and never reads the
database file.  Every failure path of the API is surfaced to the user.

Run with::

    streamlit run frontend/app.py
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import httpx
import pandas as pd
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "10"))

st.set_page_config(page_title="Room Booking Service", page_icon="🗓", layout="wide")


# --------------------------------------------------------------------------- api
def api_request(method: str, path: str, **kwargs) -> tuple[dict | list | None, dict | None]:
    """Call the API and return ``(payload, error)`` - exactly one of them is set."""
    try:
        response = httpx.request(
            method, f"{API_BASE_URL}{path}", timeout=REQUEST_TIMEOUT, **kwargs
        )
    except httpx.TimeoutException:
        return None, {
            "status": None,
            "message": (
                f"The request timed out after {REQUEST_TIMEOUT:.0f}s. The backend may be "
                "restarting, please try again shortly."
            ),
        }
    except httpx.HTTPError as exc:
        return None, {
            "status": None,
            "message": f"Cannot reach the backend at {API_BASE_URL} ({exc.__class__.__name__}).",
        }

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        if isinstance(detail, dict):
            return None, {
                "status": response.status_code,
                "code": detail.get("code"),
                "message": detail.get("message") or str(detail),
                "extra": detail,
            }
        return None, {"status": response.status_code, "message": str(detail)}
    try:
        return response.json(), None
    except ValueError:
        return None, {"status": response.status_code, "message": "The backend returned a non-JSON response."}


def show_error(error: dict) -> None:
    """Render an API error, including the documented conflict details."""
    message = error.get("message", "Unknown error")
    code = error.get("code")
    status_code = error.get("status")
    if status_code == 409:
        st.warning(f"The slot conflicts with an existing booking: {message}")
        extra = error.get("extra") or {}
        conflicting = extra.get("conflicting_booking_ids") or []
        if conflicting:
            st.caption(
                "Conflicting booking ids: " + ", ".join(f"#{item}" for item in conflicting)
            )
        with st.expander("Technical details"):
            st.json(error)
        return
    if status_code == 503:
        st.info(f"The service is in maintenance mode and is read-only right now: {message}")
        return
    st.error(f"The request failed ({code}): {message}" if code else f"The request failed: {message}")
    with st.expander("Technical details"):
        st.json(error)


# ---------------------------------------------------------------------- sidebar
st.sidebar.title("Room Booking Service")
st.sidebar.caption(f"API: {API_BASE_URL}")
health, health_error = api_request("GET", "/health")
if health_error:
    st.sidebar.error("Backend unavailable")
    st.sidebar.caption(health_error["message"])
elif health:
    maintenance = health.get("maintenance_mode", False)
    if maintenance:
        st.sidebar.warning("Maintenance mode: queries only")
    else:
        st.sidebar.success(f"Backend healthy · v{health.get('version', '?')}")
st.sidebar.divider()
st.sidebar.markdown(
    "**How to use**\n"
    "1. New booking: overlapping bookings in the same room are rejected "
    "(back-to-back slots are allowed)\n"
    "2. Bookings: filter by room and by date range\n"
    "3. Utilization: computed over the opening hours (08:00-20:00)"
)

create_tab, list_tab, stats_tab = st.tabs(["New booking", "Bookings", "Utilization"])


# ------------------------------------------------------------------- create tab
with create_tab:
    st.subheader("New booking")
    rooms_payload, rooms_error = api_request("GET", "/rooms")
    existing_rooms = [] if (rooms_error or not rooms_payload) else rooms_payload.get("items", [])

    with st.form("create_booking_form", clear_on_submit=False):
        col_a, col_b = st.columns(2)
        with col_a:
            room = st.text_input(
                "Room *",
                placeholder="e.g. Room A",
                help="Existing rooms: " + (", ".join(existing_rooms) if existing_rooms else "none yet"),
            )
            booker = st.text_input("Booker *", placeholder="e.g. Tao Yuhao")
        with col_b:
            purpose = st.text_input("Purpose", placeholder="Optional, e.g. weekly project meeting")
            st.caption("Times use `HH:MM`; the end must be later than the start.")
        col_c, col_d, col_e = st.columns(3)
        day = col_c.date_input("Date *", value=date.today(), min_value=date(2000, 1, 1))
        start_time = col_d.time_input("Start time *", value=time(9, 0), step=timedelta(minutes=15))
        end_time = col_e.time_input("End time *", value=time(10, 0), step=timedelta(minutes=15))
        submitted = st.form_submit_button("Book the room", type="primary")

    if submitted:
        if not room.strip() or not booker.strip():
            st.error("Room and booker are both required.")
        elif end_time <= start_time:
            st.error("The end time must be later than the start time.")
        else:
            start_at = datetime.combine(day, start_time)
            end_at = datetime.combine(day, end_time)
            payload, error = api_request(
                "POST",
                "/bookings",
                json={
                    "room": room.strip(),
                    "booker": booker.strip(),
                    "purpose": purpose.strip() or None,
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                },
            )
            if error:
                show_error(error)
            elif payload:
                st.success(
                    f"Booking #{payload['id']} created: {payload['room']} "
                    f"{payload['start_at'][11:16]}-{payload['end_at'][11:16]} ({payload['booker']})"
                )


# --------------------------------------------------------------------- list tab
with list_tab:
    st.subheader("Bookings")
    rooms_payload, _ = api_request("GET", "/rooms")
    filter_rooms = [] if not rooms_payload else rooms_payload.get("items", [])
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    room_choice = col_f1.selectbox("Room", ["All rooms"] + filter_rooms)
    date_from = col_f2.date_input("From", value=date.today())
    date_to = col_f3.date_input("To", value=date.today() + timedelta(days=7))

    if date_to < date_from:
        st.error("The end date cannot be earlier than the start date.")
    else:
        params: dict[str, str] = {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "limit": "200",
        }
        if room_choice != "All rooms":
            params["room"] = room_choice
        payload, error = api_request("GET", "/bookings", params=params)
        if error:
            show_error(error)
        elif not payload or payload.get("total", 0) == 0:
            st.info("There are no bookings in this date range.")
        else:
            st.caption(f"{payload['total']} booking(s) in this range (at most 200 are shown).")
            frame = pd.DataFrame(payload["items"])
            frame["Slot"] = frame.apply(
                lambda row: f"{row['start_at'][5:10]} {row['start_at'][11:16]}-{row['end_at'][11:16]}",
                axis=1,
            )
            st.dataframe(
                frame[["id", "Slot", "room", "booker", "purpose"]].rename(
                    columns={"id": "ID", "room": "Room", "booker": "Booker", "purpose": "Purpose"}
                ),
                hide_index=True,
            )


# -------------------------------------------------------------------- stats tab
with stats_tab:
    st.subheader("Utilization")
    col_s1, col_s2 = st.columns(2)
    stats_from = col_s1.date_input("From", value=date.today())
    stats_to = col_s2.date_input("To", value=date.today() + timedelta(days=6))

    if stats_to < stats_from:
        st.error("The end date cannot be earlier than the start date.")
    else:
        payload, error = api_request(
            "GET",
            "/stats/utilization",
            params={"date_from": stats_from.isoformat(), "date_to": stats_to.isoformat()},
        )
        if error:
            show_error(error)
        elif not payload or not payload.get("rooms"):
            st.info("There are no bookings to analyse in this range.")
        else:
            window = payload["window"]
            st.caption(
                f"{window['date_from']} to {window['date_to']}, opening hours "
                f"{window['open_hour']:02d}:00-{window['close_hour']:02d}:00, "
                f"{window['open_minutes'] / 60:.0f} available hours in total. "
                "Utilization = booked minutes / available minutes."
            )
            frame = pd.DataFrame(payload["rooms"]).sort_values("utilization", ascending=False)
            frame["Utilization (%)"] = (frame["utilization"] * 100).round(1)
            frame["Booked (h)"] = (frame["booked_minutes"] / 60).round(2)
            st.bar_chart(frame, x="room", y="Utilization (%)")
            st.dataframe(
                frame[["room", "booking_count", "Booked (h)", "Utilization (%)"]].rename(
                    columns={"room": "Room", "booking_count": "Bookings"}
                ),
                hide_index=True,
            )
