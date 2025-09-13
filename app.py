from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
import os, random
from datetime import datetime
from functools import wraps
import re
from flask import send_from_directory, jsonify
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import string, secrets
import logging
import json, uuid, shutil
import subprocess

app = Flask(__name__)

# === Config ===
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# === File storage ===
BASE_DIR = os.path.dirname(__file__)
UPLOAD_RESUMES = os.path.join(BASE_DIR, "uploads", "resumes")
UPLOAD_OFFERS  = os.path.join(BASE_DIR, "uploads", "offers")
os.makedirs(UPLOAD_RESUMES, exist_ok=True)
os.makedirs(UPLOAD_OFFERS, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_RESUMES  # existing resume usage

# --- MAIL (inline config; edit with your real creds) ---
app.config.update({
    "MAIL_SERVER": "smtp.gmail.com",
    "MAIL_PORT": 587,
    "MAIL_USE_TLS": True,
    "MAIL_USE_SSL": False,
    "MAIL_USERNAME": "saich5252@gmail.com",       # <-- set
    "MAIL_PASSWORD": "uxes gofe hanv euca",      # <-- set (Gmail App Password)
    "MAIL_DEFAULT_SENDER": ("PeopleOps HR", "saich5252@gmail.com"),
    "MAIL_SUPPRESS_SEND": False,
    "MAIL_DEBUG": True
})
mail = Mail(app)

# Optional logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("peopleops")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads', 'resumes')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_RESUME_EXTS = {'pdf', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Self-onboarding storage (JSON + docs) ---
ONBOARDING_DIR = os.path.join(BASE_DIR, "uploads", "onboarding")
ONBOARDING_DOCS_DIR = os.path.join(ONBOARDING_DIR, "docs")
os.makedirs(ONBOARDING_DIR, exist_ok=True)
os.makedirs(ONBOARDING_DOCS_DIR, exist_ok=True)

ALLOWED_ONBOARDING_DOCS = {'pdf', 'jpg', 'jpeg', 'png'}

def allowed_onboarding_doc(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_ONBOARDING_DOCS

def onboarding_json_path(username: str) -> str:
    safe = secure_filename(username or "user")
    return os.path.join(ONBOARDING_DIR, f"{safe}.json")

def load_onboarding(username: str) -> dict:
    p = onboarding_json_path(username)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    # default structure
    return {
        "personal": {
            "full_name": "",
            "dob": "",
            "address": "",
            "phone": "",
            "emergency_contact": ""
        },
        "bank": {
            "account_name": "",
            "account_number": "",
            "ifsc": "",
            "bank_name": ""
        },
        "documents": {
            "pan": None,
            "aadhaar": None,
            "photo": None,
            "cancelled_cheque": None
        },
        "tasks": {
            "policy_ack": False,
            "code_of_conduct": False,
            "it_form": False,
            "pf_form": False
        },
        "completed": False
    }

# --- Employee Handbooks storage ---
HANDBOOKS_DIR = os.path.join(BASE_DIR, "uploads", "handbooks")
os.makedirs(HANDBOOKS_DIR, exist_ok=True)
ALLOWED_HANDBOOKS = {"pdf"}

def save_onboarding(username: str, data: dict):
    p = onboarding_json_path(username)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Unified Document Management storage ---
DOCS_DIR = os.path.join(BASE_DIR, "uploads", "documents")
DOCS_FILES_DIR = os.path.join(DOCS_DIR, "files")
DOCS_INDEX = os.path.join(DOCS_DIR, "index.json")
os.makedirs(DOCS_DIR, exist_ok=True)
os.makedirs(DOCS_FILES_DIR, exist_ok=True)

def load_docs_index() -> list:
    if os.path.exists(DOCS_INDEX):
        try:
            with open(DOCS_INDEX, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except:
            return []
    return []

def save_docs_index(rows: list):
    with open(DOCS_INDEX, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

# === Models ===
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(6), unique=True, nullable=False)  # 6-digit string
    username = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    employee_type = db.Column(db.String(50), nullable=False)
    subposition = db.Column(db.String(50), nullable=False)
    designation = db.Column(db.String(120), nullable=True)  # NEW: manual designation for Employees
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    location = db.Column(db.String(120), nullable=True)  # e.g., "New York, NY, USA"
    work_type = db.Column(db.String(20), nullable=True, default="office")  # office, remote, hybrid
    timezone = db.Column(db.String(50), nullable=True)  # e.g., "America/New_York"

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
    
# === Recruitment Models ===
from sqlalchemy import Enum as SqlEnum
from enum import Enum

class StageEnum(Enum):
    SCREENING = "Screening"
    SHORTLISTED = "Shortlisted"
    INTERVIEW_1 = "Interview 1"
    INTERVIEW_2 = "Interview 2"
    HR_INTERVIEW = "HR Interview"
    OFFER = "Offer"
    ONBOARDING = "Onboarding"
    REJECTED = "Rejected"

class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    department = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    employment_type = db.Column(db.String(40), nullable=True)  # e.g., Full-time, Contract, Intern
    openings = db.Column(db.Integer, nullable=True, default=1)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Open")  # Open / Closed
    created_by = db.Column(db.String(50), nullable=False)  # username
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Candidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    role_of_employment = db.Column(db.String(160), nullable=False)
    qualification = db.Column(db.String(160), nullable=True)

    # NEW fields
    email = db.Column(db.String(160), nullable=True)
    phone = db.Column(db.String(40), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    years_experience = db.Column(db.Integer, nullable=True)
    current_ctc = db.Column(db.Integer, nullable=True)
    expected_ctc = db.Column(db.Integer, nullable=True)
    notice_period_days = db.Column(db.Integer, nullable=True)
    source = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    resume_path = db.Column(db.String(255), nullable=True)
    stage = db.Column(db.String(50), nullable=False, default=StageEnum.SCREENING.value)
    job_id = db.Column(db.Integer, db.ForeignKey('job.id'), nullable=False)
    job = db.relationship('Job', backref=db.backref('candidates', lazy=True))

    # ✅ existed: textual tracker (keep it for backward compatibility / quick filters)
    created_by = db.Column(db.String(50), nullable=False)

    # ✅ NEW: normalized foreign key to User
    recruiter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    recruiter = db.relationship('User', foreign_keys=[recruiter_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    offer_letter_path = db.Column(db.String(255), nullable=True)
    recruited = db.Column(db.Boolean, default=False)
    onboarded_at = db.Column(db.DateTime, nullable=True)

# === Job Catalog model ===
class JobCatalog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)              # e.g., "Backend Engineer"
    family = db.Column(db.String(120), nullable=True)              # e.g., "Engineering", "HR"
    level = db.Column(db.String(60), nullable=True)                # e.g., "L2", "Senior", "Manager"
    department = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    employment_type = db.Column(db.String(40), nullable=True)      # Full-time / Contract / Intern / …
    min_experience = db.Column(db.Integer, nullable=True)          # years
    max_experience = db.Column(db.Integer, nullable=True)          # years
    salary_min = db.Column(db.Integer, nullable=True)              # numeric band (optional)
    salary_max = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(10), nullable=True, default="INR")

    description = db.Column(db.Text, nullable=True)                # overview / purpose
    responsibilities = db.Column(db.Text, nullable=True)           # bullet-ish text
    requirements = db.Column(db.Text, nullable=True)               # bullet-ish text
    skills = db.Column(db.Text, nullable=True)                     # comma-separated tags (e.g., "Python, SQL, APIs")

    status = db.Column(db.String(20), nullable=False, default="Active")  # Active / Archived
    created_by = db.Column(db.String(50), nullable=False)                # username
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def allowed_resume(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTS


# === Projects ===
class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(20), nullable=False, default="Planned")  # Planned | Active | On Hold | Completed
    progress = db.Column(db.Integer, nullable=False, default=0)           # 0–100

    start_date = db.Column(db.Date, nullable=True)
    end_date   = db.Column(db.Date, nullable=True)

    # Ownership & team
    created_by = db.Column(db.String(50), nullable=False)                 # username of creator (Team Lead)
    # CSV list of usernames (simple, SQLite-friendly)
    team_csv   = db.Column(db.Text, nullable=True)                        # e.g. "alex,neha,amit"

    # Optional: simple JSON (as text) of {username: hours}
    allocations_json = db.Column(db.Text, nullable=True)                  # e.g. '{"alex": 20, "neha": 15}'

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# === Leave / Time Off ===
class LeaveBalance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, index=True)
    leave_type = db.Column(db.String(20), nullable=False)  # in LEAVE_TYPES
    balance_days = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint('username', 'leave_type', name='uq_leave_balance_user_type'),
    )

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, index=True)   # requester
    leave_type = db.Column(db.String(20), nullable=False)             # in LEAVE_TYPES
    start_date = db.Column(db.Date, nullable=False)
    end_date   = db.Column(db.Date, nullable=False)
    days       = db.Column(db.Integer, nullable=False, default=1)     # computed (business days)
    reason     = db.Column(db.Text, nullable=True)
    status     = db.Column(db.String(20), nullable=False, default="Pending")  # Pending|Approved|Rejected|Cancelled
    approver   = db.Column(db.String(50), nullable=True)              # approver username
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# === Performance: cycles, goals, reviews ===
class PerfCycle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)  # e.g., "H1 2025"
    start_date = db.Column(db.Date, nullable=False)
    end_date   = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Active")  # Active|Closed
    created_by = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Goal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(db.Integer, db.ForeignKey('perf_cycle.id'), nullable=False, index=True)
    cycle = db.relationship('PerfCycle', backref=db.backref('goals', lazy=True))
    owner = db.Column(db.String(50), nullable=False, index=True)           # username of employee
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    weight = db.Column(db.Integer, nullable=False, default=20)             # 0..100
    status = db.Column(db.String(20), nullable=False, default="Draft")     # Draft|In Progress|Completed|Archived
    progress = db.Column(db.Integer, nullable=False, default=0)            # 0..100
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class GoalUpdate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey('goal.id'), nullable=False, index=True)
    goal = db.relationship('Goal', backref=db.backref('updates', lazy=True))
    progress = db.Column(db.Integer, nullable=False)  # 0..100
    note = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.String(50), nullable=False)  # username
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(db.Integer, db.ForeignKey('perf_cycle.id'), nullable=False, index=True)
    cycle = db.relationship('PerfCycle', backref=db.backref('reviews', lazy=True))
    reviewee = db.Column(db.String(50), nullable=False, index=True)  # employee username
    reviewer = db.Column(db.String(50), nullable=False)              # manager username
    rating = db.Column(db.Integer, nullable=True)                    # 1..5
    comments = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Open")  # Open|Submitted|Finalized
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



def business_days(d1, d2):
    """Inclusive business-day count (excludes Sat/Sun)."""
    if d2 < d1:
        d1, d2 = d2, d1
    delta = (d2 - d1).days + 1
    count = 0
    for i in range(delta):
        wd = (d1 + timedelta(days=i)).weekday()
        if wd not in (SATURDAY, SUNDAY):
            count += 1
    return count

def is_approver():
    """Allow approvals for HR or Leaders."""
    et = session.get("employee_type", "")
    return et in ("HR", "Leaders")

def get_user_or_abort(username):
    u = User.query.filter_by(username=username).first()
    if not u:
        abort(404)
    return u

def is_manager_or_hr():
    return session.get("employee_type") in ("Leaders", "HR")


