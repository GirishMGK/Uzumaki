"""Enumerations shared across models, schemas and the conflict engine."""
from enum import Enum


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    RESOURCE_MANAGER = "RESOURCE_MANAGER"
    PARTNER = "PARTNER"
    MANAGER = "MANAGER"
    STAFF = "STAFF"
    HR = "HR"
    VIEWER = "VIEWER"


# Roles allowed to see fee / cost-rate / margin fields. Everyone else gets
# those columns masked to null by the response schema, not a 403.
FINANCIALLY_PRIVILEGED_ROLES = {UserRole.ADMIN, UserRole.RESOURCE_MANAGER, UserRole.PARTNER}


class StaffCategory(str, Enum):
    PARTNER = "PARTNER"
    DIRECTOR = "DIRECTOR"
    EMPLOYEE_CA = "EMPLOYEE_CA"
    EMPLOYEE_OTHER_PROF = "EMPLOYEE_OTHER_PROF"
    EMPLOYEE_SEMI_QUALIFIED = "EMPLOYEE_SEMI_QUALIFIED"
    ARTICLED_ASSISTANT = "ARTICLED_ASSISTANT"
    INDUSTRIAL_TRAINEE = "INDUSTRIAL_TRAINEE"
    PAID_ASSISTANT = "PAID_ASSISTANT"
    SUPPORT_STAFF = "SUPPORT_STAFF"
    CONSULTANT_EXTERNAL = "CONSULTANT_EXTERNAL"


class Designation(str, Enum):
    MANAGING_PARTNER = "MANAGING_PARTNER"
    PARTNER = "PARTNER"
    ASSOCIATE_PARTNER = "ASSOCIATE_PARTNER"
    DIRECTOR = "DIRECTOR"
    SENIOR_MANAGER = "SENIOR_MANAGER"
    MANAGER = "MANAGER"
    ASSISTANT_MANAGER = "ASSISTANT_MANAGER"
    SENIOR_ASSOCIATE = "SENIOR_ASSOCIATE"
    ASSOCIATE = "ASSOCIATE"
    ARTICLE_Y1 = "ARTICLE_Y1"
    ARTICLE_Y2 = "ARTICLE_Y2"
    ARTICLE_Y3 = "ARTICLE_Y3"
    TRAINEE = "TRAINEE"
    EXECUTIVE = "EXECUTIVE"


# grade_rank: 1 = most senior. Drives seniority-mix / supervision rules (R9).
DESIGNATION_GRADE_RANK: dict[Designation, int] = {
    Designation.MANAGING_PARTNER: 1,
    Designation.PARTNER: 2,
    Designation.ASSOCIATE_PARTNER: 3,
    Designation.DIRECTOR: 3,
    Designation.SENIOR_MANAGER: 4,
    Designation.MANAGER: 5,
    Designation.ASSISTANT_MANAGER: 6,
    Designation.SENIOR_ASSOCIATE: 7,
    Designation.ASSOCIATE: 8,
    Designation.EXECUTIVE: 9,
    Designation.TRAINEE: 10,
    Designation.ARTICLE_Y3: 10,
    Designation.ARTICLE_Y2: 11,
    Designation.ARTICLE_Y1: 12,
}

# Default chargeability target % by grade_rank band — seeded into `grades`
# reference table (editable there without redeploy). See docs/decisions.md.
DEFAULT_CHARGEABILITY_TARGET_PCT: dict[Designation, float] = {
    Designation.MANAGING_PARTNER: 30,
    Designation.PARTNER: 45,
    Designation.ASSOCIATE_PARTNER: 50,
    Designation.DIRECTOR: 50,
    Designation.SENIOR_MANAGER: 65,
    Designation.MANAGER: 75,
    Designation.ASSISTANT_MANAGER: 80,
    Designation.SENIOR_ASSOCIATE: 85,
    Designation.ASSOCIATE: 85,
    Designation.EXECUTIVE: 85,
    Designation.TRAINEE: 90,
    Designation.ARTICLE_Y1: 90,
    Designation.ARTICLE_Y2: 90,
    Designation.ARTICLE_Y3: 90,
}

# Minimum grade_rank (i.e. maximum seniority number) considered "qualified
# supervisor" for R9 (SA 220 / SQC1 supervision of articles).
QUALIFIED_SUPERVISOR_MAX_GRADE_RANK = DESIGNATION_GRADE_RANK[Designation.ASSISTANT_MANAGER]


class EmploymentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    NOTICE = "NOTICE"
    EXITED = "EXITED"
    ON_LEAVE = "ON_LEAVE"
    SABBATICAL = "SABBATICAL"


class EmploymentType(str, Enum):
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"
    CONTRACT = "CONTRACT"
    SECONDED_IN = "SECONDED_IN"
    SECONDED_OUT = "SECONDED_OUT"


