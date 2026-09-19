"""Acceptance tests T1, T2, T3, T4, T5, T6, T7 (§14) against the conflict engine,
plus unit tests for R10-R24 (Phase P8)."""
import uuid

from app.models.allocation import Allocation, IndependenceDeclaration, NonAvailability
from app.models.enums import (
    AllocationRole,
    AllocationStatus,
    Designation,
    StaffCategory,
)
from app.models.reference import Skill, StaffSkill
from app.services.conflict_engine import (
    AllocationCandidate,
    check_article_hours_breach,
    check_budget_overrun,
    check_cooling_off,
    check_deadline_risk,
    check_duplicate_role,
    check_eqcr_missing,
    check_exiting_staff,
    check_grade_mix_breach,
    check_icai_training_limit,
    check_location_mismatch,
    check_no_exposure_diversity,
    check_outstation_breach,
    check_skill_gap,
    check_sustained_overload,
    check_unapproved_pipeline,
    validate_allocation,
)
from tests.factories import make_client, make_client_group, make_department, make_engagement, make_office, make_staff


def _confirmed_allocation(session, engagement, staff, date_from, date_to, pct=100, role=AllocationRole.TEAM_MEMBER):
    row = Allocation(
        engagement_id=engagement.id,
        staff_id=staff.id,
        role_on_engagement=role,
        date_from=date_from,
        date_to=date_to,
        allocation_pct=pct,
        status=AllocationStatus.CONFIRMED,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _setup_engagement(session):
    dept = make_department(session)
    client = make_client(session)
    engagement = make_engagement(session, client.id, dept.id)
    return dept, client, engagement


def test_t1_overallocation_blocked(session):
    _, _, engagement = _setup_engagement(session)
    staff = make_staff(session)
    _confirmed_allocation(session, engagement, staff, "2026-09-01", "2026-09-30", pct=100)

    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-10", date_to="2026-09-15", allocation_pct=100,
    )
    violations = validate_allocation(session, cand)
    codes = {v.code for v in violations}
    assert "OVERALLOCATION" in codes
    assert next(v for v in violations if v.code == "OVERALLOCATION").severity == "BLOCK"


def test_t2_two_50pct_ok_third_25pct_blocked(session):
    _, _, engagement = _setup_engagement(session)
    staff = make_staff(session)
    _confirmed_allocation(session, engagement, staff, "2026-09-01", "2026-09-30", pct=50)

    # second 50% booking overlapping same dates -> should be allowed (total 100%)
    cand2 = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-30", allocation_pct=50,
    )
    violations2 = validate_allocation(session, cand2)
    assert not any(v.code == "OVERALLOCATION" for v in violations2)
    _confirmed_allocation(session, engagement, staff, "2026-09-01", "2026-09-30", pct=50)

    # third 25% booking overlapping -> total would be 125% -> blocked
    cand3 = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-05", date_to="2026-09-10", allocation_pct=25,
    )
    violations3 = validate_allocation(session, cand3)
    assert any(v.code == "OVERALLOCATION" and v.severity == "BLOCK" for v in violations3)


def test_t3_leave_conflict_names_the_record(session):
    _, _, engagement = _setup_engagement(session)
    staff = make_staff(session)
    leave = NonAvailability(
        staff_id=staff.id, type="PRIVILEGE_LEAVE", date_from="2026-09-10", date_to="2026-09-12",
        status="APPROVED",
    )
    session.add(leave)
    session.commit()
    session.refresh(leave)

    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-11", date_to="2026-09-14", allocation_pct=100,
    )
    violations = validate_allocation(session, cand)
    leave_violation = next(v for v in violations if v.code == "LEAVE_CONFLICT")
    assert leave_violation.severity == "BLOCK"
    assert leave_violation.context["non_availability_id"] == str(leave.id)


def test_t4_article_blocked_during_exam_leave(session):
    _, _, engagement = _setup_engagement(session)
    article = make_staff(
        session,
        staff_category=StaffCategory.ARTICLED_ASSISTANT,
        designation=Designation.ARTICLE_Y2,
        grade_rank=11,
        exam_leave_blocks=[{"from": "2026-05-01", "to": "2026-05-20", "exam": "CA Final May 26"}],
    )
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=article.id, role_on_engagement=AllocationRole.ARTICLE,
        date_from="2026-05-10", date_to="2026-05-15", allocation_pct=100,
    )
    violations = validate_allocation(session, cand)
    assert any(v.code == "EXAM_LEAVE" and v.severity == "BLOCK" for v in violations)