# === Role → Subposition → Features (tasks) ===
# Role → Subposition → Features (tasks)
ROLE_SUBPOSITION_FEATURES = {
    "Employees": {
        "New": [
            "fa-solid fa-clipboard-check|Onboarding – Automate new hire setup with seamless onboarding workflows",
            "fa-solid fa-book-open|Employee handbooks – Digitize and distribute employee policies and guidelines",
            "fa-solid fa-folder-tree|Document management – Securely store, share, and manage HR documents"
        ],
        "Existing": [
            "fa-solid fa-users|Team – View and manage employee details, roles, and responsibilities",
            "fa-solid fa-plane-departure|Time off – Centralize leave requests, balances, and approvals",
            "fa-regular fa-calendar-check|Time off requests – Simplify leave application and manager approval process",
            "fa-solid fa-gift|Team's benefits – Manage employee perks, benefits, and eligibility",
            "fa-solid fa-file-invoice-dollar|Expenses – Submit, approve, and reimburse employee expenses easily",
            "fa-solid fa-chart-line|Performance – Drive continuous performance reviews and goal tracking",
            "fa-solid fa-clipboard-list|Manage reviews – Schedule, conduct, and store structured employee reviews",
            "fa-solid fa-comments|Company feedback – Capture employee sentiment with surveys and feedback tools",
            "fa-solid fa-headset|Support monitor – Track employee support tickets and resolve HR queries",
            "fa-solid fa-sack-dollar|Payroll – Automate salary calculations, compliance, and disbursements",
            "fa-solid fa-book-open|Employee handbooks – Digitize and distribute employee policies and guidelines",
            "fa-solid fa-folder-tree|Document management – Securely store, share, and manage HR documents"
        ],
        "Exiting": [
            "fa-solid fa-door-open|Offboarding – Manage exits smoothly while ensuring compliance and knowledge transfer",
            "fa-solid fa-sack-dollar|Payroll – Automate salary calculations, compliance, and disbursements",
            "fa-solid fa-folder-tree|Document management – Securely store, share, and manage HR documents"
        ]
    },

    "HR": {
        "Managers": [
            "fa-solid fa-chart-pie|Talent insights – Gain data-driven visibility into workforce trends and talent performance",
            "fa-solid fa-magnifying-glass|Sourcing – Streamline candidate sourcing from multiple channels in one place",
            "fa-solid fa-briefcase|Jobs – Post, manage, and track open roles across the organization",
            "fa-solid fa-layer-group|Job catalog – Maintain a standardized library of job roles and descriptions",
            "fa-solid fa-people-group|Team overview – Get a snapshot of team composition, capacity, and status",
            "fa-solid fa-sitemap|Org chart – Visualize reporting structures and organizational hierarchy",
            "fa-solid fa-earth-americas|World map – Track global workforce distribution and locations",
            "fa-solid fa-chart-line|Performance – Drive continuous performance reviews and goal tracking",
            "fa-regular fa-chart-bar|Team performance – Measure and analyze performance across teams",
            "fa-solid fa-chart-simple|All reports – Generate detailed reports across all HR and payroll modules",
            "fa-solid fa-money-bill-trend-up|Salary explorer – Compare salary benchmarks and analyze pay structures",
            "fa-solid fa-calculator|Cost calculator – Estimate workforce costs with advanced budgeting tools",
            "fa-solid fa-store|Marketplace – Access third-party HR services, tools, and integrations",
            "fa-solid fa-shield-halved|Compliance watchtower – Stay compliant with labor laws and regulations"
        ],
        "Recruiters": [
            "fa-solid fa-magnifying-glass|Sourcing – Streamline candidate sourcing from multiple channels in one place",
            "fa-solid fa-briefcase|Jobs – Post, manage, and track open roles across the organization",
            "fa-solid fa-layer-group|Job catalog – Maintain a standardized library of job roles and descriptions",
            "fa-solid fa-clipboard-check|Onboarding – Automate new hire setup with seamless onboarding workflows"
        ],
        "Executives": [
            "fa-solid fa-users|Team – View and manage employee details, roles, and responsibilities",
            "fa-solid fa-people-group|Team overview – Get a snapshot of team composition, capacity, and status",
            "fa-solid fa-clipboard-check|Onboarding – Automate new hire setup with seamless onboarding workflows",
            "fa-solid fa-door-open|Offboarding – Manage exits smoothly while ensuring compliance and knowledge transfer",
            "fa-solid fa-gears|Workflows – Automate HR processes with customizable workflows",
            "fa-solid fa-folder-tree|Document management – Securely store, share, and manage HR documents"
        ],
        "Payroll": [
            "fa-solid fa-sack-dollar|Payroll – Automate salary calculations, compliance, and disbursements",
            "fa-solid fa-file-invoice-dollar|Contractor payments – Manage payments and contracts for freelance/contract staff",
            "fa-solid fa-file-invoice|Remote invoices – Process invoices for remote employees and contractors globally",
            "fa-solid fa-shield-halved|Compliance watchtower – Stay compliant with labor laws and regulations",
            "fa-solid fa-chart-simple|All reports – Generate detailed reports across all HR and payroll modules"
        ],
        "Expense/Accounts": [
            "fa-solid fa-file-invoice-dollar|Expenses – Submit, approve, and reimburse employee expenses easily",
            "fa-solid fa-file-invoice-dollar|Contractor payments – Manage payments and contracts for freelance/contract staff",
            "fa-solid fa-file-invoice|Remote invoices – Process invoices for remote employees and contractors globally",
            "fa-solid fa-calculator|Cost calculator – Estimate workforce costs with advanced budgeting tools",
            "fa-solid fa-chart-simple|All reports – Generate detailed reports across all HR and payroll modules"
        ]
    },

    "Leaders": {
        "Team Leads": [
            "fa-solid fa-people-group|Team overview – Get a snapshot of team composition, capacity, and status",
            "fa-solid fa-diagram-project|Projects – Assign, monitor, and track projects and resource allocation",
            "fa-solid fa-plane-departure|Time off – Centralize leave requests, balances, and approvals",
            "fa-regular fa-user-clock|Team absences – Monitor team availability and absences in real time",
            "fa-solid fa-chart-line|Performance – Drive continuous performance reviews and goal tracking"
        ],
        "Department Managers": [
            "fa-solid fa-people-group|Team overview – Get a snapshot of team composition, capacity, and status",
            "fa-solid fa-sitemap|Org chart – Visualize reporting structures and organizational hierarchy",
            "fa-solid fa-earth-americas|World map – Track global workforce distribution and locations",
            "fa-solid fa-diagram-project|Projects – Assign, monitor, and track projects and resource allocation",
            "fa-solid fa-file-invoice-dollar|Expenses – Submit, approve, and reimburse employee expenses easily",
            "fa-solid fa-gift|Team's benefits – Manage employee perks, benefits, and eligibility",
            "fa-regular fa-chart-bar|Team performance – Measure and analyze performance across teams",
            "fa-solid fa-chart-simple|All reports – Generate detailed reports across all HR and payroll modules"
        ],
        "Finance Managers": [
            "fa-solid fa-file-invoice-dollar|Expenses – Submit, approve, and reimburse employee expenses easily",
            "fa-solid fa-sack-dollar|Payroll – Automate salary calculations, compliance, and disbursements",
            "fa-solid fa-file-invoice-dollar|Contractor payments – Manage payments and contracts for freelance/contract staff",
            "fa-solid fa-file-invoice|Remote invoices – Process invoices for remote employees and contractors globally",
            "fa-solid fa-money-bill-trend-up|Salary explorer – Compare salary benchmarks and analyze pay structures",
            "fa-solid fa-calculator|Cost calculator – Estimate workforce costs with advanced budgeting tools",
            "fa-solid fa-shield-halved|Compliance watchtower – Stay compliant with labor laws and regulations"
        ]
    },

    "CXOs": {
        "CEO": [
            "fa-solid fa-chart-pie|Talent insights – Gain data-driven visibility into workforce trends and talent performance",
            "fa-solid fa-sitemap|Org chart – Visualize reporting structures and organizational hierarchy",
            "fa-solid fa-earth-americas|World map – Track global workforce distribution and locations",
            "fa-solid fa-chart-simple|All reports – Generate detailed reports across all HR and payroll modules",
            "fa-solid fa-store|Marketplace – Access third-party HR services, tools, and integrations"
        ],
        "CFO": [
            "fa-solid fa-scale-balanced|Equity – Administer and track employee equity and stock options",
            "fa-solid fa-trophy|Incentives – Design and manage performance-based incentive programs",
            "fa-solid fa-sack-dollar|Payroll – Automate salary calculations, compliance, and disbursements",
            "fa-solid fa-money-bill-trend-up|Salary explorer – Compare salary benchmarks and analyze pay structures",
            "fa-solid fa-calculator|Cost calculator – Estimate workforce costs with advanced budgeting tools",
            "fa-solid fa-shield-halved|Compliance watchtower – Stay compliant with labor laws and regulations"
        ],
        "CHRO": [
            "fa-solid fa-chart-pie|Talent insights – Gain data-driven visibility into workforce trends and talent performance",
            "fa-solid fa-chart-line|Performance – Drive continuous performance reviews and goal tracking",
            "fa-regular fa-chart-bar|Team performance – Measure and analyze performance across teams",
            "fa-solid fa-comments|Company feedback – Capture employee sentiment with surveys and feedback tools",
            "fa-solid fa-book-open|Employee handbooks – Digitize and distribute employee policies and guidelines"
        ],
        "CTO/COO": [
            "fa-solid fa-diagram-project|Projects – Assign, monitor, and track projects and resource allocation",
            "fa-solid fa-gears|Workflows – Automate HR processes with customizable workflows",
            "fa-solid fa-folder-tree|Document management – Securely store, share, and manage HR documents",
            "fa-solid fa-shield-halved|Compliance watchtower – Stay compliant with labor laws and regulations",
            "fa-solid fa-store|Marketplace – Access third-party HR services, tools, and integrations"
        ]
    },

    "Contractors / Remote staff": {
        "Contractor": [
            "fa-solid fa-clipboard-check|Onboarding – Automate new hire setup with seamless onboarding workflows",
            "fa-solid fa-door-open|Offboarding – Manage exits smoothly while ensuring compliance and knowledge transfer",
            "fa-regular fa-clock|Timesheets – Capture, approve, and process employee work logs",
            "fa-solid fa-diagram-project|Projects – Assign, monitor, and track projects and resource allocation",
            "fa-solid fa-file-invoice-dollar|Expenses – Submit, approve, and reimburse employee expenses easily",
            "fa-solid fa-file-invoice-dollar|Contractor payments – Manage payments and contracts for freelance/contract staff",
            "fa-solid fa-folder-tree|Document management – Securely store, share, and manage HR documents"
        ],
        "Remote Staff": [
            "fa-solid fa-clipboard-check|Onboarding – Automate new hire setup with seamless onboarding workflows",
            "fa-solid fa-door-open|Offboarding – Manage exits smoothly while ensuring compliance and knowledge transfer",
            "fa-solid fa-stopwatch|Time tracking – Track working hours, shifts, and productivity efficiently",
            "fa-regular fa-clock|Timesheets – Capture, approve, and process employee work logs",
            "fa-solid fa-diagram-project|Projects – Assign, monitor, and track projects and resource allocation",
            "fa-solid fa-file-invoice|Remote invoices – Process invoices for remote employees and contractors globally",
            "fa-solid fa-headset|Support monitor – Track employee support tickets and resolve HR queries",
            "fa-solid fa-folder-tree|Document management – Securely store, share, and manage HR documents"
        ]
    }
}

DEFAULT_THEME = "light"  # or "dark"

@app.before_request
def ensure_theme():
    if "theme" not in session:
        session["theme"] = DEFAULT_THEME

