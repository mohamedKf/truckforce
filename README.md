# 🚛 TruckForce – Backend Setup Guide

## Project Structure
```
truckforce/
├── core/
│   ├── models.py          ← All DB models
│   ├── serializers.py     ← DRF serializers
│   ├── views.py           ← All API views
│   ├── urls.py            ← API routes
│   ├── permissions.py     ← IsManager / IsDriver
│   ├── auth_utils.py      ← Token store/retrieve
│   ├── firebase.py        ← FCM push notifications
│   ├── payroll_engine.py  ← Israeli payroll calculator
│   └── admin.py
├── truckforce/
│   ├── settings.py
│   └── urls.py
└── requirements.txt
```

---

## Step 1 – Install dependencies
```bash
pip install -r requirements.txt
```

## Step 2 – Create MySQL database
```sql
CREATE DATABASE truckforce_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

## Step 3 – Update settings.py
Edit `truckforce/settings.py`:
- Set your MySQL password
- Set `SECRET_KEY` to a long random string
- Set `ALLOWED_HOSTS` to your server IP

## Step 4 – Run migrations
```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createcachetable
```

## Step 5 – Create first admin manager
```bash
python manage.py shell
```
```python
from core.models import Manager
m = Manager(username='admin', full_name='Admin', role='admin', email='admin@company.com')
m.set_password('your_password')
m.save()
```

## Step 6 – Create CompanySettings
```python
from core.models import CompanySettings
CompanySettings.objects.create(
    company_name='Your Company',
    crane_rounding_rule='half',
    crane_price_per_hour=150,
    overtime_threshold=8.0,
)
```

## Step 7 – Run the server
```bash
python manage.py runserver 0.0.0.0:8000
```

---

## API Quick Reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/auth/manager/login/` | None | Manager login |
| POST | `/api/auth/driver/login/` | None | Driver login |
| GET | `/api/dashboard/` | Manager | Stats overview |
| GET/POST | `/api/drivers/` | Manager | List / add drivers |
| GET/POST | `/api/trucks/` | Manager | List / add trucks |
| GET/POST | `/api/schedules/` | Manager | List / create schedules |
| GET | `/api/schedules/today/` | Driver | Today's route |
| PATCH | `/api/stops/<id>/update/` | Driver | Mark stop done/skipped |
| POST | `/api/attendance/clock-in/` | Driver | Clock in |
| POST | `/api/attendance/clock-out/` | Driver | Clock out |
| POST | `/api/crane/start/` | Driver | Start crane timer |
| POST | `/api/crane/<id>/end/` | Driver | Stop crane timer |
| POST | `/api/payroll/generate/` | Manager | Generate payslip |

### Auth header (all protected endpoints):
```
Authorization: Token <your_token_here>
```

---

## Firebase Setup
1. Create a Firebase project at https://console.firebase.google.com
2. Add Android app → download `google-services.json` → put in Flutter `/android/app/`
3. Go to Project Settings → Cloud Messaging → copy **Server Key**
4. In the app: Settings → paste the Server Key

---

## Crane Rounding Rules
- `full`    → 1h 10min = **2 hours**
- `half`    → 1h 10min = **1.5 hours**, 1h 40min = **2 hours**
- `quarter` → 1h 10min = **1.25 hours**
- `exact`   → 1h 10min = **1.1667 hours**

Set in CompanySettings via `/api/settings/`