class ServiceType(str, Enum):
    STATUTORY_AUDIT = "STATUTORY_AUDIT"
    LIMITED_REVIEW = "LIMITED_REVIEW"
    CONSOLIDATION = "CONSOLIDATION"
    INTERNAL_AUDIT = "INTERNAL_AUDIT"
    IFC_TESTING = "IFC_TESTING"
    TAX_AUDIT = "TAX_AUDIT"
    TRANSFER_PRICING = "TRANSFER_PRICING"
    GST_AUDIT = "GST_AUDIT"
    CERTIFICATION = "CERTIFICATION"
    FDD_BUY_SIDE = "FDD_BUY_SIDE"
    FDD_SELL_SIDE = "FDD_SELL_SIDE"
    FORENSIC = "FORENSIC"
    IPO_ADVISORY = "IPO_ADVISORY"
    ICFR_DESIGN = "ICFR_DESIGN"
    STOCK_AUDIT = "STOCK_AUDIT"
    CONCURRENT_AUDIT = "CONCURRENT_AUDIT"
    BRANCH_AUDIT = "BRANCH_AUDIT"
    OTHER = "OTHER"


class PeriodType(str, Enum):
    ANNUAL = "ANNUAL"
    HALF_YEARLY = "HALF_YEARLY"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"
    ONE_OFF = "ONE_OFF"


class Priority(str, Enum):
    P1_CRITICAL = "P1_CRITICAL"
    P2_HIGH = "P2_HIGH"
    P3_NORMAL = "P3_NORMAL"
    P4_LOW = "P4_LOW"


class Complexity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FeeType(str, Enum):
    FIXED = "FIXED"
    HOURLY = "HOURLY"
    RETAINER = "RETAINER"
    MILESTONE = "MILESTONE"


class EngagementStatus(str, Enum):
    PIPELINE = "PIPELINE"
    WON = "WON"
    PLANNING = "PLANNING"
    FIELDWORK = "FIELDWORK"
    REVIEW = "REVIEW"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"
    LOST = "LOST"


class EntityClass(str, Enum):
    LISTED = "LISTED"
    UNLISTED_PUBLIC = "UNLISTED_PUBLIC"
    PRIVATE = "PRIVATE"
    LLP = "LLP"
    BANK = "BANK"
    NBFC = "NBFC"
    INSURANCE = "INSURANCE"
    GOVT_COMPANY = "GOVT_COMPANY"
    TRUST = "TRUST"
    FOREIGN_SUB = "FOREIGN_SUB"


class RelationshipStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PROSPECT = "PROSPECT"
    DORMANT = "DORMANT"
    EXITED = "EXITED"


class RiskRating(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    SIGNIFICANT = "SIGNIFICANT"


class AcceptanceStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"


class AllocationRole(str, Enum):
    SIGNING_PARTNER = "SIGNING_PARTNER"
    ENGAGEMENT_PARTNER = "ENGAGEMENT_PARTNER"
    EQCR = "EQCR"
    TECHNICAL_REVIEWER = "TECHNICAL_REVIEWER"
    SPECIALIST = "SPECIALIST"
    ENGAGEMENT_MANAGER = "ENGAGEMENT_MANAGER"
    REPORTING_MANAGER = "REPORTING_MANAGER"
    FIELD_INCHARGE = "FIELD_INCHARGE"
    TEAM_MEMBER = "TEAM_MEMBER"
    ARTICLE = "ARTICLE"
    OBSERVER = "OBSERVER"


class WorkLocation(str, Enum):
    CLIENT_SITE = "CLIENT_SITE"
    OFFICE = "OFFICE"
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"


class AllocationStatus(str, Enum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class BookingType(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    TENTATIVE = "TENTATIVE"


class NonAvailabilityType(str, Enum):
    PRIVILEGE_LEAVE = "PRIVILEGE_LEAVE"
    CASUAL_LEAVE = "CASUAL_LEAVE"
    SICK_LEAVE = "SICK_LEAVE"
    EXAM_LEAVE = "EXAM_LEAVE"
    COMP_OFF = "COMP_OFF"
    TRAINING = "TRAINING"
    CPE = "CPE"
    SECONDMENT = "SECONDMENT"
    MATERNITY = "MATERNITY"
    SABBATICAL = "SABBATICAL"
    HOLIDAY = "HOLIDAY"
    OTHER = "OTHER"


class DayFraction(str, Enum):
    FULL = "FULL"
    FIRST_HALF = "FIRST_HALF"
    SECOND_HALF = "SECOND_HALF"


class NonAvailabilityStatus(str, Enum):
    APPLIED = "APPLIED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class TimesheetStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SkillCategory(str, Enum):
    INDUSTRY = "INDUSTRY"
    TECHNICAL = "TECHNICAL"
    TOOL = "TOOL"
    REGULATORY = "REGULATORY"
    SOFT = "SOFT"


class ResourceRequestStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"


class AuditAction(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    APPROVE = "APPROVE"
    OVERRIDE = "OVERRIDE"
    EXPORT = "EXPORT"
    LOGIN = "LOGIN"


class RuleSeverity(str, Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    INFO = "INFO"