@app.route("/theme/toggle", methods=["POST"])
def theme_toggle():
    session["theme"] = "dark" if session.get("theme") == "light" else "light"
    return jsonify({"theme": session["theme"]})

@app.route("/theme/set", methods=["POST"])
def theme_set():
    data = request.get_json(silent=True) or {}
    mode = data.get("theme")
    if mode not in ("dark", "light"):
        return jsonify({"error": "invalid theme"}), 400
    session["theme"] = mode
    return jsonify({"theme": mode})

@app.context_processor
def inject_theme():
    return {"theme": session.get("theme", DEFAULT_THEME)}

# === Helpers ===
def generate_unique_employee_id():
    """Generate a unique 6-digit ID as a zero-padded string, retrying on collision."""
    while True:
        candidate = f"{random.randint(100000, 999999)}"
        if not User.query.filter_by(employee_id=candidate).first():
            return candidate

def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for('login'))
        return view_func(*args, **kwargs)
    return wrapper

def random_password(n=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))

def unique_username(seed: str) -> str:
    base = re.sub(r'[^a-z0-9]+', '', (seed or '').lower()) or "user"
    candidate = base
    i = 1
    while User.query.filter_by(username=candidate).first():
        i += 1
        candidate = f"{base}{i}"
    return candidate

def generate_offer_pdf(candidate, job_title: str, employee_id: str) -> str:
    """Create a simple PDF offer letter and return its file path."""
    fname = f"offer_{candidate.id}_{int(datetime.utcnow().timestamp())}.pdf"
    fpath = os.path.join(UPLOAD_OFFERS, fname)

    c = canvas.Canvas(fpath, pagesize=A4)
    width, height = A4
    y = height - 80

    lines = [
        "OFFER LETTER",
        "",
        f"Date: {datetime.utcnow().strftime('%Y-%m-%d')} (UTC)",
        "",
        f"Dear {candidate.name},",
        "",
        "Congratulations! We are pleased to offer you employment at PeopleOps.",
        f"Role: {job_title}",
        f"Employee ID: {employee_id}",
        "",
        "This offer is contingent upon standard background verification and HR formalities.",
        "",
        "Please reply to this email acknowledging acceptance of this offer.",
        "",
        "Welcome aboard!",
        "",
        "Regards,",
        "PeopleOps HR Team"
    ]
    for line in lines:
        c.drawString(72, y, line)
        y -= 22
    c.showPage()
    c.save()
    return fpath

from calendar import SATURDAY, SUNDAY

LEAVE_TYPES = ("Annual", "Sick", "Casual", "Unpaid")

# ===== Talent Insights (HR → Managers) =====
from datetime import date, timedelta, datetime
from calendar import monthrange, SATURDAY, SUNDAY

@app.route("/offers/<path:filename>")
#@login_required
def get_offer(filename):
    return send_from_directory(UPLOAD_OFFERS, filename, as_attachment=False)

# === Routes ===
@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        employee_type = request.form.get('employee_type') or ''
        subposition = request.form.get('subposition') or ''
        designation = None # NEW

        # Basic validations
        errors = []
        if not username: errors.append("Username is required.")
        if not name: errors.append("Name is required.")
        if not email: errors.append("Email is required.")
        if not password: errors.append("Password is required.")
        if password and len(password) < 6: errors.append("Password must be at least 6 characters.")
        if password != confirm_password: errors.append("Passwords do not match.")
        if not employee_type: errors.append("Employee Type is required.")
        if not subposition: errors.append("Subposition is required.")
        # NEW: require designation only for Employees
        if employee_type == "Employees" and not designation:
            errors.append("Designation is required for Employees.")

        if errors:
            for e in errors: flash(e, "error")
            return render_template('register.html')

        user = User(
            employee_id=generate_unique_employee_id(),
            username=username,
            name=name,
            email=email,
            employee_type=employee_type,
            subposition=subposition,
            designation = None
        )
        user.set_password(password)

        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if User.query.filter_by(username=username).first():
                flash("Username already exists. Choose another.", "error")
            elif User.query.filter_by(email=email).first():
                flash("Email already exists. Try logging in.", "error")
            else:
                flash("Could not create user due to a database constraint.", "error")
            return render_template('register.html')

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash("Invalid username or password.", "error")
            return render_template('login.html')

        # Save session fields (include designation)
        session['username'] = user.username
        session['employee_type'] = user.employee_type
        session['subposition'] = user.subposition
        session['employee_id'] = user.employee_id
        session['designation'] = user.designation  # NEW

        flash(f"Welcome back, {user.name}!", "success")
        return redirect(url_for('dashboard'))

    return render_template('login.html')

@app.route('/dashboard')
#@login_required
def dashboard():
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    features = ROLE_SUBPOSITION_FEATURES.get(employee_type, {}).get(subposition, [])

    return render_template(
        'dashboard.html',
        username=session.get('username'),
        employee_type=employee_type,
        subposition=subposition,
        employee_id=session.get('employee_id'),
        designation=session.get('designation'),  # NEW
        features=features
    )

@app.route("/feature/<role>/<sub>/<feature>")
#@login_required
def render_feature(role, sub, feature):
    """
    Render specific feature pages dynamically.
    Example:
      /feature/HR/Recruiter/sourcing
      → templates/HR/Recruiter/sourcing.html
    """
    try:
        return render_template(f"{role}/{sub}/{feature}.html")
    except:
        flash("Feature page not found.", "error")
        return redirect(url_for("dashboard"))

# --- Candidates API ---
@app.route("/api/candidates", methods=["GET"])
#@login_required
def api_candidates_list():
    username = session.get('username')

    # ✅ HR Managers can see all candidates; others only their own
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    if employee_type == 'HR' and subposition in ('Managers', 'Executives'):
        q = Candidate.query.order_by(Candidate.created_at.desc())
    else:
        q = Candidate.query.filter_by(created_by=username).order_by(Candidate.created_at.desc())

    job_id = request.args.get("job_id")
    if job_id:
        q = q.filter_by(job_id=job_id)

    rows = q.all()
    return jsonify([{
        "id": c.id,
        "name": c.name,
        "role_of_employment": c.role_of_employment,
        "qualification": c.qualification,

        "email": c.email,
        "phone": c.phone,
        "location": c.location,
        "years_experience": c.years_experience,
        "current_ctc": c.current_ctc,
        "expected_ctc": c.expected_ctc,
        "notice_period_days": c.notice_period_days,
        "source": c.source,
        "notes": c.notes,

        "resume_url": (url_for('get_resume', filename=os.path.basename(c.resume_path)) if c.resume_path else None),
        "stage": c.stage,
        "job_id": c.job_id,
        "job_title": c.job.title if c.job else None,
        "created_at": c.created_at.isoformat(),

        # ✅ recruiter info for HR views
        "recruiter_username": c.created_by,
        "recruiter_id": c.recruiter_id,
        "recruiter_name": (c.recruiter.name if c.recruiter else None),
        "recruiter_email": (c.recruiter.email if c.recruiter else None),
    } for c in rows])