def test_t5_eqcr_partner_cannot_also_be_engagement_partner(session):
    _, _, engagement = _setup_engagement(session)
    partner = make_staff(
        session, staff_category=StaffCategory.PARTNER, designation=Designation.PARTNER, grade_rank=2,
        icai_membership_no="123456",
    )
    engagement.eqcr_partner_id = partner.id
    session.add(engagement)
    session.commit()

    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=partner.id, role_on_engagement=AllocationRole.ENGAGEMENT_PARTNER,
        date_from="2026-09-01", date_to="2026-09-30", allocation_pct=100,
    )
    violations = validate_allocation(session, cand)
    assert any(v.code == "EQCR_INDEPENDENCE" and v.severity == "BLOCK" for v in violations)


def test_t6_no_qualified_supervisor_for_articles(session):
    _, _, engagement = _setup_engagement(session)
    # Two other articles already on the engagement, no AM+ supervisor.
    other_article = make_staff(session, staff_category=StaffCategory.ARTICLED_ASSISTANT, designation=Designation.ARTICLE_Y1, grade_rank=12)
    _confirmed_allocation(session, engagement, other_article, "2026-09-01", "2026-09-30", role=AllocationRole.ARTICLE)

    article = make_staff(session, staff_category=StaffCategory.ARTICLED_ASSISTANT, designation=Designation.ARTICLE_Y2, grade_rank=11)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=article.id, role_on_engagement=AllocationRole.ARTICLE,
        date_from="2026-09-05", date_to="2026-09-10", allocation_pct=100,
    )
    violations = validate_allocation(session, cand)
    assert any(v.code == "NO_QUALIFIED_SUPERVISOR" and v.severity == "BLOCK" for v in violations)

    # Now add a qualified supervisor (Assistant Manager, grade_rank=6) overlapping -> rule clears.
    supervisor = make_staff(session, staff_category=StaffCategory.EMPLOYEE_CA, designation=Designation.ASSISTANT_MANAGER, grade_rank=6)
    _confirmed_allocation(session, engagement, supervisor, "2026-09-01", "2026-09-30", role=AllocationRole.FIELD_INCHARGE)
    violations2 = validate_allocation(session, cand)
    assert not any(v.code == "NO_QUALIFIED_SUPERVISOR" for v in violations2)


def test_t7_independence_conflict_blocks_across_client_group(session):
    group = make_client_group(session)
    client_a = make_client(session, group_id=group.id)
    client_b = make_client(session, group_id=group.id)
    dept = make_department(session)
    engagement_b = make_engagement(session, client_b.id, dept.id)

    staff = make_staff(session)
    declaration = IndependenceDeclaration(
        staff_id=staff.id, client_id=client_a.id, declaration_fy="FY2026-27", is_conflicted=True,
    )
    session.add(declaration)
    session.commit()

    cand = AllocationCandidate(
        engagement_id=engagement_b.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-10", allocation_pct=100,
    )
    violations = validate_allocation(session, cand)
    assert any(v.code == "INDEPENDENCE_CONFLICT" and v.severity == "BLOCK" for v in violations)


# ---------------------------------------------------------------------------
# R10-R24 (Phase P8)
# ---------------------------------------------------------------------------


def test_r10_eqcr_missing_warns_until_assigned(session):
    _, _, engagement = _setup_engagement(session)
    engagement.eqcr_required = True
    session.add(engagement)
    session.commit()
    staff = make_staff(session)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-10",
    )
    v = check_eqcr_missing(cand, engagement)
    assert v is not None and v.code == "EQCR_MISSING" and v.severity == "WARN"

    engagement.eqcr_partner_id = staff.id
    session.add(engagement)
    session.commit()
    assert check_eqcr_missing(cand, engagement) is None


def test_r11_skill_gap_warns_when_staff_lacks_required_skill(session):
    _, _, engagement = _setup_engagement(session)
    skill = Skill(code="FS-VAL", name="Financial Valuation", category="TECHNICAL")
    session.add(skill)
    session.commit()
    session.refresh(skill)
    engagement.requires_specialist_skills = [skill.code]
    session.add(engagement)
    session.commit()

    staff = make_staff(session)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.SPECIALIST,
        date_from="2026-09-01", date_to="2026-09-10",
    )
    v = check_skill_gap(session, cand, engagement)
    assert v is not None and v.code == "SKILL_GAP" and "FS-VAL" in v.context["missing_skills"]

    session.add(StaffSkill(staff_id=staff.id, skill_id=skill.id, proficiency=4))
    session.commit()
    assert check_skill_gap(session, cand, engagement) is None


def test_r12_grade_mix_breach_when_no_qualified_staff(session):
    _, _, engagement = _setup_engagement(session)
    article = make_staff(session, staff_category=StaffCategory.ARTICLED_ASSISTANT, designation=Designation.ARTICLE_Y1, grade_rank=12)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=article.id, role_on_engagement=AllocationRole.ARTICLE,
        date_from="2026-09-01", date_to="2026-09-10",
    )
    v = check_grade_mix_breach(session, cand, article)
    assert v is not None and v.code == "GRADE_MIX_BREACH"

    qualified = make_staff(session, staff_category=StaffCategory.EMPLOYEE_CA, designation=Designation.SENIOR_ASSOCIATE, grade_rank=7)
    _confirmed_allocation(session, engagement, qualified, "2026-09-01", "2026-09-30", role=AllocationRole.TEAM_MEMBER)
    assert check_grade_mix_breach(session, cand, article) is None


