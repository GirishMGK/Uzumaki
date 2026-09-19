"""Deterministic demo dataset per §14: 6 offices, 10 departments, 14 partners,
300 staff (150 articles), 200 clients / 25 groups, 380 engagements spanning
FY2025-26 and FY2026-27, 2 years of holidays, and a spread of leave records.

Run explicitly — never automatically (see app/jobs/startup_seed.py for what
*does* run automatically, which is just an admin login + app_config
defaults):

    cd backend && python -m seed.seed_data

Idempotent-ish: re-running against an already-seeded DB will raise on the
unique constraints (employee_code/client_code/etc.) rather than duplicate
silently, so you'll know if you're about to double-seed.
"""
import random
from datetime import date, timedelta

from sqlmodel import Session, select

from app.core.security import hash_password
from app.db.session import engine, init_db
from app.models.allocation import Allocation, HolidayCalendar, NonAvailability
from app.services.capacity_materializer import recompute_range
from app.models.client import Client, ClientGroup
from app.models.engagement import Engagement
from app.models.enums import (
    AllocationRole,
    AllocationStatus,
    Designation,
    EngagementStatus,
    EntityClass,
    RelationshipStatus,
    RiskRating,
    ServiceType,
    StaffCategory,
    UserRole,
)
from app.models.reference import Department, Grade, Office, Skill
from app.models.staff import Staff
from app.models.user import User

random.seed(42)

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan",
    "Ananya", "Diya", "Saanvi", "Aadhya", "Kiara", "Myra", "Anika", "Navya", "Riya", "Ira",
    "Rahul", "Suresh", "Ramesh", "Venkatesh", "Prakash", "Anil", "Sunil", "Deepak", "Vijay", "Kiran",
    "Priya", "Lakshmi", "Sunita", "Meena", "Kavita", "Pooja", "Neha", "Swati", "Divya", "Shreya",
]
LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Reddy", "Rao", "Iyer", "Nair", "Menon", "Pillai", "Chatterjee",
    "Mukherjee", "Banerjee", "Agarwal", "Bansal", "Jain", "Shah", "Mehta", "Desai", "Patel", "Kulkarni",
    "Joshi", "Deshpande", "Chowdhury", "Das", "Roy", "Sen", "Bose", "Krishnan", "Subramaniam", "Murthy",
]

OFFICES = [
    ("HYD", "Hyderabad HO", "Hyderabad", "Telangana", True),
    ("BLR", "Bengaluru", "Bengaluru", "Karnataka", False),
    ("CHN", "Chennai", "Chennai", "Tamil Nadu", False),
    ("MUM", "Mumbai", "Mumbai", "Maharashtra", False),
    ("DEL", "New Delhi", "New Delhi", "Delhi", False),
    ("VIZ", "Visakhapatnam", "Visakhapatnam", "Andhra Pradesh", False),
]

DEPARTMENTS = [
    ("STAT", "Statutory Audit"), ("IA", "Internal Audit"), ("TAX", "Direct Tax & Tax Audit"),
    ("IDT", "Indirect Tax"), ("RA", "Risk Advisory / IFC"), ("FDD", "Transaction Advisory & Due Diligence"),
    ("FOR", "Forensic"), ("IPO", "Capital Markets"), ("CONS", "Consolidation & Reporting"),
    ("SUP", "Support (non-chargeable)"),
]

SKILLS = [
    ("INDAS109", "Ind AS 109 / ECL", "TECHNICAL"), ("INDAS115", "Ind AS 115", "TECHNICAL"),
    ("INDAS116", "Ind AS 116", "TECHNICAL"), ("IFRS", "IFRS", "TECHNICAL"),
    ("CARO2020", "CARO 2020", "REGULATORY"), ("IFC", "IFC/ICFR", "TECHNICAL"),
    ("SA600", "SA 600 group audits", "TECHNICAL"), ("NBFC_SBR", "NBFC-SBR & RBI Master Directions", "REGULATORY"),
    ("BANK_BR", "Bank branch audit", "INDUSTRY"), ("IRDAI", "Insurance (IRDAI)", "REGULATORY"),
    ("IPO_ICDR", "IPO/ICDR", "REGULATORY"), ("FORENSIC", "Forensic (SA 240)", "TECHNICAL"),
    ("TP", "Transfer Pricing", "REGULATORY"), ("GST", "GST", "REGULATORY"),
    ("IDEA_ACL", "IDEA/ACL", "TOOL"), ("SAP_FICO", "SAP FICO", "TOOL"), ("ORACLE", "Oracle", "TOOL"),
    ("TALLY", "Tally", "TOOL"), ("POWERBI", "Power BI", "TOOL"), ("PYTHON", "Python analytics", "TOOL"),
]