@app.route("/api/candidates", methods=["POST"])
#@login_required
def api_candidates_create():
    username = session.get('username')
    recruiter = User.query.filter_by(username=username).first()

    name = (request.form.get("name") or "").strip()
    role_of_employment = (request.form.get("role_of_employment") or "").strip()
    qualification = (request.form.get("qualification") or "").strip()
    job_id = request.form.get("job_id")

    # NEW optional fields
    email = (request.form.get("email") or "").strip() or None
    phone = (request.form.get("phone") or "").strip() or None
    location = (request.form.get("location") or "").strip() or None
    source = (request.form.get("source") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    def to_int(v):
        try:
            return int(v) if v not in (None, "",) else None
        except:
            return None
    years_experience = to_int(request.form.get("years_experience"))
    current_ctc = to_int(request.form.get("current_ctc"))
    expected_ctc = to_int(request.form.get("expected_ctc"))
    notice_period_days = to_int(request.form.get("notice_period_days"))

    if not name or not role_of_employment or not job_id:
        return jsonify({"error": "Name, Role of Employment, and Job are required"}), 400

    job = Job.query.filter_by(id=job_id, created_by=username).first()
    if not job:
        return jsonify({"error": "Invalid job"}), 400

    resume_path = None
    if "resume" in request.files:
        file = request.files["resume"]
        if file and allowed_resume(file.filename):
            fname = secure_filename(file.filename)
            saved = os.path.join(app.config['UPLOAD_FOLDER'], f"{datetime.utcnow().timestamp()}_{fname}")
            file.save(saved)
            resume_path = saved

    candidate = Candidate(
        name=name,
        role_of_employment=role_of_employment,
        qualification=qualification,
        email=email,
        phone=phone,
        location=location,
        years_experience=years_experience,
        current_ctc=current_ctc,
        expected_ctc=expected_ctc,
        notice_period_days=notice_period_days,
        source=source,
        notes=notes,

        resume_path=resume_path,
        stage=StageEnum.SCREENING.value,
        job_id=job.id,

        # trackers
        created_by=username,                 # (keep)
        recruiter_id=recruiter.id if recruiter else None  # ✅ NEW
    )
    db.session.add(candidate)
    db.session.commit()
    return jsonify({"id": candidate.id}), 201

@app.route("/api/candidates/<int:candidate_id>/status", methods=["POST"])
#@login_required
def api_candidates_update_status(candidate_id):
    username = session.get('username')
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')

    # creator or HR Manager/Executive can update:
    can_update_any = (employee_type == 'HR' and subposition in ('Managers', 'Executives'))

    q = Candidate.query.filter_by(id=candidate_id)
    if not can_update_any:
        q = q.filter_by(created_by=username)

    c = q.first()
    if not c:
        return jsonify({"error": "Candidate not found"}), 404

    new_stage = (request.form.get("stage") or (request.json.get("stage") if request.is_json else "")).strip()
    valid_stages = [s.value for s in StageEnum]
    if new_stage not in valid_stages:
        return jsonify({"error": "Invalid stage", "valid": valid_stages}), 400

    c.stage = new_stage
    db.session.commit()
    return jsonify({"ok": True, "stage": c.stage})

# Serve resumes
@app.route("/resumes/<path:filename>")
#@login_required
def get_resume(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)


# List jobs (current user or all for HR Managers/Executives)
@app.route("/api/jobs", methods=["GET"])
#@login_required
def api_jobs_list():
    username = session.get('username')
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')

    # HR Managers/Executives see all jobs; others see only their own
    if employee_type == 'HR' and subposition in ('Managers', 'Executives'):
        q = Job.query.order_by(Job.created_at.desc())
    else:
        q = Job.query.filter_by(created_by=username).order_by(Job.created_at.desc())

    # Optional filters: ?status=Open|Closed, ?q=term, plus department/location
    status = (request.args.get("status") or "").strip()
    if status in ("Open", "Closed"):
        q = q.filter_by(status=status)

    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(
            db.or_(
                Job.title.ilike(like),
                Job.department.ilike(like),
                Job.location.ilike(like),
                Job.description.ilike(like),
            )
        )

    # Optional structured filters
    for field in ("department", "location", "employment_type"):
        val = (request.args.get(field) or "").strip()
        if val:
            q = q.filter(getattr(Job, field) == val)

    rows = q.all()
    return jsonify([{
        "id": j.id,
        "title": j.title,
        "department": j.department,
        "location": j.location,
        "employment_type": j.employment_type,
        "openings": j.openings,
        "description": j.description,
        "status": j.status,
        "created_by": j.created_by,
        "created_at": j.created_at.isoformat()
    } for j in rows])


@app.route("/api/users/stats", methods=["GET"])
#@login_required
def api_users_stats():
    """
    Get user statistics for team overview dashboard
    Uses same permission checks as your existing /api/users endpoint
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # Only HR Managers, Leaders, and CXOs can access stats (same pattern as your other APIs)
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    # Get all users using same query pattern as your existing /api/users
    users = User.query.order_by(User.created_at.desc()).all()
    
    # Calculate stats
    total_users = len(users)
    by_type = {}
    by_subposition = {}
    recent_hires = 0
    
    from datetime import datetime, timedelta
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    for user in users:
        # By type
        user_type = user.employee_type or 'Unknown'
        by_type[user_type] = by_type.get(user_type, 0) + 1
        
        # By subposition (department)
        subpos = user.subposition or 'Unassigned'  
        by_subposition[subpos] = by_subposition.get(subpos, 0) + 1
        
        # Recent hires (last 30 days)
        if user.created_at and user.created_at >= thirty_days_ago:
            recent_hires += 1
    
    return jsonify({
        "total_users": total_users,
        "by_type": by_type,
        "by_subposition": by_subposition,
        "recent_hires": recent_hires
    })

@app.route("/api/projects/team", methods=["GET"])
#@login_required
def api_projects_team():
    """
    Get projects for team overview - shows all projects for capacity planning
    Extends your existing /api/projects endpoint with team visibility
    """
    username = session.get("username")
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # HR Managers and Leaders can see all projects for capacity planning
    if employee_type == 'HR' and subposition in ('Managers', 'Executives'):
        projects = Project.query.order_by(Project.created_at.desc()).all()
    elif employee_type in ('Leaders', 'CXOs'):
        projects = Project.query.order_by(Project.created_at.desc()).all() 
    else:
        # Regular users see only their own projects (existing pattern)
        projects = Project.query.filter_by(created_by=username).order_by(Project.created_at.desc()).all()
    
    result = []
    for p in projects:
        # Parse team members (same as your existing logic)
        team_members = []
        if p.team_csv:
            team_members = [x.strip() for x in p.team_csv.split(',') if x.strip()]
        
        # Parse allocations (same as your existing logic)
        allocations = {}
        if p.allocations_json:
            try:
                allocations = json.loads(p.allocations_json)
            except:
                pass
        
        result.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "progress": p.progress,
            "start_date": p.start_date.isoformat() if p.start_date else None,
            "end_date": p.end_date.isoformat() if p.end_date else None,
            "created_by": p.created_by,
            "team_members": team_members,
            "allocations": allocations,
            "team_size": len(team_members),
            "total_allocated_hours": sum(allocations.values()) if allocations else 0,
            "created_at": p.created_at.isoformat() if p.created_at else None
        })
    
    return jsonify(result)

@app.route("/api/leaves/current", methods=["GET"])
#@login_required 
def api_leaves_current():
    """
    Get current team absences - extends your existing /api/leaves endpoint
    Shows who's currently on leave for capacity planning
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # Same permission pattern as your existing leave APIs
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    from datetime import date
    today = date.today()
    
    # Get current leave requests that are approved and active (same pattern as existing)
    current_leaves = LeaveRequest.query.filter(
        LeaveRequest.status == 'Approved',
        LeaveRequest.start_date <= today,
        LeaveRequest.end_date >= today
    ).order_by(LeaveRequest.start_date.desc()).all()
    
    absences = []
    for leave in current_leaves:
        # Get user info (same pattern as your existing code)
        user = User.query.filter_by(username=leave.username).first()
        absences.append({
            "id": leave.id,
            "username": leave.username,
            "name": user.name if user else leave.username,
            "employee_type": user.employee_type if user else None,
            "subposition": user.subposition if user else None,
            "leave_type": leave.leave_type,
            "start_date": leave.start_date.isoformat(),
            "end_date": leave.end_date.isoformat(),
            "days": leave.days,
            "reason": leave.reason,
            "created_at": leave.created_at.isoformat()
        })
    
    return jsonify(absences)

@app.route("/api/team/activity", methods=["GET"])
#@login_required
def api_team_activity():
    """
    Get recent team activity for the overview dashboard
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # Same permission checks
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    activities = []
    
    # Recent hires (last 30 days) - using existing user data
    from datetime import datetime, timedelta
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    recent_users = User.query.filter(
        User.created_at >= thirty_days_ago
    ).order_by(User.created_at.desc()).limit(10).all()
    
    for user in recent_users:
        activities.append({
            "type": "hire",
            "title": f"{user.name} joined as {user.subposition}",
            "description": f"New {user.employee_type} team member",
            "user": user.name,
            "employee_type": user.employee_type,
            "subposition": user.subposition,
            "created_at": user.created_at.isoformat(),
            "icon": "fa-user-plus",
            "color": "green"
        })
    
    # Recent leave approvals (last 7 days) - using existing leave data
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_leaves = LeaveRequest.query.filter(
        LeaveRequest.updated_at >= seven_days_ago,
        LeaveRequest.status == 'Approved'
    ).order_by(LeaveRequest.updated_at.desc()).limit(5).all()
    
    for leave in recent_leaves:
        user = User.query.filter_by(username=leave.username).first()
        activities.append({
            "type": "leave",
            "title": f"{user.name if user else leave.username} - {leave.leave_type} leave approved",
            "description": f"{leave.days} days from {leave.start_date} to {leave.end_date}",
            "user": user.name if user else leave.username,
            "leave_type": leave.leave_type,
            "days": leave.days,
            "created_at": leave.updated_at.isoformat() if leave.updated_at else leave.created_at.isoformat(),
            "icon": "fa-calendar-check",
            "color": "blue"
        })
    
    # Sort all activities by date
    activities.sort(key=lambda x: x["created_at"], reverse=True)
    
    return jsonify(activities[:15])  # Return top 15 most recent

@app.route("/api/dashboard/metrics", methods=["GET"])
#@login_required
def api_dashboard_metrics():
    """
    Get all metrics for team overview dashboard in one call
    Combines data from multiple existing endpoints for better performance
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    from datetime import datetime, timedelta, date
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    today = date.today()
    
    # Get all data in single queries (efficient)
    total_users = User.query.count()
    recent_hires = User.query.filter(User.created_at >= thirty_days_ago).count()
    
    # Jobs data (using existing Job model)
    total_jobs = Job.query.count()
    open_jobs = Job.query.filter_by(status='Open').count()
    
    # Candidates data (using existing Candidate model) 
    total_candidates = Candidate.query.count()
    pipeline_candidates = Candidate.query.filter(
        ~Candidate.stage.in_(['Rejected', 'Onboarding'])
    ).count()
    
    # Current absences
    current_absences = LeaveRequest.query.filter(
        LeaveRequest.status == 'Approved',
        LeaveRequest.start_date <= today,
        LeaveRequest.end_date >= today
    ).count()
    
    # Active projects
    active_projects = Project.query.filter_by(status='Active').count()
    
    return jsonify({
        "headcount": {
            "total": total_users,
            "recent_hires": recent_hires
        },
        "recruitment": {
            "total_jobs": total_jobs,
            "open_jobs": open_jobs,
            "total_candidates": total_candidates,
            "pipeline_candidates": pipeline_candidates
        },
        "capacity": {
            "current_absences": current_absences,
            "active_projects": active_projects
        }
    })


# Create job
@app.route("/api/jobs", methods=["POST"])
#@login_required
def api_jobs_create():
    username = session.get('username')

    get = (request.json or request.form)
    title = (get.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Job title is required"}), 400

    job = Job(
        title=title,
        department=(get.get("department") or "").strip() or None,
        location=(get.get("location") or "").strip() or None,
        employment_type=(get.get("employment_type") or "").strip() or None,
        openings=int(get.get("openings") or 1),
        description=(get.get("description") or "").strip() or None,
        status="Open",
        created_by=username,
    )
    db.session.add(job)
    db.session.commit()
    return jsonify({"id": job.id}), 201

# Read single job (optional helper)
@app.route("/api/jobs/<int:job_id>", methods=["GET"])
#@login_required
def api_jobs_read(job_id):
    username = session.get('username')
    j = Job.query.filter_by(id=job_id, created_by=username).first()
    if not j:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": j.id,
        "title": j.title,
        "department": j.department,
        "location": j.location,
        "employment_type": j.employment_type,
        "openings": j.openings,
        "description": j.description,
        "status": j.status,
        "created_at": j.created_at.isoformat()
    })

# Update job (title/fields/status)
@app.route("/api/jobs/<int:job_id>", methods=["POST", "PUT", "PATCH"])
#@login_required
def api_jobs_update(job_id):
    username = session.get('username')
    j = Job.query.filter_by(id=job_id, created_by=username).first()
    if not j:
        return jsonify({"error": "Not found"}), 404

    data = (request.json or request.form)

    def norm(s):
        return (s or "").strip()

    if "title" in data:
        if not norm(data.get("title")):
            return jsonify({"error": "Title cannot be empty"}), 400
        j.title = norm(data.get("title"))

    if "department" in data: j.department = norm(data.get("department")) or None
    if "location" in data: j.location = norm(data.get("location")) or None
    if "employment_type" in data: j.employment_type = norm(data.get("employment_type")) or None
    if "openings" in data:
        try:
            j.openings = int(data.get("openings") or 1)
        except:
            return jsonify({"error": "Openings must be an integer"}), 400
    if "description" in data: j.description = norm(data.get("description")) or None
    if "status" in data:
        new_status = norm(data.get("status"))
        if new_status not in ("Open", "Closed"):
            return jsonify({"error": "Invalid status"}), 400
        j.status = new_status

    db.session.commit()
    return jsonify({"ok": True})

# Delete job
@app.route("/api/jobs/<int:job_id>", methods=["DELETE"])
#@login_required
def api_jobs_delete(job_id):
    username = session.get('username')
    j = Job.query.filter_by(id=job_id, created_by=username).first()
    if not j:
        return jsonify({"error": "Not found"}), 404

    # Optional: prevent delete if there are candidates mapped to this job
    # if j.candidates: return jsonify({"error":"Cannot delete a job that has candidates."}), 400

    db.session.delete(j)
    db.session.commit()
    return jsonify({"ok": True})

# List catalog entries (owned by current user)
# Filters: ?status=Active|Archived, ?q=term, plus department/location/level/employment_type/family
@app.route("/api/job_catalog", methods=["GET"])
#@login_required
def api_job_catalog_list():
    username = session.get('username')
    q = JobCatalog.query.filter_by(created_by=username).order_by(JobCatalog.created_at.desc())

    # status filter
    status = (request.args.get("status") or "").strip()
    if status in ("Active", "Archived"):
        q = q.filter_by(status=status)

    # structured filters
    for field in ("department", "location", "level", "employment_type", "family"):
        val = (request.args.get(field) or "").strip()
        if val:
            q = q.filter(getattr(JobCatalog, field) == val)

    # text search
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(
            db.or_(
                JobCatalog.title.ilike(like),
                JobCatalog.department.ilike(like),
                JobCatalog.location.ilike(like),
                JobCatalog.description.ilike(like),
                JobCatalog.responsibilities.ilike(like),
                JobCatalog.requirements.ilike(like),
                JobCatalog.skills.ilike(like),
                JobCatalog.family.ilike(like),
                JobCatalog.level.ilike(like),
            )
        )

    rows = q.all()
    return jsonify([{
        "id": r.id,
        "title": r.title,
        "family": r.family,
        "level": r.level,
        "department": r.department,
        "location": r.location,
        "employment_type": r.employment_type,
        "min_experience": r.min_experience,
        "max_experience": r.max_experience,
        "salary_min": r.salary_min,
        "salary_max": r.salary_max,
        "currency": r.currency,
        "description": r.description,
        "responsibilities": r.responsibilities,
        "requirements": r.requirements,
        "skills": r.skills,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    } for r in rows])