def test_r13_icai_training_limit_aggregate_months(session):
    article = make_staff(session, staff_category=StaffCategory.ARTICLED_ASSISTANT, designation=Designation.ARTICLE_Y2, grade_rank=11)
    session.add(NonAvailability(staff_id=article.id, type="SECONDMENT", date_from="2025-01-01", date_to="2025-12-31", status="APPROVED"))
    session.commit()
    cand = AllocationCandidate(
        engagement_id=uuid.uuid4(), staff_id=article.id, role_on_engagement=AllocationRole.ARTICLE,
        date_from="2026-01-01", date_to="2026-01-05",
    )
    v = check_icai_training_limit(session, cand, article)
    assert v is not None and v.code == "ICAI_TRAINING_LIMIT"


def test_r13_icai_training_limit_principal_cap(session):
    principal = make_staff(session, staff_category=StaffCategory.PARTNER, designation=Designation.PARTNER, grade_rank=2)

    def _seconded_article():
        return make_staff(
            session, staff_category=StaffCategory.ARTICLED_ASSISTANT, designation=Designation.ARTICLE_Y2, grade_rank=11,
            articleship_principal_id=principal.id, secondment_flag=True,
        )

    _seconded_article()
    _seconded_article()
    sib3 = _seconded_article()
    session.add(NonAvailability(staff_id=sib3.id, type="SECONDMENT", date_from="2026-01-01", date_to="2026-01-30", status="APPROVED"))
    session.commit()

    cand = AllocationCandidate(
        engagement_id=uuid.uuid4(), staff_id=sib3.id, role_on_engagement=AllocationRole.ARTICLE,
        date_from="2026-02-01", date_to="2026-02-05",
    )
    v = check_icai_training_limit(session, cand, sib3)
    assert v is not None and v.code == "ICAI_TRAINING_LIMIT"


def test_r14_article_hours_breach(session):
    _, _, engagement = _setup_engagement(session)
    article = make_staff(session, staff_category=StaffCategory.ARTICLED_ASSISTANT, designation=Designation.ARTICLE_Y1, grade_rank=12)
    _confirmed_allocation(session, engagement, article, "2026-09-07", "2026-09-11", pct=100, role=AllocationRole.ARTICLE)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=article.id, role_on_engagement=AllocationRole.ARTICLE,
        date_from="2026-09-07", date_to="2026-09-11", allocation_pct=20,
    )
    v = check_article_hours_breach(session, cand, article)
    assert v is not None and v.code == "ARTICLE_HOURS_BREACH"


def test_r15_sustained_overload_flags_long_full_booking(session):
    _, _, engagement = _setup_engagement(session)
    staff = make_staff(session)
    _confirmed_allocation(session, engagement, staff, "2026-08-01", "2026-10-15", pct=100)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-07", date_to="2026-09-11", allocation_pct=10,
    )
    v = check_sustained_overload(session, cand)
    assert v is not None and v.code == "SUSTAINED_OVERLOAD"
    assert v.context["consecutive_weeks"] >= 6


def test_r16_outstation_breach(session):
    office_a = make_office(session)
    office_b = make_office(session)
    staff = make_staff(session, base_office_id=office_a.id, max_outstation_days_per_month=5)
    cand = AllocationCandidate(
        engagement_id=uuid.uuid4(), staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-10", office_id=office_b.id,
    )
    v = check_outstation_breach(session, cand, staff)
    assert v is not None and v.code == "OUTSTATION_BREACH"


def test_r17_location_mismatch_is_informational(session):
    office_a = make_office(session)
    office_b = make_office(session)
    staff = make_staff(session, base_office_id=office_a.id)
    cand = AllocationCandidate(
        engagement_id=uuid.uuid4(), staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-05", office_id=office_b.id,
    )
    v = check_location_mismatch(cand, staff)
    assert v is not None and v.code == "LOCATION_MISMATCH" and v.severity == "INFO"

    cand_same_office = AllocationCandidate(
        engagement_id=cand.engagement_id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-05", office_id=office_a.id,
    )
    assert check_location_mismatch(cand_same_office, staff) is None


def test_r18_budget_overrun(session):
    _, _, engagement = _setup_engagement(session)
    engagement.fee_amount = 10000
    session.add(engagement)
    session.commit()
    staff = make_staff(session, cost_rate_per_hour=2000)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-05", allocation_pct=100,
    )
    v = check_budget_overrun(session, cand, engagement, staff)
    assert v is not None and v.code == "BUDGET_OVERRUN"


