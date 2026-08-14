"""Ta'lim statistikasi uchun FastAPI'dan mustaqil hisoblash yordamchilari."""

import calendar
from datetime import date


def education_stats_period(period: str, selected_date: str) -> tuple[str, str]:
    """Tanlangan kun uchun inklyuziv davr boshi va oxirini qaytaradi."""
    if period not in {"day", "month", "year"}:
        raise ValueError("Davr turi noto'g'ri.")
    try:
        anchor = date.fromisoformat(str(selected_date or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Statistika sanasi noto'g'ri.") from exc
    if period == "day":
        start = end = anchor
    elif period == "month":
        start = anchor.replace(day=1)
        end = anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
    else:
        start = anchor.replace(month=1, day=1)
        end = anchor.replace(month=12, day=31)
    return start.isoformat(), end.isoformat()


def education_stats_result(
    student_due: int,
    student_paid: int,
    teacher_due: int,
    teacher_paid: int,
    other_expenses: int,
) -> dict:
    """Haqiqiy pul oqimi va hisoblangan natijani bir-biridan ajratadi."""
    other = int(other_expenses or 0)
    return {
        "other_expenses": other,
        "cash_flow": int(student_paid or 0) - int(teacher_paid or 0) - other,
        "accrual_result": int(student_due or 0) - int(teacher_due or 0) - other,
    }


def _months_between(start: str, end: str) -> list[str]:
    first = date.fromisoformat(start).replace(day=1)
    last = date.fromisoformat(end).replace(day=1)
    months = []
    current = first
    while current <= last:
        months.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def _scalar(conn, sql: str, params=()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int((row[0] if row else 0) or 0)


def education_statistics_data(conn, business_id: int, period: str, selected_date: str) -> dict:
    """Mavjud ta'lim jadvallaridan bir biznes uchun real-vaqt jamlanma yaratadi."""
    start, end = education_stats_period(period, selected_date)
    months = _months_between(start, end)
    active_students = _scalar(conn, "SELECT COUNT(*) FROM education_students WHERE business_id=? AND status='active'", (business_id,))
    active_groups = _scalar(conn, "SELECT COUNT(*) FROM education_groups WHERE business_id=? AND status='active'", (business_id,))
    new_enrollments = _scalar(
        conn,
        "SELECT COUNT(*) FROM education_enrollments WHERE business_id=? AND date(created_at,'unixepoch','+5 hours') BETWEEN ? AND ?",
        (business_id, start, end),
    )
    attendance_total = _scalar(
        conn,
        "SELECT COUNT(*) FROM education_attendance WHERE business_id=? AND lesson_date BETWEEN ? AND ?",
        (business_id, start, end),
    )
    attendance_present = _scalar(
        conn,
        "SELECT COUNT(*) FROM education_attendance WHERE business_id=? AND lesson_date BETWEEN ? AND ? AND attendance_status IN ('present','late')",
        (business_id, start, end),
    )
    attendance_percent = int(round(attendance_present * 100 / attendance_total)) if attendance_total else 0

    group_rows = conn.execute(
        """SELECT g.id,g.name,COALESCE(g.billing_type,'monthly') billing_type,
                  COALESCE(g.package_lessons,0) package_lessons,COALESCE(g.package_price,0) package_price
           FROM education_groups g WHERE g.business_id=? AND g.status='active' ORDER BY g.name COLLATE NOCASE,g.id""",
        (business_id,),
    ).fetchall()
    groups = []
    group_map = {}
    for group in group_rows:
        item = {
            "id": int(group["id"]), "name": group["name"],
            "active_students": _scalar(conn, "SELECT COUNT(*) FROM education_students WHERE business_id=? AND group_id=? AND status='active'", (business_id, group["id"])),
            "attendance_percent": 0, "calculated": 0, "paid": 0, "debt": 0,
        }
        total = _scalar(conn, "SELECT COUNT(*) FROM education_attendance WHERE business_id=? AND group_id=? AND lesson_date BETWEEN ? AND ?", (business_id, group["id"], start, end))
        present = _scalar(conn, "SELECT COUNT(*) FROM education_attendance WHERE business_id=? AND group_id=? AND lesson_date BETWEEN ? AND ? AND attendance_status IN ('present','late')", (business_id, group["id"], start, end))
        item["attendance_percent"] = int(round(present * 100 / total)) if total else 0
        groups.append(item)
        group_map[item["id"]] = item

    students = conn.execute(
        """SELECT s.id,s.group_id,COALESCE(s.monthly_fee,0) monthly_fee,COALESCE(s.joined_date,'') joined_date,
                  COALESCE(g.billing_type,'monthly') billing_type,COALESCE(g.package_lessons,0) package_lessons,
                  COALESCE(g.package_price,0) package_price
           FROM education_students s LEFT JOIN education_groups g ON g.id=s.group_id AND g.business_id=s.business_id
           WHERE s.business_id=? AND s.status='active'""",
        (business_id,),
    ).fetchall()
    student_due = 0
    for student in students:
        expected = 0
        if student["billing_type"] == "attendance" and int(student["package_lessons"] or 0) > 0:
            per_month = []
            for month in months:
                month_start = start if period == "day" else month + "-01"
                month_end = end if period == "day" else month + "-" + str(calendar.monthrange(int(month[:4]), int(month[5:7]))[1]).zfill(2)
                lessons = _scalar(
                    conn,
                    """SELECT COUNT(*) FROM education_attendance WHERE business_id=? AND group_id=? AND student_id=?
                       AND lesson_date BETWEEN ? AND ? AND attendance_status IN ('present','late','absent')""",
                    (business_id, student["group_id"], student["id"], month_start, month_end),
                )
                per_month.append(int(round((int(student["package_price"] or 0) / int(student["package_lessons"])) * min(lessons, int(student["package_lessons"])))))
            expected = sum(per_month)
        elif period != "day":
            joined = str(student["joined_date"] or "")[:7]
            eligible_months = [month for month in months if not joined or joined <= month]
            expected = int(student["monthly_fee"] or 0) * len(eligible_months)
        student_due += expected
        if student["group_id"] in group_map:
            group_map[student["group_id"]]["calculated"] += expected

    payment_columns = {row[1] for row in conn.execute("PRAGMA table_info(education_payments)").fetchall()}
    active_payment_sql = " AND COALESCE(p.voided_at,0)=0" if "voided_at" in payment_columns else ""
    payment_rows = conn.execute(
        """SELECT p.amount,s.group_id FROM education_payments p
           JOIN education_students s ON s.id=p.student_id AND s.business_id=p.business_id
           WHERE p.business_id=?""" + active_payment_sql + " AND date(p.created_at,'unixepoch','+5 hours') BETWEEN ? AND ?",
        (business_id, start, end),
    ).fetchall()
    student_paid = sum(int(row["amount"] or 0) for row in payment_rows)
    for row in payment_rows:
        if row["group_id"] in group_map:
            group_map[row["group_id"]]["paid"] += int(row["amount"] or 0)
    for group in groups:
        group["debt"] = max(0, group["calculated"] - group["paid"])

    teachers = conn.execute(
        "SELECT id,salary_type,COALESCE(salary_amount,0) salary_amount,COALESCE(hired_date,'') hired_date FROM education_teachers WHERE business_id=? AND status='active'",
        (business_id,),
    ).fetchall()
    teacher_due = 0
    for teacher in teachers:
        if teacher["salary_type"] == "monthly" and period != "day":
            hired = str(teacher["hired_date"] or "")[:7]
            teacher_due += int(teacher["salary_amount"] or 0) * len([month for month in months if not hired or hired <= month])
        elif teacher["salary_type"] == "per_lesson":
            lessons = _scalar(
                conn,
                """SELECT COUNT(DISTINCT CAST(a.group_id AS TEXT)||':'||a.lesson_date)
                   FROM education_attendance a JOIN education_groups g ON g.id=a.group_id AND g.business_id=a.business_id
                   WHERE a.business_id=? AND g.teacher_id=? AND a.lesson_date BETWEEN ? AND ?""",
                (business_id, teacher["id"], start, end),
            )
            teacher_due += lessons * int(teacher["salary_amount"] or 0)
    teacher_paid = _scalar(
        conn,
        "SELECT COALESCE(SUM(amount),0) FROM education_teacher_payments WHERE business_id=? AND date(created_at,'unixepoch','+5 hours') BETWEEN ? AND ?",
        (business_id, start, end),
    )
    other_expenses = _scalar(
        conn,
        """SELECT COALESCE(SUM(amount),0) FROM expenses WHERE business_id=?
           AND date(created_at,'unixepoch','+5 hours') BETWEEN ? AND ? AND COALESCE(source,'manual')<>'education_salary'""",
        (business_id, start, end),
    )
    return {
        "period": {"type": period, "date": selected_date, "start": start, "end": end},
        "education": {"active_students": active_students, "active_groups": active_groups, "new_enrollments": new_enrollments, "attendance_percent": attendance_percent},
        "student_finance": {"calculated": student_due, "paid": student_paid, "debt": max(0, student_due - student_paid)},
        "teacher_finance": {"calculated": teacher_due, "paid": teacher_paid, "debt": max(0, teacher_due - teacher_paid)},
        "result": education_stats_result(student_due, student_paid, teacher_due, teacher_paid, other_expenses),
        "groups": groups,
    }