# Create catalog entry
@app.route("/api/job_catalog", methods=["POST"])
#@login_required
def api_job_catalog_create():
    username = session.get('username')
    data = (request.json or request.form)

    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400

    def norm(s): return (s or "").strip() or None
    def to_int(x):
        try: return int(x) if x not in (None, "",) else None
        except: return None

    row = JobCatalog(
        title=title,
        family=norm(data.get("family")),
        level=norm(data.get("level")),
        department=norm(data.get("department")),
        location=norm(data.get("location")),
        employment_type=norm(data.get("employment_type")),
        min_experience=to_int(data.get("min_experience")),
        max_experience=to_int(data.get("max_experience")),
        salary_min=to_int(data.get("salary_min")),
        salary_max=to_int(data.get("salary_max")),
        currency=norm(data.get("currency")) or "INR",
        description=norm(data.get("description")),
        responsibilities=norm(data.get("responsibilities")),
        requirements=norm(data.get("requirements")),
        skills=norm(data.get("skills")),
        status="Active",
        created_by=username,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id}), 201

# Read one
@app.route("/api/job_catalog/<int:catalog_id>", methods=["GET"])
#@login_required
def api_job_catalog_read(catalog_id):
    username = session.get('username')
    r = JobCatalog.query.filter_by(id=catalog_id, created_by=username).first()
    if not r:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": r.id,
        "title": r.title,
        "family": r.family,
        "level": r.level,
        "department": r.department,
        "location": r.location,
        "employment_type": r.employment_type,
        "min_experience": r.min_experience,
        "max_experience": r.max_experience,
        "salary_min": r.salary_min,
        "salary_max": r.salary_max,
        "currency": r.currency,
        "description": r.description,
        "responsibilities": r.responsibilities,
        "requirements": r.requirements,
        "skills": r.skills,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    })

# Update (fields + status)
@app.route("/api/job_catalog/<int:catalog_id>", methods=["POST", "PUT", "PATCH"])
#@login_required
def api_job_catalog_update(catalog_id):
    username = session.get('username')
    r = JobCatalog.query.filter_by(id=catalog_id, created_by=username).first()
    if not r:
        return jsonify({"error": "Not found"}), 404

    data = (request.json or request.form)
    def norm(s): return (s or "").strip()

    # simple field mapping
    mapping = {
        "title": "title", "family": "family", "level": "level",
        "department": "department", "location": "location",
        "employment_type": "employment_type", "description": "description",
        "responsibilities": "responsibilities", "requirements": "requirements",
        "skills": "skills", "currency": "currency",
    }
    for k, attr in mapping.items():
        if k in data:
            val = norm(data.get(k))
            setattr(r, attr, val or None)

    # numeric fields
    for k in ("min_experience", "max_experience", "salary_min", "salary_max"):
        if k in data:
            try:
                setattr(r, k, int(data.get(k)))  # allow None-like handled above if empty
            except:
                setattr(r, k, None)

    # status
    if "status" in data:
        s = norm(data.get("status"))
        if s not in ("Active", "Archived"):
            return jsonify({"error": "Invalid status"}), 400
        r.status = s

    db.session.commit()
    return jsonify({"ok": True})

# Delete
@app.route("/api/job_catalog/<int:catalog_id>", methods=["DELETE"])
#@login_required
def api_job_catalog_delete(catalog_id):
    username = session.get('username')
    r = JobCatalog.query.filter_by(id=catalog_id, created_by=username).first()
    if not r:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(r)
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/onboarding", methods=["GET"])
#@login_required
def api_onboarding_list():
    username = session.get('username')
    rows = Candidate.query.filter_by(created_by=username, stage="Onboarding") \
                          .order_by(Candidate.created_at.desc()).all()
    return jsonify([{
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "phone": c.phone,
        "job_title": c.job.title if c.job else None,
        "role_of_employment": c.role_of_employment,
        "recruited": c.recruited,
        "offer_url": (url_for('get_offer', filename=os.path.basename(c.offer_letter_path))
                      if c.offer_letter_path else None),
        "onboarded_at": (c.onboarded_at.isoformat() if c.onboarded_at else None),
    } for c in rows])

@app.route("/api/onboarding/<int:candidate_id>/offer", methods=["POST"])
#@login_required
def api_onboarding_offer(candidate_id):
    username = session.get('username')
    c = Candidate.query.filter_by(id=candidate_id, created_by=username, stage="Onboarding").first()
    if not c:
        return jsonify({"error": "Candidate not found or not in Onboarding stage"}), 404
    if not c.email:
        return jsonify({"error": "Candidate has no email address"}), 400

    # Build employee identity & account
    emp_id = generate_unique_employee_id()
    seed = (c.email.split("@")[0] if c.email else c.name).lower()
    uname = unique_username(seed)
    pwd = random_password()
    job_title = c.job.title if c.job else (c.role_of_employment or "Employee")

    # Ensure unique email in Users table; if clash, alias it
    u_email = (c.email or "").lower()
    if User.query.filter_by(email=u_email).first():
        parts = u_email.split("@")
        alias = f"+{emp_id}"
        u_email = f"{parts[0]}{alias}@{parts[1]}" if len(parts) == 2 else f"{u_email}.{emp_id}"

    # ✅ Key change: always onboard as Employees → New; designation = job role
    user = User(
        employee_id=emp_id,
        username=uname,
        name=c.name,
        email=u_email,
        employee_type="Employees",
        subposition="New",        # <-- always NEW on creation by HR
        designation=job_title     # <-- store job role as designation
    )
    user.set_password(pwd)
    db.session.add(user)
    db.session.flush()  # get ID if needed

    # Generate PDF
    offer_path = generate_offer_pdf(c, job_title, emp_id)

    # Build & send email
    msg = Message(
        subject="Congratulations! Your Offer Letter from PeopleOps",
        recipients=[c.email],  # to candidate's real email
        sender=app.config["MAIL_DEFAULT_SENDER"],
        body=(
            f"Dear {c.name},\n\n"
            f"Congratulations! Please find attached your offer letter for the role of '{job_title}'.\n\n"
            f"Your portal access has been created:\n"
            f"  • Username: {uname}\n"
            f"  • Temporary password: {pwd}\n\n"
            "Please log in and change your password after first login.\n\n"
            "Regards,\nPeopleOps HR"
        ),
    )
    with open(offer_path, "rb") as f:
        msg.attach(filename=os.path.basename(offer_path),
                   content_type="application/pdf",
                   data=f.read())

    try:
        log.info("MAIL → server=%s port=%s tls=%s ssl=%s sender=%s",
                 app.config["MAIL_SERVER"], app.config["MAIL_PORT"],
                 app.config["MAIL_USE_TLS"], app.config["MAIL_USE_SSL"],
                 app.config["MAIL_DEFAULT_SENDER"])
        with mail.connect() as conn:
            conn.send(msg)
    except Exception as e:
        log.exception("Failed to send email")
        db.session.rollback()
        return jsonify({"error": f"SMTP send failed: {type(e).__name__}: {e}"}), 500

    # Persist offer info on candidate
    c.offer_letter_path = offer_path
    c.recruited = True
    c.onboarded_at = datetime.utcnow()
    db.session.commit()

    try:
        # Build target key for this new employee
        target_key = user.employee_id or user.username

        rows = load_docs_index()

        # 1) Offer letter entry
        offer_fname = os.path.basename(offer_path)
        rows.insert(0, {
            "id": uuid.uuid4().hex[:12],
            "for": target_key,
            "title": f"Offer Letter - {job_title}",
            "category": "Offer",
            "period": datetime.utcnow().strftime("%Y-%m"),
            "filename": offer_fname,
            "url": url_for("get_offer", filename=offer_fname),   # served by /offers/
            "uploaded_at": datetime.utcnow().isoformat(),
            "uploaded_by": session.get("username"),
        })

        # 2) Copy candidate resume (if existed) into managed docs
        if c.resume_path and os.path.exists(c.resume_path):
            safe_resume_name = secure_filename(os.path.basename(c.resume_path))
            managed_name = f"{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:8]}_{safe_resume_name}"
            dest_path = os.path.join(DOCS_FILES_DIR, managed_name)
            shutil.copyfile(c.resume_path, dest_path)
            rows.insert(0, {
                "id": uuid.uuid4().hex[:12],
                "for": target_key,
                "title": "Resume",
                "category": "Resume",
                "period": datetime.utcnow().strftime("%Y-%m"),
                "filename": managed_name,
                "url": url_for("get_managed_doc", filename=managed_name),
                "uploaded_at": datetime.utcnow().isoformat(),
                "uploaded_by": session.get("username"),
            })

        save_docs_index(rows)
    except Exception as _e:
        log.exception("Failed to auto-register onboarding documents")

    return jsonify({
        "ok": True,
        "offer_url": url_for('get_offer', filename=os.path.basename(offer_path))
    })

@app.route("/onboarding_docs/<path:filename>")
#@login_required
def get_onboarding_doc(filename):
    return send_from_directory(ONBOARDING_DOCS_DIR, filename, as_attachment=False)

# ===== Self-Onboarding APIs (for Employees → New) =====

@app.route("/api/self_onboarding", methods=["GET"])
#@login_required
def api_self_onboarding_get():
    username = session.get("username")
    data = load_onboarding(username)
    return jsonify(data)

@app.route("/api/self_onboarding", methods=["POST"])
#@login_required
def api_self_onboarding_update():
    username = session.get("username")
    payload = request.json or request.form
    data = load_onboarding(username)

    # Update personal
    if "personal" in payload:
        data["personal"].update({
            "full_name": (payload["personal"].get("full_name") or data["personal"]["full_name"]),
            "dob": (payload["personal"].get("dob") or data["personal"]["dob"]),
            "address": (payload["personal"].get("address") or data["personal"]["address"]),
            "phone": (payload["personal"].get("phone") or data["personal"]["phone"]),
            "emergency_contact": (payload["personal"].get("emergency_contact") or data["personal"]["emergency_contact"]),
        })

    # Update bank
    if "bank" in payload:
        data["bank"].update({
            "account_name": (payload["bank"].get("account_name") or data["bank"]["account_name"]),
            "account_number": (payload["bank"].get("account_number") or data["bank"]["account_number"]),
            "ifsc": (payload["bank"].get("ifsc") or data["bank"]["ifsc"]),
            "bank_name": (payload["bank"].get("bank_name") or data["bank"]["bank_name"]),
        })

    # Update tasks
    if "tasks" in payload:
        for k, v in payload["tasks"].items():
            if k in data["tasks"]:
                data["tasks"][k] = bool(v)

    # Recompute completion: all tasks true + required docs (PAN, Aadhaar, Photo)
    docs = data.get("documents", {})
    tasks_ok = all(data["tasks"].values()) if data.get("tasks") else False
    docs_ok = all(docs.get(k) for k in ("pan", "aadhaar", "photo"))
    data["completed"] = bool(tasks_ok and docs_ok)

    save_onboarding(username, data)

    # 🔁 Promote New → Existing if onboarding is complete (idempotent)
    if data["completed"]:
        user = User.query.filter_by(username=username).first()
        if user and user.subposition == "New":
            user.subposition = "Existing"
            db.session.commit()
            # Reflect immediately in current session
            session["subposition"] = "Existing"

    return jsonify({"ok": True, "completed": data["completed"]})

