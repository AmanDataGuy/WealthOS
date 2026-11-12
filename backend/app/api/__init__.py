"""
# api - the web API's routes, one router per area of the app

- `account`, `sources`, `assistant`, `dashboards`, `strategy`, `company`; `main.py` includes them all.
- Each imports the sign-in checks (`USER`, `USER_WS`, `verify_user_token` ...) from `app.main`, so `main.py`
  defines those first and imports these modules at its end. Loading `app.main` here first keeps that order even
  when a router module is imported on its own.
"""
import app.main  # noqa: F401