DESIGNATION_LADDER = [
    (Designation.PARTNER, StaffCategory.PARTNER, 2),
    (Designation.SENIOR_MANAGER, StaffCategory.EMPLOYEE_CA, 4),
    (Designation.MANAGER, StaffCategory.EMPLOYEE_CA, 5),
    (Designation.ASSISTANT_MANAGER, StaffCategory.EMPLOYEE_CA, 6),
    (Designation.SENIOR_ASSOCIATE, StaffCategory.EMPLOYEE_SEMI_QUALIFIED, 7),
    (Designation.ASSOCIATE, StaffCategory.EMPLOYEE_SEMI_QUALIFIED, 8),
]

FY_LIST = ["FY2025-26", "FY2026-27"]


def _random_name() -> tuple[str, str]:
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)


def seed(session: Session, *, capacity_window: tuple[date, date] | None = None) -> None:
    """`capacity_window`, if given, materialises capacity_daily (§5) for that
    date range after seeding — see the note near the bottom of this function.
    Left `None` by default because it's expensive (a couple of seconds per
    90-day window x 300 staff) and most callers of `seed()` (tests checking
    row counts, import behaviour, etc.) don't touch capacity_daily at all;
    the CLI entrypoint below passes a window so a freshly-seeded dev
    instance has real utilisation/bench data to look at immediately.
    """
    offices = []
    for code, name, city, state, is_ho in OFFICES:
        o = Office(code=code, name=name, city=city, state=state, is_head_office=is_ho)
        session.add(o)
        offices.append(o)
    session.commit()

    departments = []
    for code, name in DEPARTMENTS:
        d = Department(code=code, name=name)
        session.add(d)
        departments.append(d)
    session.commit()

    for designation in Designation:
        from app.models.enums import DESIGNATION_GRADE_RANK, DEFAULT_CHARGEABILITY_TARGET_PCT

        session.add(
            Grade(
                designation=designation,
                grade_rank=DESIGNATION_GRADE_RANK[designation],
                default_chargeability_target_pct=DEFAULT_CHARGEABILITY_TARGET_PCT[designation],
            )
        )
    session.commit()

    for code, name, category in SKILLS:
        session.add(Skill(code=code, name=name, category=category))
    session.commit()

    # --- Staff: 14 partners, then fill to 300 with ~150 articles + the rest employees ---
    staff_rows: list[Staff] = []
    for i in range(14):
        fn, ln = _random_name()
        office = offices[i % len(offices)]
        dept = departments[i % len(departments)]
        s = Staff(
            employee_code=f"P{i+1:03d}",
            full_name=f"{fn} {ln}",
            staff_category=StaffCategory.PARTNER,
            designation=Designation.MANAGING_PARTNER if i == 0 else Designation.PARTNER,
            grade_rank=1 if i == 0 else 2,
            icai_membership_no=f"{100000 + i}",
            base_office_id=office.id,
            current_office_id=office.id,
            primary_department_id=dept.id,
            date_of_joining=(date(2010, 1, 1) + timedelta(days=i * 90)).isoformat(),
            chargeability_target_pct=30 if i == 0 else 45,
        )
        session.add(s)
        staff_rows.append(s)
    session.commit()
    partners = staff_rows[:]

    n_qualified_target = 136  # employees (CA/semi-qual/support); 14 partners + 136 + 150 articles = 300
    for i in range(n_qualified_target):
        fn, ln = _random_name()
        office = offices[i % len(offices)]
        dept = departments[i % len(departments)]
        designation, category, grade_rank = DESIGNATION_LADDER[1 + i % (len(DESIGNATION_LADDER) - 1)]
        s = Staff(
            employee_code=f"E{i+1:04d}",
            full_name=f"{fn} {ln}",
            staff_category=category,
            designation=designation,
            grade_rank=grade_rank,
            base_office_id=office.id,
            current_office_id=office.id,
            primary_department_id=dept.id,
            reporting_manager_id=partners[i % len(partners)].id,
            date_of_joining=(date(2018, 6, 1) + timedelta(days=i * 20)).isoformat(),
            chargeability_target_pct=[65, 75, 80, 85, 85][min(4, max(0, grade_rank - 4))],
        )
        session.add(s)
        staff_rows.append(s)
    session.commit()

    n_articles = 150
    for i in range(n_articles):
        fn, ln = _random_name()
        office = offices[i % len(offices)]
        dept = departments[i % len(departments)]
        year = 1 + i % 3
        designation = {1: Designation.ARTICLE_Y1, 2: Designation.ARTICLE_Y2, 3: Designation.ARTICLE_Y3}[year]
        principal = partners[i % len(partners)]
        start = date(2024, 8, 1) + timedelta(days=i * 5)
        s = Staff(
            employee_code=f"A{i+1:04d}",
            full_name=f"{fn} {ln}",
            staff_category=StaffCategory.ARTICLED_ASSISTANT,
            designation=designation,
            grade_rank={1: 12, 2: 11, 3: 10}[year],
            base_office_id=office.id,
            current_office_id=office.id,
            primary_department_id=dept.id,
            reporting_manager_id=principal.id,
            articleship_principal_id=principal.id,
            articleship_start_date=start.isoformat(),
            articleship_end_date=(start + timedelta(days=3 * 365)).isoformat(),
            date_of_joining=start.isoformat(),
            standard_hours_per_week=35,
            chargeability_target_pct=90,
            leave_entitlement_days=round((365 * year) / 6, 1),
        )
        session.add(s)
        staff_rows.append(s)
    session.commit()

    qualified_and_articles = staff_rows[14:]
    manager_grade_or_above = [s for s in staff_rows if s.grade_rank <= 6]

    # --- Client groups + clients ---
    groups = []
    for i in range(25):
        g = ClientGroup(group_code=f"GRP-{i+1:03d}", group_name=f"{random.choice(LAST_NAMES)} Group",
                         group_relationship_partner_id=partners[i % len(partners)].id)
        session.add(g)
        groups.append(g)
    session.commit()

    sectors = ["BFSI", "Manufacturing", "IT", "Pharma", "Realty", "PSU", "Other"]
    entity_classes = list(EntityClass)
    clients = []
    for i in range(200):
        group = groups[i % len(groups)] if i % 3 != 0 else None
        c = Client(
            client_code=f"CL-{i+1:04d}",
            name=f"{random.choice(LAST_NAMES)} {random.choice(['Industries', 'Enterprises', 'Ltd', 'Pvt Ltd', 'Group'])}",
            entity_class=random.choice(entity_classes),
            is_pie=(i % 11 == 0),
            sector=sectors[i % len(sectors)],
            relationship_status=RelationshipStatus.ACTIVE,
            risk_rating=random.choice(list(RiskRating)),
            group_id=group.id if group else None,
            relationship_partner_id=partners[i % len(partners)].id,
        )
        session.add(c)
        clients.append(c)
    session.commit()

    # --- Engagements ---
    service_types = list(ServiceType)
    engagements = []
    for i in range(380):
        client = clients[i % len(clients)]
        dept = departments[i % len(departments)]
        fy = FY_LIST[i % 2]
        ep = partners[i % len(partners)]
        fy_start = date(2026, 4, 1) if fy == "FY2025-26" else date(2027, 4, 1)
        # Stagger start dates across the year and vary duration so allocations
        # don't all pile onto one identical 6-month window (that produced an
        # unrealistically dense, all-staff-double-booked board when every
        # engagement shared the same planned_start/planned_end).
        p_start = fy_start + timedelta(days=(i * 11) % 300)
        p_end = p_start + timedelta(days=random.randrange(30, 120))
        e = Engagement(
            engagement_code=f"{dept.code}/{client.client_code}/{fy.replace('FY', '')}-{i+1:04d}",
            client_id=client.id,
            department_id=dept.id,
            service_type=service_types[i % len(service_types)],
            financial_year=fy,
            engagement_partner_id=ep.id,
            signing_partner_id=ep.id,
            engagement_manager_id=manager_grade_or_above[i % len(manager_grade_or_above)].id,
            eqcr_required=client.is_pie,
            fee_amount=float(random.randrange(2, 80)) * 100000,
            fee_type="FIXED",
            budget_hours_total=float(random.randrange(80, 1200)),
            reporting_deadline=(date(2026, 9, 30) if fy == "FY2025-26" else date(2027, 9, 30)).isoformat(),
            status=random.choice(list(EngagementStatus)),
            planned_start=p_start.isoformat(),
            planned_end=p_end.isoformat(),
        )
        session.add(e)
        engagements.append(e)
    session.commit()

    # --- Holidays: 2 years, firm-wide ---
    holiday_names = ["Republic Day", "Holi", "Good Friday", "Independence Day", "Ganesh Chaturthi", "Gandhi Jayanti", "Diwali", "Christmas"]
    for year in (2025, 2026):
        for month_offset, name in enumerate(holiday_names):
            d = date(year, 1, 1) + timedelta(days=month_offset * 45 + 10)
            session.add(HolidayCalendar(office_id=None, holiday_date=d.isoformat(), name=f"{name} {year}"))
    session.commit()

    # --- Leave: a spread of approved leave across ~1/3 of qualified+article staff ---
    for i, s in enumerate(qualified_and_articles):
        if i % 3 != 0:
            continue
        start = date(2026, 4, 1) + timedelta(days=(i * 7) % 150)
        session.add(
            NonAvailability(
                staff_id=s.id, type="PRIVILEGE_LEAVE", date_from=start.isoformat(),
                date_to=(start + timedelta(days=1)).isoformat(), status="APPROVED",
            )
        )
    session.commit()

    # --- A sample of confirmed allocations so dashboards/reports have data to show ---
    for i, e in enumerate(engagements[:200]):
        team = random.sample(qualified_and_articles, k=min(4, len(qualified_and_articles)))
        for j, member in enumerate(team):
            role = AllocationRole.FIELD_INCHARGE if j == 0 else (
                AllocationRole.ARTICLE if member.staff_category == StaffCategory.ARTICLED_ASSISTANT else AllocationRole.TEAM_MEMBER
            )
            session.add(
                Allocation(
                    engagement_id=e.id, staff_id=member.id, role_on_engagement=role,
                    department_id=e.department_id, partner_id=e.engagement_partner_id,
                    date_from=e.planned_start, date_to=e.planned_end,
                    allocation_pct=50 if j % 2 else 100,
                    status=AllocationStatus.CONFIRMED,
                )
            )
    session.commit()

    # --- Login users for a handful of roles, for demo purposes ---
    demo_users = [
        ("admin@firm.local", UserRole.ADMIN, None),
        ("rm@firm.local", UserRole.RESOURCE_MANAGER, None),
        ("hr@firm.local", UserRole.HR, None),
        (f"{partners[0].employee_code.lower()}@firm.local", UserRole.PARTNER, partners[0].id),
        (f"{qualified_and_articles[0].employee_code.lower()}@firm.local", UserRole.MANAGER, qualified_and_articles[0].id),
    ]
    for email, role, staff_id in demo_users:
        # Skip rather than collide if app/jobs/startup_seed.py already created admin@firm.local
        # (or this script is re-run) — see the module docstring's "idempotent-ish" note.
        if session.exec(select(User).where(User.email == email)).first():
            continue
        session.add(User(email=email, hashed_password=hash_password("Demo@2026"), role=role, full_name=email.split("@")[0], staff_id=staff_id))
    session.commit()

    # --- capacity_daily (§5), only if the caller asked for it ---
    # Normal API traffic keeps this materialised via the synchronous
    # invalidation hook on every allocation/leave mutation (see
    # app/api/v1/allocations.py, non_availability.py) plus the nightly job —
    # neither of those fire for allocations inserted directly via
    # session.add() like this script does, so RP-03/RP-06 and anything else
    # reading capacity_daily would see an empty table without an explicit
    # build for at least the window the caller cares about.
    if capacity_window:
        rows_written = recompute_range(session, capacity_window[0], capacity_window[1])
        print(f"Materialised capacity_daily: {rows_written} rows.")

    print(f"Seeded: {len(offices)} offices, {len(departments)} departments, {len(staff_rows)} staff "
          f"({len(partners)} partners, {n_articles} articles), {len(groups)} groups, {len(clients)} clients, "
          f"{len(engagements)} engagements.")


if __name__ == "__main__":
    init_db()
    with Session(engine) as s:
        # A 6-month window around "today" is enough for the scheduler's
        # 8-week default view, the dashboards' 90-day default filter, and
        # RP-03/RP-06 to all show real numbers immediately after seeding,
        # without paying for materialising both full FYs (~240k rows,
        # over a minute) on every fresh seed.
        today = date.today()
        s_window = (today - timedelta(days=120), today + timedelta(days=60))
        seed(s, capacity_window=s_window)