@app.route("/api/self_onboarding/upload", methods=["POST"])
#@login_required
def api_self_onboarding_upload():
    username = session.get("username")
    data = load_onboarding(username)

    # Expect fields: "pan", "aadhaar", "photo", "cancelled_cheque"
    if not request.files:
        return jsonify({"error": "No file provided"}), 400

    updated = []
    for field in ("pan", "aadhaar", "photo", "cancelled_cheque"):
        f = request.files.get(field)
        if not f:
            continue
        if f and allowed_onboarding_doc(f.filename):
            fname = secure_filename(f"{username}_{field}_{int(datetime.utcnow().timestamp())}_{f.filename}")
            path = os.path.join(ONBOARDING_DOCS_DIR, fname)
            f.save(path)
            data["documents"][field] = url_for("get_onboarding_doc", filename=fname)
            updated.append(field)
        else:
            return jsonify({"error": f"Invalid file for {field}"}), 400

    # Recompute completion after file(s) upload
    docs = data.get("documents", {})
    tasks_ok = all(data["tasks"].values()) if data.get("tasks") else False
    docs_ok = all(docs.get(k) for k in ("pan", "aadhaar", "photo"))
    data["completed"] = bool(tasks_ok and docs_ok)

    save_onboarding(username, data)

    # 🔁 Promote New → Existing if onboarding is now complete (idempotent)
    if data["completed"]:
        user = User.query.filter_by(username=username).first()
        if user and user.subposition == "New":
            user.subposition = "Existing"
            db.session.commit()
            # Reflect immediately in current session
            session["subposition"] = "Existing"

    return jsonify({
        "ok": True,
        "updated": updated,
        "completed": data["completed"],
        "documents": data["documents"],
    })

@app.route("/handbooks/<path:filename>")
#@login_required
def get_handbook_file(filename):
    return send_from_directory(HANDBOOKS_DIR, filename, as_attachment=False)

@app.route("/docs/<path:filename>")
#@login_required
def get_managed_doc(filename):
    return send_from_directory(DOCS_FILES_DIR, filename, as_attachment=False)





# Leaders Team Leads +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# === Team Overview: lightweight users list ===
@app.route("/api/users", methods=["GET"])
#@login_required
def api_users_list():
    """
    Returns users for snapshot views.
    Optional query params:
      ?q=<search in name/email/username>
      ?type=Employees|HR|Leaders|CXOs
    """
    q = (request.args.get("q") or "").strip().lower()
    etype = (request.args.get("type") or "").strip()

    qry = User.query
    if etype:
        qry = qry.filter_by(employee_type=etype)

    rows = qry.order_by(User.created_at.desc()).all()
    out = []
    for u in rows:
        if q and not (
            (u.name or "").lower().find(q) != -1
            or (u.email or "").lower().find(q) != -1
            or (u.username or "").lower().find(q) != -1
        ):
            continue
        out.append({
            "id": u.id,
            "employee_id": u.employee_id,
            "username": u.username,
            "name": u.name,
            "email": u.email,
            "employee_type": u.employee_type,
            "subposition": u.subposition,
            "designation": u.designation,
            "created_at": u.created_at.isoformat(),
        })
    return jsonify(out)

from datetime import datetime
import json

def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else None
    except:
        return None

def _csv_to_list(s):
    if not s: return []
    return [x.strip() for x in s.split(",") if x.strip()]

def _list_to_csv(lst):
    return ",".join(sorted(set([x.strip() for x in (lst or []) if x and x.strip()])))

# ---- Projects: List ----
@app.route("/api/projects", methods=["GET"])
#@login_required
def api_projects_list():
    """
    List projects created by current user (Team Lead).
    Filters:
      ?q= search in name/description
      ?status=Planned|Active|On Hold|Completed
    """
    username = session.get("username")
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip()

    query = Project.query.filter_by(created_by=username).order_by(Project.created_at.desc())
    if status in ("Planned", "Active", "On Hold", "Completed"):
        query = query.filter_by(status=status)

    rows = query.all()
    out = []
    for p in rows:
        if q and q not in (p.name or "").lower() and q not in (p.description or "").lower():
            continue
        out.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "progress": p.progress,
            "start_date": p.start_date.isoformat() if p.start_date else None,
            "end_date": p.end_date.isoformat() if p.end_date else None,
            "created_by": p.created_by,
            "team": _csv_to_list(p.team_csv),
            "allocations": (json.loads(p.allocations_json) if p.allocations_json else {}),
            "created_at": p.created_at.isoformat()
        })
    return jsonify(out)

# ---- Projects: Create ----
@app.route("/api/projects", methods=["POST"])
#@login_required
def api_projects_create():
    username = session.get("username")
    data = request.json or request.form

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Project name is required"}), 400

    description = (data.get("description") or "").strip() or None
    status = (data.get("status") or "Planned").strip()
    if status not in ("Planned", "Active", "On Hold", "Completed"):
        status = "Planned"

    progress = 0
    try:
        progress = int(data.get("progress") or 0)
        progress = max(0, min(100, progress))
    except:
        progress = 0

    start_date = _parse_date(data.get("start_date"))
    end_date   = _parse_date(data.get("end_date"))

    # team & allocations
    team = data.get("team") or []  # expect list of usernames
    if isinstance(team, str):
        # allow comma-separated fall-back
        team = _csv_to_list(team)

    allocations = data.get("allocations") or {}  # expect dict {username: hours}
    # sanitize allocations
    clean_alloc = {}
    if isinstance(allocations, dict):
        for k, v in allocations.items():
            try:
                hrs = int(v)
                if hrs < 0: hrs = 0
                clean_alloc[k] = hrs
            except:
                continue

    row = Project(
        name=name,
        description=description,
        status=status,
        progress=progress,
        start_date=start_date,
        end_date=end_date,
        created_by=username,
        team_csv=_list_to_csv(team),
        allocations_json=(json.dumps(clean_alloc) if clean_alloc else None),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id}), 201

# ---- Projects: Update ----
@app.route("/api/projects/<int:project_id>", methods=["PUT", "PATCH", "POST"])
#@login_required
def api_projects_update(project_id):
    username = session.get("username")
    p = Project.query.filter_by(id=project_id, created_by=username).first()
    if not p:
        return jsonify({"error": "Not found"}), 404

    data = request.json or request.form

    def norm(s): return (s or "").strip()

    if "name" in data:
        if not norm(data.get("name")):
            return jsonify({"error": "Name cannot be empty"}), 400
        p.name = norm(data.get("name"))

    if "description" in data:
        p.description = norm(data.get("description")) or None

    if "status" in data:
        s = norm(data.get("status"))
        if s in ("Planned", "Active", "On Hold", "Completed"):
            p.status = s

    if "progress" in data:
        try:
            pr = int(data.get("progress"))
            p.progress = max(0, min(100, pr))
        except:
            pass

    if "start_date" in data: p.start_date = _parse_date(data.get("start_date"))
    if "end_date" in data:   p.end_date   = _parse_date(data.get("end_date"))

    if "team" in data:
        team = data.get("team")
        if isinstance(team, str):
            team = _csv_to_list(team)
        p.team_csv = _list_to_csv(team or [])

    if "allocations" in data:
        allocations = data.get("allocations") or {}
        clean_alloc = {}
        if isinstance(allocations, dict):
            for k, v in allocations.items():
                try:
                    hrs = int(v)
                    if hrs < 0: hrs = 0
                    clean_alloc[k] = hrs
                except:
                    continue
        p.allocations_json = json.dumps(clean_alloc) if clean_alloc else None

    db.session.commit()
    return jsonify({"ok": True})

# ---- Projects: Delete ----
@app.route("/api/projects/<int:project_id>", methods=["DELETE"])
#@login_required
def api_projects_delete(project_id):
    username = session.get("username")
    p = Project.query.filter_by(id=project_id, created_by=username).first()
    if not p:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(p)
    db.session.commit()
    return jsonify({"ok": True})

# Get balances (current user; HR/Leaders can query ?user=<username>)
@app.route("/api/leave/balances", methods=["GET"])
#@login_required
def api_leave_balances_get():
    target = request.args.get("user") or session.get("username")
    if target != session.get("username") and not is_approver():
        return jsonify({"error": "Not allowed"}), 403
    bals = LeaveBalance.query.filter_by(username=target).all()
    # ensure all LEAVE_TYPES present
    present = {b.leave_type for b in bals}
    for t in LEAVE_TYPES:
        if t not in present:
            bals.append(LeaveBalance(username=target, leave_type=t, balance_days=0))
    return jsonify([{ "leave_type": b.leave_type, "balance_days": b.balance_days } for b in bals])

# Set balances (HR/Leaders only)
@app.route("/api/leave/balances/set", methods=["POST"])
#@login_required
def api_leave_balances_set():
    if not is_approver():
        return jsonify({"error": "Not allowed"}), 403
    data = request.json or request.form
    target = (data.get("user") or "").strip()
    if not target:
        return jsonify({"error": "user is required"}), 400
    get_user_or_abort(target)  # ensure exists
    updates = data.get("balances") or {}  # {"Annual": 12, ...}
    for lt, val in updates.items():
        if lt not in LEAVE_TYPES:
            continue
        try:
            v = max(0, int(val))
        except:
            v = 0
        row = LeaveBalance.query.filter_by(username=target, leave_type=lt).first()
        if not row:
            row = LeaveBalance(username=target, leave_type=lt, balance_days=v)
            db.session.add(row)
        else:
            row.balance_days = v
    db.session.commit()
    return jsonify({"ok": True})

# List requests
#  - normal users: their own
#  - HR/Leaders: ?scope=all to see everyone; else own
@app.route("/api/leaves", methods=["GET"])
#@login_required
def api_leaves_list():
    scope = (request.args.get("scope") or "").strip()
    status = (request.args.get("status") or "").strip()
    username = session.get("username")
    q = LeaveRequest.query
    if scope == "all" and is_approver():
        pass
    else:
        q = q.filter_by(username=username)
    if status in ("Pending", "Approved", "Rejected", "Cancelled"):
        q = q.filter_by(status=status)
    rows = q.order_by(LeaveRequest.created_at.desc()).all()
    return jsonify([{
        "id": r.id,
        "username": r.username,
        "leave_type": r.leave_type,
        "start_date": r.start_date.isoformat(),
        "end_date": r.end_date.isoformat(),
        "days": r.days,
        "reason": r.reason,
        "status": r.status,
        "approver": r.approver,
        "created_at": r.created_at.isoformat()
    } for r in rows])

# Create request
@app.route("/api/leaves", methods=["POST"])
#@login_required
def api_leaves_create():
    data = request.json or request.form
    username = session.get("username")
    lt = (data.get("leave_type") or "").strip()
    sd = (data.get("start_date") or "").strip()
    ed = (data.get("end_date") or "").strip()
    reason = (data.get("reason") or "").strip() or None

    if lt not in LEAVE_TYPES:
        return jsonify({"error": "Invalid leave_type"}), 400
    try:
        d1 = datetime.strptime(sd, "%Y-%m-%d").date()
        d2 = datetime.strptime(ed, "%Y-%m-%d").date()
    except:
        return jsonify({"error": "Invalid dates"}), 400

    days = business_days(d1, d2)
    if days <= 0:
        return jsonify({"error": "No business days in range"}), 400

    # Validate balance for paid types (everything except Unpaid)
    if lt != "Unpaid":
        bal = LeaveBalance.query.filter_by(username=username, leave_type=lt).first()
        have = bal.balance_days if bal else 0
        if have < days:
            return jsonify({"error": f"Insufficient {lt} balance", "have": have, "need": days}), 400

    row = LeaveRequest(
        username=username, leave_type=lt, start_date=d1, end_date=d2,
        days=days, reason=reason, status="Pending"
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id}), 201