def test_r19_deadline_risk(session):
    _, _, engagement = _setup_engagement(session)
    engagement.reporting_deadline = "2026-09-10"
    session.add(engagement)
    session.commit()
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=uuid.uuid4(), role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-05", date_to="2026-09-15",
    )
    v = check_deadline_risk(cand, engagement)
    assert v is not None and v.code == "DEADLINE_RISK"


def test_r20_no_exposure_diversity(session):
    dept = make_department(session)
    client = make_client(session)
    engagement1 = make_engagement(session, client.id, dept.id)
    engagement2 = make_engagement(session, client.id, dept.id)
    article = make_staff(session, staff_category=StaffCategory.ARTICLED_ASSISTANT, designation=Designation.ARTICLE_Y2, grade_rank=11)
    _confirmed_allocation(session, engagement1, article, "2026-01-01", "2026-06-30", role=AllocationRole.ARTICLE)
    cand = AllocationCandidate(
        engagement_id=engagement2.id, staff_id=article.id, role_on_engagement=AllocationRole.ARTICLE,
        date_from="2026-07-01", date_to="2026-07-10",
    )
    v = check_no_exposure_diversity(session, cand, article, engagement2)
    assert v is not None and v.code == "NO_EXPOSURE_DIVERSITY" and v.severity == "INFO"


def test_r21_exiting_staff_near_notice_end(session):
    staff = make_staff(session, notice_period_end="2026-09-30")
    cand = AllocationCandidate(
        engagement_id=uuid.uuid4(), staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-25", date_to="2026-09-28",
    )
    v = check_exiting_staff(cand, staff)
    assert v is not None and v.code == "EXITING_STAFF"

    cand_early = AllocationCandidate(
        engagement_id=uuid.uuid4(), staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-05", date_to="2026-09-06",
    )
    assert check_exiting_staff(cand_early, staff) is None


def test_r22_unapproved_pipeline_when_still_pipeline_status(session):
    dept = make_department(session)
    client = make_client(session, acceptance_status="ACCEPTED")
    engagement = make_engagement(session, client.id, dept.id, status="PIPELINE")
    v = check_unapproved_pipeline(engagement, client)
    assert v is not None and v.code == "UNAPPROVED_PIPELINE" and v.severity == "INFO"

    engagement.status = "FIELDWORK"
    assert check_unapproved_pipeline(engagement, client) is None


def test_r23_duplicate_role_blocks_second_engagement_partner(session):
    _, _, engagement = _setup_engagement(session)
    partner1 = make_staff(
        session, staff_category=StaffCategory.PARTNER, designation=Designation.PARTNER, grade_rank=2, icai_membership_no="1",
    )
    _confirmed_allocation(session, engagement, partner1, "2026-09-01", "2026-09-30", role=AllocationRole.ENGAGEMENT_PARTNER)

    partner2 = make_staff(
        session, staff_category=StaffCategory.PARTNER, designation=Designation.PARTNER, grade_rank=2, icai_membership_no="2",
    )
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=partner2.id, role_on_engagement=AllocationRole.ENGAGEMENT_PARTNER,
        date_from="2026-09-10", date_to="2026-09-15",
    )
    v = check_duplicate_role(session, cand)
    assert v is not None and v.code == "DUPLICATE_ROLE" and v.severity == "BLOCK"


def test_r24_cooling_off_warns_on_prior_employment_declaration(session):
    dept = make_department(session)
    client = make_client(session)
    engagement = make_engagement(session, client.id, dept.id)
    staff = make_staff(session)
    session.add(IndependenceDeclaration(staff_id=staff.id, client_id=client.id, declaration_fy="FY2026-27", held_employment_last_2yrs=True))
    session.commit()

    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-05",
    )
    v = check_cooling_off(session, cand, engagement)
    assert v is not None and v.code == "COOLING_OFF" and v.severity == "WARN"


def test_p8_new_rules_wired_into_validate_allocation(session):
    dept = make_department(session)
    client = make_client(session)
    engagement = make_engagement(session, client.id, dept.id)
    engagement.eqcr_required = True
    session.add(engagement)
    session.commit()

    office_a = make_office(session)
    office_b = make_office(session)
    staff = make_staff(session, base_office_id=office_a.id)
    cand = AllocationCandidate(
        engagement_id=engagement.id, staff_id=staff.id, role_on_engagement=AllocationRole.TEAM_MEMBER,
        date_from="2026-09-01", date_to="2026-09-05", office_id=office_b.id,
    )
    violations = validate_allocation(session, cand)
    codes = {v.code for v in violations}
    assert "EQCR_MISSING" in codes
    assert "LOCATION_MISMATCH" in codes