# Cancel (by requester, while Pending)
@app.route("/api/leaves/<int:leave_id>/cancel", methods=["POST"])
#@login_required
def api_leaves_cancel(leave_id):
    r = LeaveRequest.query.filter_by(id=leave_id).first()
    if not r or r.username != session.get("username"):
        return jsonify({"error": "Not found"}), 404
    if r.status != "Pending":
        return jsonify({"error": "Only pending requests can be cancelled"}), 400
    r.status = "Cancelled"
    db.session.commit()
    return jsonify({"ok": True})

# Decision (Approve/Reject) — HR/Leaders only
@app.route("/api/leaves/<int:leave_id>/decision", methods=["POST"])
#@login_required
def api_leaves_decision(leave_id):
    if not is_approver():
        return jsonify({"error": "Not allowed"}), 403
    data = request.json or request.form
    action = (data.get("action") or "").strip().lower()   # approve|reject
    r = LeaveRequest.query.filter_by(id=leave_id).first()
    if not r:
        return jsonify({"error": "Not found"}), 404
    if r.status != "Pending":
        return jsonify({"error": "Only pending requests can be decided"}), 400

    if action == "approve":
        # deduct balance for paid types
        if r.leave_type != "Unpaid":
            bal = LeaveBalance.query.filter_by(username=r.username, leave_type=r.leave_type).first()
            have = bal.balance_days if bal else 0
            if have < r.days:
                return jsonify({"error": f"Insufficient balance for {r.leave_type}"}), 400
            bal.balance_days = have - r.days
        r.status = "Approved"
        r.approver = session.get("username")
        db.session.commit()
        return jsonify({"ok": True, "status": r.status})

    elif action == "reject":
        r.status = "Rejected"
        r.approver = session.get("username")
        db.session.commit()
        return jsonify({"ok": True, "status": r.status})

    return jsonify({"error": "Invalid action"}), 400

from datetime import date

def _date_or_default(s, fallback):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else fallback
    except:
        return fallback

@app.route("/api/absences", methods=["GET"])
#@login_required
def api_absences():
    """
    Returns Approved leave requests.
    Query params (optional):
      from=YYYY-MM-DD
      to=YYYY-MM-DD
      scope=all   (HR/Leaders only; otherwise restricted to current user)
    """
    scope = (request.args.get("scope") or "").strip()
    username = session.get("username")
    today = date.today()
    d_from = _date_or_default(request.args.get("from"), today)
    d_to   = _date_or_default(request.args.get("to"),   today + timedelta(days=30))

    q = LeaveRequest.query.filter_by(status="Approved")
    if scope == "all" and is_approver():
        pass
    else:
        q = q.filter_by(username=username)

    # Overlap: start <= to AND end >= from
    q = q.filter(LeaveRequest.start_date <= d_to).filter(LeaveRequest.end_date >= d_from)
    rows = q.order_by(LeaveRequest.start_date.asc()).all()

    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "username": r.username,
            "leave_type": r.leave_type,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
            "days": r.days,
            "reason": r.reason,
            "created_at": r.created_at.isoformat()
        })
    return jsonify(out)

# List cycles (all)
@app.route("/api/perf/cycles", methods=["GET"])
#@login_required
def api_perf_cycles_list():
    rows = PerfCycle.query.order_by(PerfCycle.start_date.desc()).all()
    return jsonify([{
        "id": r.id, "name": r.name, "start_date": r.start_date.isoformat(),
        "end_date": r.end_date.isoformat(), "status": r.status
    } for r in rows])

# Create/Update cycle (Leaders/HR)
@app.route("/api/perf/cycles", methods=["POST"])
#@login_required
def api_perf_cycles_save():
    if not is_manager_or_hr():
        return jsonify({"error": "Not allowed"}), 403
    data = request.json or request.form
    cid = data.get("id")
    name = (data.get("name") or "").strip()
    try:
        sd = datetime.strptime(data.get("start_date"), "%Y-%m-%d").date()
        ed = datetime.strptime(data.get("end_date"), "%Y-%m-%d").date()
    except:
        return jsonify({"error": "Invalid dates"}), 400
    if not name: return jsonify({"error": "Name required"}), 400

    if cid:
        row = PerfCycle.query.get(int(cid))
        if not row: return jsonify({"error":"Not found"}), 404
        row.name, row.start_date, row.end_date = name, sd, ed
        if "status" in data and data["status"] in ("Active","Closed"):
            row.status = data["status"]
    else:
        row = PerfCycle(name=name, start_date=sd, end_date=ed, status="Active", created_by=session["username"])
        db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "id": row.id})


# List my goals for a cycle (or all cycles if none). Managers/HR can pass ?user=<username>
@app.route("/api/perf/goals", methods=["GET"])
#@login_required
def api_perf_goals_list():
    target = request.args.get("user") or session.get("username")
    if target != session.get("username") and not is_manager_or_hr():
        return jsonify({"error": "Not allowed"}), 403
    q = Goal.query.filter_by(owner=target)
    if request.args.get("cycle_id"):
        q = q.filter_by(cycle_id=int(request.args.get("cycle_id")))
    rows = q.order_by(Goal.created_at.desc()).all()
    return jsonify([{
        "id": g.id, "cycle_id": g.cycle_id, "cycle_name": g.cycle.name if g.cycle else None,
        "owner": g.owner, "title": g.title, "description": g.description, "weight": g.weight,
        "status": g.status, "progress": g.progress, "updated_at": g.updated_at.isoformat()
    } for g in rows])

# Create / Update goal (owner or manager)
@app.route("/api/perf/goals", methods=["POST"])
#@login_required
def api_perf_goals_save():
    data = request.json or request.form
    gid = data.get("id")
    owner = (data.get("owner") or session.get("username")).strip()
    if owner != session.get("username") and not is_manager_or_hr():
        return jsonify({"error":"Not allowed"}), 403
    cycle_id = int(data.get("cycle_id"))
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip() or None
    weight = max(0, min(100, int(data.get("weight") or 20)))

    if gid:
        g = Goal.query.get(int(gid))
        if not g: return jsonify({"error":"Not found"}), 404
        if g.owner != session.get("username") and not is_manager_or_hr():
            return jsonify({"error":"Not allowed"}), 403
        g.title, g.description, g.weight = title or g.title, description, weight
        if "status" in data and data["status"] in ("Draft","In Progress","Completed","Archived"):
            g.status = data["status"]
    else:
        g = Goal(cycle_id=cycle_id, owner=owner, title=title, description=description, weight=weight, status="Draft")
        db.session.add(g)
    db.session.commit()
    return jsonify({"ok": True, "id": g.id})

# Add progress update (owner or manager)
@app.route("/api/perf/goals/<int:goal_id>/update", methods=["POST"])
#@login_required
def api_perf_goal_update(goal_id):
    g = Goal.query.get(goal_id)
    if not g: return jsonify({"error":"Not found"}), 404
    if g.owner != session.get("username") and not is_manager_or_hr():
        return jsonify({"error":"Not allowed"}), 403
    data = request.json or request.form
    progress = max(0, min(100, int(data.get("progress") or g.progress)))
    note = (data.get("note") or "").strip() or None
    g.progress = progress
    if progress >= 100 and g.status != "Completed":
        g.status = "Completed"
    upd = GoalUpdate(goal_id=goal_id, progress=progress, note=note, created_by=session["username"])
    db.session.add(upd)
    db.session.commit()
    return jsonify({"ok": True})


# List reviews (for me OR all if manager/hr)
@app.route("/api/perf/reviews", methods=["GET"])
#@login_required
def api_perf_reviews_list():
    q = Review.query
    if is_manager_or_hr() and request.args.get("scope") == "all":
        pass
    else:
        # show those where I'm reviewee OR reviewer
        me = session.get("username")
        q = q.filter(db.or_(Review.reviewee==me, Review.reviewer==me))
    if request.args.get("cycle_id"):
        q = q.filter_by(cycle_id=int(request.args.get("cycle_id")))
    rows = q.order_by(Review.created_at.desc()).all()
    return jsonify([{
        "id": r.id, "cycle_id": r.cycle_id, "cycle_name": r.cycle.name if r.cycle else None,
        "reviewee": r.reviewee, "reviewer": r.reviewer, "rating": r.rating,
        "comments": r.comments, "status": r.status
    } for r in rows])

# Create/assign a review (Manager/HR)
@app.route("/api/perf/reviews", methods=["POST"])
#@login_required
def api_perf_reviews_create():
    if not is_manager_or_hr():
        return jsonify({"error":"Not allowed"}), 403
    d = request.json or request.form
    r = Review(
        cycle_id=int(d.get("cycle_id")),
        reviewee=(d.get("reviewee") or "").strip(),
        reviewer=session.get("username") if not d.get("reviewer") else (d.get("reviewer")).strip(),
        status="Open"
    )
    db.session.add(r); db.session.commit()
    return jsonify({"ok": True, "id": r.id})

# Submit/Finalize a review (reviewer)
@app.route("/api/perf/reviews/<int:rid>", methods=["POST"])
#@login_required
def api_perf_reviews_submit(rid):
    d = request.json or request.form
    r = Review.query.get(rid)
    if not r: return jsonify({"error":"Not found"}), 404
    if r.reviewer != session.get("username") and not is_manager_or_hr():
        return jsonify({"error":"Not allowed"}), 403
    if "rating" in d:
        try: r.rating = max(1, min(5, int(d.get("rating"))))
        except: pass
    if "comments" in d:
        r.comments = (d.get("comments") or "").strip() or None
    if "status" in d and d["status"] in ("Submitted","Finalized"):
        r.status = d["status"]
    db.session.commit()
    return jsonify({"ok": True})





@app.route("/api/org/structure", methods=["GET"])
#@login_required
def api_org_structure():
    """
    Get organizational structure data for the org chart
    Returns all users with their reporting relationships
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # Only HR Managers, Leaders, and CXOs can access org chart
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    # Get all users with their manager relationships
    users = User.query.order_by(User.employee_type.desc(), User.subposition, User.name).all()
    
    result = []
    for user in users:
        # Get manager information if exists
        manager_info = None
        if hasattr(user, 'manager_id') and user.manager_id:
            manager = User.query.get(user.manager_id)
            if manager:
                manager_info = {
                    "id": manager.id,
                    "name": manager.name,
                    "employee_type": manager.employee_type,
                    "subposition": manager.subposition
                }
        
        # Count direct reports
        direct_reports_count = 0
        if hasattr(user, 'direct_reports'):
            direct_reports_count = len(user.direct_reports)
        else:
            # Fallback if relationship not set up
            direct_reports_count = User.query.filter_by(manager_id=user.id).count() if hasattr(User, 'manager_id') else 0
        
        result.append({
            "id": user.id,
            "employee_id": user.employee_id,
            "username": user.username,
            "name": user.name,
            "email": user.email,
            "employee_type": user.employee_type,
            "subposition": user.subposition,
            "designation": user.designation,
            "manager_id": getattr(user, 'manager_id', None),
            "manager": manager_info,
            "direct_reports_count": direct_reports_count,
            "created_at": user.created_at.isoformat() if user.created_at else None
        })
    
    return jsonify(result)


@app.route("/api/org/update-reporting", methods=["POST"])
#@login_required
def api_org_update_reporting():
    """
    Update reporting relationship between employees
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # Only HR Managers can update org structure
    if not (employee_type == 'HR' and subposition in ('Managers', 'Executives')):
        return jsonify({"error": "Not authorized"}), 403
    
    data = request.json or {}
    employee_id = data.get('employee_id')
    manager_id = data.get('manager_id')  # Can be None for top-level employees
    
    if not employee_id:
        return jsonify({"error": "Employee ID is required"}), 400
    
    # Get the employee
    employee = User.query.get(employee_id)
    if not employee:
        return jsonify({"error": "Employee not found"}), 404
    
    # Validate manager if provided
    if manager_id:
        manager = User.query.get(manager_id)
        if not manager:
            return jsonify({"error": "Manager not found"}), 404
        
        # Prevent circular reporting (employee can't report to themselves or their subordinates)
        if manager_id == employee_id:
            return jsonify({"error": "Employee cannot report to themselves"}), 400
        
        # Check for circular reference (simplified check)
        if would_create_circular_reference(employee_id, manager_id):
            return jsonify({"error": "This change would create a circular reporting structure"}), 400
    
    # Update the reporting relationship
    if hasattr(employee, 'manager_id'):
        employee.manager_id = manager_id
    else:
        # If the field doesn't exist yet, we'll store it in a separate table
        # For now, we'll add a note that the database needs to be updated
        return jsonify({"error": "Database schema needs to be updated to support org chart"}), 500
    
    try:
        db.session.commit()
        return jsonify({"success": True, "message": "Reporting structure updated successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Failed to update reporting structure"}), 500
    


def would_create_circular_reference(employee_id, manager_id):
    """
    Check if setting manager_id as manager of employee_id would create a circular reference
    """
    if not hasattr(User, 'manager_id'):
        return False  # Can't check without the field
    
    # Simple check: traverse up the manager chain from the proposed manager
    current_manager_id = manager_id
    visited = set()
    
    while current_manager_id and current_manager_id not in visited:
        if current_manager_id == employee_id:
            return True  # Circular reference found
        
        visited.add(current_manager_id)
        manager = User.query.get(current_manager_id)
        current_manager_id = getattr(manager, 'manager_id', None) if manager else None
    
    return False


@app.route("/api/org/hierarchy", methods=["GET"])
#@login_required
def api_org_hierarchy():
    """
    Get hierarchical organization structure (tree format)
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    # Get all users
    users = User.query.all()
    
    # Build hierarchy
    user_dict = {user.id: {
        "id": user.id,
        "name": user.name,
        "employee_type": user.employee_type,
        "subposition": user.subposition,
        "designation": user.designation,
        "email": user.email,
        "manager_id": getattr(user, 'manager_id', None),
        "children": []
    } for user in users}
    
    # Build tree structure
    root_nodes = []
    for user_data in user_dict.values():
        manager_id = user_data.get('manager_id')
        if manager_id and manager_id in user_dict:
            user_dict[manager_id]['children'].append(user_data)
        else:
            root_nodes.append(user_data)
    
    return jsonify(root_nodes)


@app.route("/api/org/departments", methods=["GET"])
#@login_required
def api_org_departments():
    """
    Get organization structure grouped by departments
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    users = User.query.all()
    
    # Group by department (using subposition as department)
    departments = {}
    for user in users:
        dept = user.subposition or 'Unassigned'
        if dept not in departments:
            departments[dept] = {
                "name": dept,
                "employees": [],
                "total_count": 0,
                "managers": [],
                "individual_contributors": []
            }
        
        user_data = {
            "id": user.id,
            "name": user.name,
            "employee_type": user.employee_type,
            "designation": user.designation,
            "email": user.email,
            "manager_id": getattr(user, 'manager_id', None)
        }
        
        departments[dept]["employees"].append(user_data)
        departments[dept]["total_count"] += 1
        
        # Check if this user is a manager (has direct reports)
        has_reports = any(getattr(other, 'manager_id', None) == user.id for other in users)
        if has_reports:
            departments[dept]["managers"].append(user_data)
        else:
            departments[dept]["individual_contributors"].append(user_data)
    
    return jsonify(list(departments.values()))


@app.route("/api/org/stats", methods=["GET"])
#@login_required
def api_org_stats():
    """
    Get organizational statistics
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    users = User.query.all()
    total_employees = len(users)
    
    # Count leadership positions
    leadership_roles = ['Managers', 'Executives', 'CEO', 'CHRO', 'CFO', 'CTO/COO', 'Team Leads', 'Department Managers']
    leadership_count = len([u for u in users if u.subposition in leadership_roles])
    
    # Count departments
    departments = set(user.subposition for user in users if user.subposition)
    department_count = len(departments)
    
    # Calculate average team size
    managers_with_reports = []
    for user in users:
        reports_count = sum(1 for other in users if getattr(other, 'manager_id', None) == user.id)
        if reports_count > 0:
            managers_with_reports.append(reports_count)
    
    avg_team_size = round(sum(managers_with_reports) / len(managers_with_reports)) if managers_with_reports else 0
    
    return jsonify({
        "total_employees": total_employees,
        "leadership_count": leadership_count,
        "department_count": department_count,
        "avg_team_size": avg_team_size,
        "managers_count": len(managers_with_reports),
        "individual_contributors": total_employees - len(managers_with_reports)
    })


@app.route("/api/org/setup-db", methods=["POST"])
#@login_required
def api_org_setup_db():
    """
    Helper route to add the manager_id field to User table
    Only run this once to set up the organizational structure
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # Only HR Managers can run this
    if not (employee_type == 'HR' and subposition in ('Managers', 'Executives')):
        return jsonify({"error": "Not authorized"}), 403
    
    try:
        # Check if the column already exists
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('user')]
        
        if 'manager_id' not in columns:
            # Add the manager_id column
            db.engine.execute('ALTER TABLE user ADD COLUMN manager_id INTEGER')
            db.engine.execute('ALTER TABLE user ADD FOREIGN KEY (manager_id) REFERENCES user(id)')
            
            return jsonify({"success": True, "message": "Database updated successfully. Restart your application to use org chart features."})
        else:
            return jsonify({"success": True, "message": "Database already has org chart support."})
            
    except Exception as e:
        return jsonify({"error": f"Failed to update database: {str(e)}"}), 500
    

@app.route("/api/org/structure-mock", methods=["GET"])
#@login_required
def api_org_structure_mock():
    """
    Temporary endpoint that creates a mock org structure without requiring database changes
    This generates a logical hierarchy based on employee_type and subposition
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    users = User.query.all()
    
    # Create a logical hierarchy based on roles
    hierarchy_rules = {
        # CXO level (top level)
        ('CXOs', 'CEO'): {'level': 1, 'reports_to': None},
        ('CXOs', 'CHRO'): {'level': 2, 'reports_to': ('CXOs', 'CEO')},
        ('CXOs', 'CFO'): {'level': 2, 'reports_to': ('CXOs', 'CEO')},
        ('CXOs', 'CTO/COO'): {'level': 2, 'reports_to': ('CXOs', 'CEO')},
        
        # Department heads
        ('HR', 'Managers'): {'level': 3, 'reports_to': ('CXOs', 'CHRO')},
        ('HR', 'Executives'): {'level': 3, 'reports_to': ('CXOs', 'CHRO')},
        ('Leaders', 'Department Managers'): {'level': 3, 'reports_to': ('CXOs', 'CEO')},
        
        # Team level
        ('Leaders', 'Team Leads'): {'level': 4, 'reports_to': ('Leaders', 'Department Managers')},
        ('HR', 'Recruiters'): {'level': 4, 'reports_to': ('HR', 'Managers')},
        
        # Individual contributors
        ('Employees', 'Existing'): {'level': 5, 'reports_to': ('Leaders', 'Team Leads')},
        ('Employees', 'New'): {'level': 5, 'reports_to': ('Leaders', 'Team Leads')},
    }
    
    result = []
    for user in users:
        role_key = (user.employee_type, user.subposition)
        role_info = hierarchy_rules.get(role_key, {'level': 6, 'reports_to': None})
        
        # Find logical manager
        manager_id = None
        manager_info = None
        
        if role_info.get('reports_to'):
            manager_type, manager_subpos = role_info['reports_to']
            potential_manager = User.query.filter_by(
                employee_type=manager_type,
                subposition=manager_subpos
            ).first()
            
            if potential_manager:
                manager_id = potential_manager.id
                manager_info = {
                    "id": potential_manager.id,
                    "name": potential_manager.name,
                    "employee_type": potential_manager.employee_type,
                    "subposition": potential_manager.subposition
                }
        
        # Count potential direct reports
        direct_reports_count = 0
        for other_user in users:
            other_role_key = (other_user.employee_type, other_user.subposition)
            other_role_info = hierarchy_rules.get(other_role_key, {'reports_to': None})
            
            if other_role_info.get('reports_to') == role_key:
                direct_reports_count += 1
        
        result.append({
            "id": user.id,
            "employee_id": user.employee_id,
            "username": user.username,
            "name": user.name,
            "email": user.email,
            "employee_type": user.employee_type,
            "subposition": user.subposition,
            "designation": user.designation,
            "manager_id": manager_id,
            "manager": manager_info,
            "direct_reports_count": direct_reports_count,
            "hierarchy_level": role_info['level'],
            "created_at": user.created_at.isoformat() if user.created_at else None
        })
    
    return jsonify(result)

def get_user_location(user):
    """
    Get user location from various possible sources in order of preference
    """
    # 1. Check if User model has a direct location field
    if hasattr(user, 'location') and user.location:
        return user.location.strip()
    
    # 2. Check onboarding data for address
    try:
        onboarding_data = load_onboarding(user.username)
        if onboarding_data and onboarding_data.get('personal', {}).get('address'):
            address = onboarding_data['personal']['address'].strip()
            if address:
                return parse_location_from_address(address)
    except:
        pass
    
    # 3. Check if there's a separate location table/relationship
    # (This would be if you have an EmployeeLocation model)
    
    # 4. Infer from email domain (if company uses location-based emails)
    location_from_email = infer_location_from_email(user.email)
    if location_from_email:
        return location_from_email
    
    # 5. Default fallback
    return "Not specified"


@app.route("/api/workforce/locations", methods=["GET"])
#@login_required
def api_workforce_locations():
    """
    Get workforce location data for the world map
    Uses actual user-provided locations from various sources
    """
    employee_type = session.get('employee_type')
    subposition = session.get('subposition')
    
    # Only HR Managers, Leaders, and CXOs can access workforce locations
    if not ((employee_type == 'HR' and subposition in ('Managers', 'Executives')) or 
            (employee_type in ('Leaders', 'CXOs'))):
        return jsonify({"error": "Not authorized"}), 403
    
    users = User.query.all()
    result = []
    
    for user in users:
        # Get location from multiple possible sources
        user_location = get_user_location(user)
        work_type = get_user_work_type(user, user_location)
        timezone = get_timezone_from_location(user_location)
        
        result.append({
            "id": user.id,
            "employee_id": user.employee_id,
            "username": user.username,
            "name": user.name,
            "email": user.email,
            "employee_type": user.employee_type,
            "subposition": user.subposition,
            "designation": user.designation,
            "location": user_location,
            "work_type": work_type,
            "timezone": timezone,
            "created_at": user.created_at.isoformat() if user.created_at else None
        })
    
    return jsonify(result)

@app.route("/run_duplicates")
def run_duplicates():
    try:
        # Run duplicates.py from the same directory
        subprocess.run(["python3", "duplicates.py"], check=True)
        flash("Bulk upload completed successfully!", "success")
    except subprocess.CalledProcessError:
        flash("Error while running duplicates.py", "error")

    return redirect(url_for("dashboard"))


@app.route('/logout')
#@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# Initialize DB on first run
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)